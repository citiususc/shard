"""Generate one ontology-grounded SHACL shape from a business rule."""

import re
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from rdflib import Graph

from shard.domain.ontology import (
    ontology_base_namespace,
    ontology_prefix_block,
    ontology_shape_prefix,
    ontology_shapes_namespace,
    parse_ontology_graph,
)
from shard.application.shape_validation import (
    allowed_ontology_terms_text,
    ontology_grounding_catalog,
    validation_profiles_from_payload,
    validate_shape_content,
    validate_shape_grounding,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

MAX_RETRIES = 10

_PREFIX_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
_PREFIX_DECLARATION_RE = re.compile(
    r"(?:@prefix|PREFIX)\s+([^:\s]*):\s*<([^>]+)>\s*\.?",
    re.IGNORECASE,
)

def _prefix_bindings(prefixes):
    return {
        match.group(1): match.group(2)
        for match in _PREFIX_DECLARATION_RE.finditer(prefixes or "")
    }

def _ensure_shape_prefix(prefixes, shape_ns, requested_prefix=""):
    """Return a validated preferred shape prefix and a matching prefix block."""
    bindings = _prefix_bindings(prefixes)
    prefix = str(requested_prefix or "").strip()
    if not prefix and shape_ns:
        candidates = [name for name, namespace in bindings.items() if name and namespace == shape_ns]
        candidates.sort(key=lambda name: (
            name != "shape", not name.endswith("-sh"), "shape" not in name, len(name), name,
        ))
        prefix = candidates[0] if candidates else "shape"
    prefix = prefix or "shape"
    if not _PREFIX_NAME_RE.fullmatch(prefix):
        raise ValueError(
            "shape_prefix must start with a letter and contain only letters, digits, '.', '_' or '-'."
        )
    existing = bindings.get(prefix)
    if existing and shape_ns and existing != shape_ns:
        raise ValueError(
            f"Shape prefix '{prefix}' is already bound to {existing}, not {shape_ns}."
        )
    if shape_ns and not existing:
        current = (prefixes or "").rstrip()
        declaration = f"@prefix {prefix}: <{shape_ns}> ."
        prefixes = f"{current}\n{declaration}\n" if current else f"{declaration}\n"
    return prefix, prefixes

_TARGET_ROLE_KEYS = ("focus_nodes", "constraint_paths", "related_terms")


def _term_key(term):
    return str(term.get("full_iri") or term.get("iri") or term.get("id") or "")


def _normalise_target_roles(payload, ontology_content, ontology_filename, target):
    """Return role-grouped ontology terms while accepting the legacy target."""
    raw_roles = payload.get("target_roles") or {}
    ontology_terms = payload.get("_ontology_terms") or []
    if ontology_content and not ontology_terms:
        from shard.application.ontology_catalog import parse_ontology

        ontology_terms = parse_ontology(ontology_filename, ontology_content).get("entities") or []

    lookup = {}
    for term in ontology_terms:
        for value in (
            term.get("id"), term.get("iri"), term.get("full_iri"), term.get("label"),
        ):
            if value:
                lookup[str(value)] = term

    roles = {key: [] for key in _TARGET_ROLE_KEYS}
    seen = set()
    for role in _TARGET_ROLE_KEYS:
        for value in raw_roles.get(role) or []:
            term = value if isinstance(value, dict) else lookup.get(str(value))
            if term is None:
                text = str(value or "").strip()
                term = {"iri": text, "full_iri": text} if text else None
            key = _term_key(term or {})
            if term and key and key not in seen:
                roles[role].append(dict(term))
                seen.add(key)

    if not any(roles.values()) and target:
        role = "constraint_paths" if target.get("type") == "property" else "focus_nodes"
        roles[role].append(dict(target))
    return roles


def _target_terms(target_roles):
    return [term for role in _TARGET_ROLE_KEYS for term in target_roles.get(role, [])]


def _primary_target(target_roles, fallback=None):
    for role in ("focus_nodes", "constraint_paths", "related_terms"):
        if target_roles.get(role):
            return target_roles[role][0]
    return fallback or {}


def _target_context_text(target_roles):
    labels = {
        "focus_nodes": "Focus nodes/classes",
        "constraint_paths": "Constrained property paths",
        "related_terms": "Related ontology terms",
    }
    lines = []
    for role in _TARGET_ROLE_KEYS:
        terms = target_roles.get(role) or []
        values = [str(term.get("full_iri") or term.get("iri") or "") for term in terms]
        lines.append(f"{labels[role]}: {', '.join(value for value in values if value) or '(none)'}")
    return "\n".join(lines)


def _build_ontology_info(
    ontology_content,
    target,
    target_roles=None,
    ontology_filename="ontology.ttl",
):
    """Build ontology context for every term participating in one rule."""
    from shard.application.generation_support import get_info_by_name, get_property_domain

    target_roles = target_roles or {}
    terms = _target_terms(target_roles) or [target]
    if not ontology_content:
        sections = [_target_context_text(target_roles)]
        sections.extend(
            f"# {term.get('iri')}\n{term.get('ontologyNote', '')}" for term in terms
        )
        return "\n\n".join(section for section in sections if section.strip())

    g = parse_ontology_graph(ontology_content, ontology_filename)
    sections = ["# Resolved ontology roles\n" + _target_context_text(target_roles)]
    documented = set()
    for term in terms:
        term_iri = term.get("full_iri") or term.get("iri")
        if not term_iri or term_iri in documented:
            continue
        documented.add(term_iri)
        term_info = get_info_by_name(g, term_iri)
        sections.append(f"# {term_iri}\n{term_info or term.get('ontologyNote', '')}")

        if term.get("type") == "property":
            domain = get_property_domain(g, term_iri)
            if domain:
                sections.append(f"Domain of {term_iri}: {domain}")
                for owl_class in domain:
                    if owl_class in documented:
                        continue
                    documented.add(owl_class)
                    class_info = get_info_by_name(g, owl_class)
                    if class_info:
                        sections.append(f"## {owl_class}\n{class_info}")
    return "\n\n".join(section.strip() for section in sections if section.strip())

def _hints_from_shape(shape_str, prefixes):
    """Derive constraint hints by listing sh:* predicates present in the shape."""
    hints = []
    try:
        g = Graph(bind_namespaces="none")
        g.parse(data=f"{prefixes}\n{shape_str}", format="turtle")
        SH = "http://www.w3.org/ns/shacl#"
        for s, p, o in g:
            if str(p).startswith(SH):
                local = str(p).rsplit("#", 1)[-1]
                if local in {"path", "targetClass", "property"}:
                    continue
                try:
                    oq = g.qname(o)
                except Exception:
                    oq = str(o)
                hints.append({"reason": f"constraint sh:{local}", "constraint": f"sh:{local} {oq}"})
    except Exception:
        pass
    # de-duplicate, cap
    seen, out = set(), []
    for h in hints:
        if h["constraint"] not in seen:
            seen.add(h["constraint"])
            out.append(h)
    return out[:12]

def build_shape(payload):
    from shard.application.generation_support import clean_shacl_response
    from shard.baselines import baseline_context_for_targets
    from shard.inference import DEFAULT_GEN_MAX_NEW_TOKENS, get_chat_llm
    from shard.observability import logger
    from shard.prompting import load_prompt_from_json

    legacy_target = payload.get("target") or {}
    rule = payload.get("business_rule", "")
    domain_context = (payload.get("domain_context") or "").strip() or "(none provided)"
    generation_guidance = (payload.get("generation_guidance") or "").strip() or "(none provided)"
    prefixes = payload.get("prefixes") or ""
    ontology_content = payload.get("ontology_content", "")
    ontology_filename = payload.get(
        "ontology_filename",
        legacy_target.get("ontology_filename", "ontology.ttl"),
    )
    target_roles = _normalise_target_roles(
        payload,
        ontology_content,
        ontology_filename,
        legacy_target,
    )
    target = _primary_target(target_roles, legacy_target)
    all_targets = _target_terms(target_roles)
    temperature = float(payload.get("temperature", 0.5))
    model_id = payload.get("model") or "system.ai.gemma-3-12b"

    base_ns = payload.get("base_namespace") or ""
    shape_ns = payload.get("shape_namespace") or ""
    shape_prefix = str(payload.get("shape_prefix") or "").strip()
    ontology_graph = None
    if ontology_content and (not base_ns or not shape_ns or not shape_prefix or not prefixes):
        ontology_graph = parse_ontology_graph(
            ontology_content,
            ontology_filename,
        )
        if not base_ns:
            base_ns = ontology_base_namespace(ontology_graph)
        if not shape_ns:
            shape_ns, _ = ontology_shapes_namespace(ontology_graph, base_ns)
        if not shape_prefix:
            shape_prefix, _ = ontology_shape_prefix(ontology_graph, shape_ns)
        if not prefixes:
            prefixes = ontology_prefix_block(
                ontology_graph,
                base_ns,
                shape_ns or None,
                shape_prefix or None,
            )

    try:
        shape_prefix, prefixes = _ensure_shape_prefix(prefixes, shape_ns, shape_prefix)
    except ValueError as exc:
        return {
            "shape": "", "valid": False, "error": str(exc), "attempts": 0,
            "hints": [], "fallback": False, "error_type": "config",
            "message": f"Invalid generated-shape prefix configuration: {exc}",
        }

    logger.info(
        f"[build] rule context focus={len(target_roles['focus_nodes'])} "
        f"paths={len(target_roles['constraint_paths'])} "
        f"related={len(target_roles['related_terms'])} model={model_id}"
    )
    try:
        astrea_shapes = baseline_context_for_targets(payload, all_targets or [target])
    except ValueError as exc:
        return {
            "shape": "", "valid": False, "error": str(exc), "attempts": 0,
            "hints": [], "fallback": False, "error_type": "config",
            "message": f"Invalid Astrea baseline shapes: {exc}",
        }
    if astrea_shapes:
        logger.info(
            f"[build] using rule-focused Astrea baseline evidence "
            f"({len(astrea_shapes)} chars)."
        )
    else:
        logger.info("[build] no Astrea baseline evidence matched the resolved terms.")
    ontology_info = _build_ontology_info(
        ontology_content,
        target,
        target_roles,
        ontology_filename,
    )
    grounding_catalog = None
    if ontology_content:
        grounding_catalog = ontology_grounding_catalog(
            ontology_content,
            ontology_filename,
            target,
            target_roles,
        )
        ontology_info = "\n\n".join([
            ontology_info.strip(),
            "# Allowed ontology IRIs for generated shapes",
            allowed_ontology_terms_text(grounding_catalog),
        ]).strip()

    prompt_file = PACKAGE_ROOT / "resources" / "prompts" / "rule_general.json"

    gen_model = get_chat_llm(model_id, kind="generator", temperature=temperature,
                             max_new_tokens=DEFAULT_GEN_MAX_NEW_TOKENS)

    attempt = 0
    error_message = None
    error_type = "parse"
    last_result = ""

    while attempt < MAX_RETRIES:
        key = "generator_with_error" if error_message else "generator"
        logger.debug(f"[build] attempt {attempt + 1}/{MAX_RETRIES} using prompt '{key}'")
        prompt = load_prompt_from_json(prompt_file, key)
        chain = prompt | gen_model | StrOutputParser()

        invoke_vars = {
            "target_context": _target_context_text(target_roles),
            "prefixes": prefixes,
            "shape_prefix": shape_prefix,
            "ontology_info": ontology_info,
            "domain_context": domain_context,
            "rule": rule,
            "generation_guidance": generation_guidance,
            "astrea_shapes": astrea_shapes or "(none matched for this rule)",
            "shacl_history": "(none)",
        }
        if error_message:
            invoke_vars["previous_invalid_shapes"] = last_result
            invoke_vars["error"] = error_message

        try:
            result = chain.invoke(invoke_vars)
        except Exception as e:
            # Backend/credentials/endpoint error — NOT a Turtle parse error.
            logger.error(f"[build] backend error: {e}")
            return {"shape": "", "valid": False, "error": str(e), "attempts": attempt,
                    "hints": [], "fallback": False, "error_type": "backend",
                    "message": f"Backend error calling the model: {e}"}
        last_result = clean_shacl_response(result)

        if "SHACL shapes not found" in result:
            logger.info("[build] model reported: SHACL shapes not found")
            return {"shape": "", "valid": False, "error": None, "attempts": attempt + 1,
                    "hints": [], "fallback": False, "not_found": True,
                    "message": "The model reported no shape can be justified from this rule and context."}

        validation = validate_shape_content(last_result, prefixes, [])
        if not validation.get("syntax_valid"):
            e = validation.get("error")
            logger.warn(f"[build] parse failed on attempt {attempt + 1}: {e}")
            error_message = str(e)
            error_type = "parse"
            attempt += 1
            continue

        grounding = validate_shape_grounding(
            last_result,
            prefixes,
            ontology_content,
            ontology_filename,
            target,
            grounding_catalog,
            target_roles,
        )
        if not grounding.get("valid"):
            e = grounding.get("error")
            logger.warn(f"[build] grounding failed on attempt {attempt + 1}: {e}")
            error_message = str(e)
            error_type = "grounding"
            attempt += 1
            continue

        validation = validate_shape_content(
            last_result,
            prefixes,
            validation_profiles_from_payload(payload),
        )
        if not validation.get("valid"):
            logger.info(f"[build] generated shape failed validation profile on attempt {attempt + 1}")
            hints = _hints_from_shape(last_result, prefixes)
            return {"shape": last_result, "attempts": attempt + 1, "hints": hints,
                    "fallback": False, **validation}

        logger.info(f"[build] valid SHACL on attempt {attempt + 1}")
        hints = _hints_from_shape(last_result, prefixes)
        return {"shape": last_result, "valid": True, "error": None, "attempts": attempt + 1,
                "hints": hints, "fallback": False, "error_type": "none",
                "astrea_evidence_active": bool(astrea_shapes),
                "target_roles": target_roles,
                "syntax_valid": True,
                "profile_valid": validation.get("profile_valid"),
                "profile_count": validation.get("profile_count", 0),
                "profile_names": validation.get("profile_names", []),
                "generic_profile_active": validation.get("generic_profile_active", False),
                "generic_profile_name": validation.get("generic_profile_name"),
                "domain_profile_count": validation.get("domain_profile_count", 0),
                "domain_profile_names": validation.get("domain_profile_names", []),
                "validation_level": validation.get("validation_level"),
                "message": validation.get("message") if validation.get("profile_count") else f"Valid SHACL generated by '{model_id}' (attempt {attempt + 1})."}

    # Retries exhausted: return the invalid shape with the parse error.
    logger.error(f"[build] exhausted {MAX_RETRIES} attempts; last {error_type} error: {error_message}")
    return {"shape": last_result, "valid": False, "error": error_message, "attempts": MAX_RETRIES,
            "hints": [], "fallback": False, "error_type": error_type,
            "message": f"Reached {MAX_RETRIES} attempts; returning last output with its {error_type} error."}
