"""Generate and audit one ontology-grounded SHACL document per data constraint."""

import json
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


def _is_timeout_error(error):
    """Recognize provider timeout families without coupling to one SDK."""
    current = error
    while current is not None:
        if isinstance(current, TimeoutError) or "timeout" in type(current).__name__.lower():
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

MAX_RETRIES = 10
MAX_CRITIC_FORMAT_RETRIES = 2

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
            if isinstance(value, dict):
                reference = next(
                    (
                        str(value.get(key))
                        for key in ("full_iri", "iri", "id", "label")
                        if value.get(key)
                    ),
                    "",
                )
                catalog_term = lookup.get(reference)
                term = {**catalog_term, **value} if catalog_term else value
            else:
                term = lookup.get(str(value))
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
        "focus_nodes": "Focus nodes/classes (authorized sh:targetClass candidates)",
        "constraint_paths": "Constrained property paths (AUTHORITATIVE sh:path allowlist)",
        "related_terms": "Related ontology terms (context only; not additional targets or paths)",
    }

    def field_text(value):
        values = value if isinstance(value, list) else [value]
        rendered = []
        for item in values:
            if isinstance(item, dict):
                text = item.get("full_iri") or item.get("iri") or item.get("label")
            else:
                text = item
            if text and str(text) not in {"—", "blank node"}:
                rendered.append(str(text))
        return ", ".join(rendered) or "(not specified)"

    def term_line(term, role):
        iri = term.get("iri") or term.get("full_iri") or "(missing IRI)"
        details = [f"label={term.get('label') or '(none)'}"]
        full_iri = term.get("full_iri")
        if full_iri and full_iri != iri:
            details.append(f"full_iri={full_iri}")
        if term.get("type"):
            details.append(f"type={term['type']}")
        if term.get("kind"):
            details.append(f"kind={term['kind']}")
        if term.get("domain"):
            details.append(f"domain={field_text(term['domain'])}")
        range_text = field_text(term.get("range")) if term.get("range") else ""
        if range_text:
            details.append(f"range={range_text}")
        if role == "constraint_paths" and range_text:
            kind = str(term.get("kind") or "").lower()
            if "objectproperty" in kind:
                details.append(f"value-shape evidence=sh:class {range_text}")
            elif "datatypeproperty" in kind:
                details.append(f"datatype evidence={range_text}")
        return f"- {iri} | " + " | ".join(details)

    lines = [
        "Role contract:",
        "- Every sh:path MUST be selected from the constrained property path allowlist below.",
        "- Never replace an authorized path with a semantically similar ontology property.",
        "- Related terms may support sh:class, datatype, node or logical context, but are not sh:path values.",
    ]
    for role in _TARGET_ROLE_KEYS:
        terms = target_roles.get(role) or []
        lines.append(f"\n{labels[role]}:")
        lines.extend(term_line(term, role) for term in terms)
        if not terms:
            lines.append("- (none)")
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


def _semantic_issue(value, *, default_code="SEMANTIC_REVIEW_ISSUE"):
    """Normalize one concise critic finding without retaining model reasoning."""
    if isinstance(value, str):
        return {"code": default_code, "message": value.strip(), "path": None}
    if not isinstance(value, dict):
        return None
    message = str(value.get("message") or value.get("description") or "").strip()
    if not message:
        return None
    path = str(value.get("path") or "").strip() or None
    return {
        "code": str(value.get("code") or default_code).strip() or default_code,
        "message": message,
        "path": path,
    }


def _parse_semantic_review(raw):
    """Parse and normalize the critic's closed JSON report."""
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("The semantic critic did not return a JSON object.")
    try:
        document, end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"The semantic critic returned invalid JSON: {exc.msg}.") from exc
    if text[start + end:].strip():
        raise ValueError("The semantic critic returned text after its JSON object.")
    if not isinstance(document, dict):
        raise ValueError("The semantic critic report must be a JSON object.")

    raw_status = str(document.get("status") or "").strip().lower().replace("-", "_")
    status = {
        "passed": "passed",
        "pass": "passed",
        "valid": "passed",
        "ok": "passed",
        "needs_correction": "needs_correction",
        "fail": "needs_correction",
        "failed": "needs_correction",
    }.get(raw_status)
    if status is None:
        raise ValueError("The semantic critic status must be 'passed' or 'needs_correction'.")

    raw_issues = document.get("issues") or []
    if not isinstance(raw_issues, list):
        raise ValueError("The semantic critic issues field must be an array.")
    issues = []
    for item in raw_issues:
        issue = _semantic_issue(item)
        if issue:
            issues.append(issue)
    clauses = document.get("clauses") or []
    if not isinstance(clauses, list):
        raise ValueError("The semantic critic clauses field must be an array.")
    normalized_clauses = []
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        path = str(clause.get("path") or "").strip()
        raw_clause_issues = clause.get("issues") or []
        if not isinstance(raw_clause_issues, list):
            raise ValueError("Each semantic critic clause issues field must be an array.")
        clause_issues = []
        for item in raw_clause_issues:
            issue = _semantic_issue(item)
            if issue:
                if not issue.get("path") and path:
                    issue["path"] = path
                clause_issues.append(issue)
                issues.append(issue)
        normalized_clauses.append({
            "path": path,
            "cardinality": str(clause.get("cardinality") or "not_applicable"),
            "value_constraint": str(clause.get("value_constraint") or "not_applicable"),
            "issues": clause_issues,
        })

    unique_issues = []
    seen = set()
    for issue in issues:
        key = (issue.get("code"), issue.get("path"), issue.get("message"))
        if key not in seen:
            seen.add(key)
            unique_issues.append(issue)
    if unique_issues:
        status = "needs_correction"
    elif status == "needs_correction":
        unique_issues.append({
            "code": "UNSPECIFIED_SEMANTIC_MISMATCH",
            "message": "The critic requested correction without describing a specific mismatch.",
            "path": None,
        })

    return {
        "status": status,
        "summary": str(document.get("summary") or "").strip(),
        "clauses": normalized_clauses,
        "issues": unique_issues,
    }


def _mechanical_review(error_type, message):
    """Represent deterministic validation feedback as a corrector input."""
    return {
        "status": "needs_correction",
        "summary": "The candidate failed deterministic SHACL validation.",
        "clauses": [],
        "issues": [{
            "code": f"{str(error_type or 'validation').upper()}_VALIDATION_FAILED",
            "message": str(message or "SHACL validation failed."),
            "path": None,
        }],
    }


def _public_semantic_review(status, critic_calls, correction_count, issues):
    """Build the stable, concise semantic-review result exposed by the API."""
    return {
        "status": status,
        "critic_calls": critic_calls,
        "correction_count": correction_count,
        "issues_found": len(issues),
        "issues": issues,
    }


def _astrea_merge_mode(payload):
    mode = str(payload.get("astrea_use_mode") or "none").strip().lower()
    return mode in {"merge", "both", "evidence-and-merge"}


def _astrea_merge_strategy(payload):
    strategy = str(
        payload.get("astrea_merge_technique")
        or payload.get("merge_strategy")
        or "generated-priority"
    ).strip().lower()
    strategy = {
        "priority-llm": "generated-priority",
        "priority_llm": "generated-priority",
    }.get(strategy, strategy)
    if strategy not in {"generated-priority", "restrictive"}:
        raise ValueError(
            "Astrea merge strategy must be generated-priority or restrictive."
        )
    return strategy


def _without_redundant_prefixes(document, prefixes):
    """Keep only merged prefix declarations not already present in the editor."""
    editor_bindings = set(_prefix_bindings(prefixes).items())
    body_lines = []
    for line in str(document or "").splitlines():
        match = _PREFIX_DECLARATION_RE.fullmatch(line.strip())
        if match and (match.group(1), match.group(2)) in editor_bindings:
            continue
        body_lines.append(line)
    return "\n".join(body_lines).strip()


def _merge_astrea_for_rule(
    payload,
    generated_shape,
    prefixes,
    target_roles,
    ontology_content,
    ontology_filename,
    target,
    grounding_catalog,
):
    """Merge only the Astrea fragment structurally matched to one rule."""
    if not _astrea_merge_mode(payload):
        return generated_shape, None, None

    from shard.baselines import (
        baseline_from_payload,
        focused_baseline_for_roles,
        merge_shape_documents,
        parse_baseline_shapes,
    )

    strategy = _astrea_merge_strategy(payload)
    metadata = {
        "requested": True,
        "applied": False,
        "strategy": strategy,
        "warnings": [],
    }
    content, filename = baseline_from_payload(payload, purpose="merge")
    metadata["baseline_name"] = filename
    if not content.strip():
        metadata["warnings"].append(
            "Astrea merge was requested, but no baseline document was available."
        )
        return generated_shape, metadata, None

    graph = payload.get("_astrea_merge_graph")
    if graph is None:
        graph = parse_baseline_shapes(content, filename)
        payload["_astrea_merge_graph"] = graph
    focused = focused_baseline_for_roles(graph, target_roles)
    if not focused:
        metadata["warnings"].append(
            "No Astrea shape matched the resolved focus nodes and constraint paths."
        )
        return generated_shape, metadata, None

    generated_document = f"{str(prefixes or '').strip()}\n\n{generated_shape}".strip()
    merged = merge_shape_documents(
        focused,
        generated_document,
        strategy,
        astrea_filename=filename,
        generated_filename="generated-rule.ttl",
    )
    merged_shape = _without_redundant_prefixes(
        merged.get("shape_document", ""), prefixes
    )
    validation = validate_shape_content(
        merged_shape,
        prefixes,
        validation_profiles_from_payload(payload),
    )
    if not validation.get("valid"):
        raise ValueError(
            validation.get("error")
            or validation.get("report_text")
            or "The focused Astrea merge failed SHACL validation."
        )

    grounding = validate_shape_grounding(
        merged_shape,
        prefixes,
        ontology_content,
        ontology_filename,
        target,
        grounding_catalog,
        target_roles,
    )
    if not grounding.get("valid"):
        raise ValueError(
            grounding.get("error")
            or "The focused Astrea merge failed ontology grounding."
        )

    metadata.update({
        "applied": True,
        "warnings": [str(item) for item in merged.get("warnings") or []],
        "statistics": merged.get("stats") or {},
    })
    return merged_shape, metadata, validation

def build_shape(payload):
    from shard.application.generation_support import clean_shacl_response
    from shard.baselines import baseline_context_for_roles
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
        astrea_shapes = baseline_context_for_roles(payload, target_roles)
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
            (
                "# Ontology IRI catalog for targets, classes and range references\n"
                "The authoritative sh:path allowlist remains the constrained property paths "
                "in the role contract."
            ),
            allowed_ontology_terms_text(grounding_catalog),
        ]).strip()

    prompt_file = PACKAGE_ROOT / "resources" / "prompts" / "rule_general.json"

    max_new_tokens = int(payload.get("max_new_tokens", DEFAULT_GEN_MAX_NEW_TOKENS))
    gen_model = get_chat_llm(
        model_id,
        kind="generator",
        temperature=temperature,
        max_new_tokens=max_new_tokens,
    )
    llm_review_enabled = bool(payload.get("llm_review", True))
    review_max_attempts = max(1, min(int(payload.get("review_max_attempts", 3)), 5))
    critic_model = None
    corrector_model = None
    if llm_review_enabled:
        critic_model = get_chat_llm(
            model_id,
            kind="critic",
            temperature=0.0,
            max_new_tokens=max_new_tokens,
        )
        corrector_model = get_chat_llm(
            model_id,
            kind="corrector",
            temperature=0.0,
            max_new_tokens=max_new_tokens,
        )

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
            backend_error_type = "timeout" if _is_timeout_error(e) else "backend"
            return {"shape": "", "valid": False, "error": str(e), "attempts": attempt,
                    "hints": [], "fallback": False, "error_type": backend_error_type,
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

        review_attempts = 0
        llm_review_applied = False
        semantic_review = _public_semantic_review("not_run", 0, 0, [])
        validation = None
        if llm_review_enabled:
            reviewed_result = last_result
            correction_count = 0
            critic_calls = 0
            collected_issues = []
            pending_report = None
            last_review_error = None

            while True:
                if pending_report is None:
                    report = None
                    format_error = None
                    for format_attempt in range(MAX_CRITIC_FORMAT_RETRIES):
                        critic_key = (
                            "semantic_critic_with_error"
                            if format_error else "semantic_critic"
                        )
                        logger.debug(
                            f"[build] semantic critic call {critic_calls + 1} "
                            f"using prompt '{critic_key}'"
                        )
                        critic_prompt = load_prompt_from_json(prompt_file, critic_key)
                        critic_chain = critic_prompt | critic_model | StrOutputParser()
                        critic_vars = {
                            "target_context": _target_context_text(target_roles),
                            "prefixes": prefixes,
                            "ontology_info": ontology_info,
                            "domain_context": domain_context,
                            "rule": rule,
                            "generation_guidance": generation_guidance,
                            "candidate_shapes": reviewed_result,
                        }
                        if format_error:
                            critic_vars["error"] = format_error
                        try:
                            critic_raw = critic_chain.invoke(critic_vars)
                        except Exception as exc:
                            logger.error(f"[build] semantic critic backend error: {exc}")
                            backend_error_type = (
                                "timeout" if _is_timeout_error(exc) else "backend"
                            )
                            return {
                                "shape": reviewed_result,
                                "valid": False,
                                "error": str(exc),
                                "attempts": attempt + 1,
                                "review_attempts": review_attempts,
                                "llm_review_applied": False,
                                "semantic_review": _public_semantic_review(
                                    "failed", critic_calls, correction_count,
                                    collected_issues,
                                ),
                                "hints": [],
                                "fallback": False,
                                "error_type": backend_error_type,
                                "message": f"Backend error during semantic critique: {exc}",
                            }
                        critic_calls += 1
                        review_attempts += 1
                        try:
                            report = _parse_semantic_review(critic_raw)
                            break
                        except ValueError as exc:
                            format_error = str(exc)
                            logger.warn(
                                f"[build] semantic critic format error on call "
                                f"{critic_calls}: {format_error}"
                            )
                    if report is None:
                        last_review_error = format_error
                        error_type = "review"
                        break
                else:
                    report = pending_report
                    pending_report = None

                for issue in report.get("issues") or []:
                    key = (issue.get("code"), issue.get("path"), issue.get("message"))
                    if not any(
                        (item.get("code"), item.get("path"), item.get("message")) == key
                        for item in collected_issues
                    ):
                        collected_issues.append(issue)

                if report["status"] == "passed":
                    validation = validate_shape_content(
                        reviewed_result,
                        prefixes,
                        validation_profiles_from_payload(payload),
                    )
                    if validation.get("valid"):
                        last_result = reviewed_result
                        llm_review_applied = True
                        semantic_review = _public_semantic_review(
                            "passed", critic_calls, correction_count, collected_issues,
                        )
                        logger.info(
                            f"[build] semantic critic passed after {critic_calls} "
                            f"check(s) and {correction_count} correction(s)"
                        )
                        break
                    last_review_error = str(
                        validation.get("error") or "SHACL validation failed."
                    )
                    error_type = str(validation.get("error_type") or "profile")
                    report = _mechanical_review(error_type, last_review_error)
                    for issue in report["issues"]:
                        collected_issues.append(issue)

                if correction_count >= review_max_attempts:
                    last_review_error = (
                        report.get("summary")
                        or "; ".join(
                            issue.get("message", "") for issue in report.get("issues") or []
                        )
                        or "The semantic critic still requires correction."
                    )
                    error_type = "review"
                    break

                logger.debug(
                    f"[build] semantic corrector call {correction_count + 1}/"
                    f"{review_max_attempts}"
                )
                corrector_prompt = load_prompt_from_json(
                    prompt_file, "semantic_corrector"
                )
                corrector_chain = corrector_prompt | corrector_model | StrOutputParser()
                corrector_vars = {
                    "target_context": _target_context_text(target_roles),
                    "prefixes": prefixes,
                    "shape_prefix": shape_prefix,
                    "ontology_info": ontology_info,
                    "domain_context": domain_context,
                    "rule": rule,
                    "generation_guidance": generation_guidance,
                    "astrea_shapes": astrea_shapes or "(none matched for this rule)",
                    "candidate_shapes": reviewed_result,
                    "audit_report": json.dumps(report, ensure_ascii=False, indent=2),
                }
                try:
                    corrected_raw = corrector_chain.invoke(corrector_vars)
                except Exception as exc:
                    logger.error(f"[build] semantic corrector backend error: {exc}")
                    backend_error_type = "timeout" if _is_timeout_error(exc) else "backend"
                    return {
                        "shape": reviewed_result,
                        "valid": False,
                        "error": str(exc),
                        "attempts": attempt + 1,
                        "review_attempts": review_attempts,
                        "llm_review_applied": False,
                        "semantic_review": _public_semantic_review(
                            "failed", critic_calls, correction_count,
                            collected_issues,
                        ),
                        "hints": [],
                        "fallback": False,
                        "error_type": backend_error_type,
                        "message": f"Backend error during semantic correction: {exc}",
                    }
                correction_count += 1
                review_attempts += 1
                reviewed_result = clean_shacl_response(corrected_raw)

                if not reviewed_result or "SHACL shapes not found" in corrected_raw:
                    last_review_error = (
                        "The semantic corrector did not return a complete Turtle document."
                    )
                    error_type = "review"
                    pending_report = _mechanical_review(
                        error_type, last_review_error
                    )
                    continue

                syntax = validate_shape_content(reviewed_result, prefixes, [])
                if not syntax.get("syntax_valid"):
                    last_review_error = str(
                        syntax.get("error") or "Invalid Turtle output."
                    )
                    error_type = "parse"
                    logger.warn(
                        f"[build] corrected Turtle failed parse: {last_review_error}"
                    )
                    pending_report = _mechanical_review(
                        error_type, last_review_error
                    )
                    continue

                reviewed_grounding = validate_shape_grounding(
                    reviewed_result,
                    prefixes,
                    ontology_content,
                    ontology_filename,
                    target,
                    grounding_catalog,
                    target_roles,
                )
                if not reviewed_grounding.get("valid"):
                    last_review_error = str(
                        reviewed_grounding.get("error")
                        or "Ontology grounding failed."
                    )
                    error_type = "grounding"
                    logger.warn(
                        f"[build] corrected Turtle failed grounding: "
                        f"{last_review_error}"
                    )
                    pending_report = _mechanical_review(
                        error_type, last_review_error
                    )
                    continue

                validation = validate_shape_content(
                    reviewed_result,
                    prefixes,
                    validation_profiles_from_payload(payload),
                )
                if not validation.get("valid"):
                    last_review_error = str(
                        validation.get("error") or "SHACL validation failed."
                    )
                    error_type = str(validation.get("error_type") or "profile")
                    logger.warn(
                        f"[build] corrected Turtle failed validation: "
                        f"{last_review_error}"
                    )
                    pending_report = _mechanical_review(
                        error_type, last_review_error
                    )
                    continue

                # A mechanically valid correction must be audited again. The
                # final critic, rather than the corrector itself, closes the loop.
                pending_report = None

            if not llm_review_applied:
                semantic_review = _public_semantic_review(
                    "failed", critic_calls, correction_count, collected_issues,
                )
                logger.error(
                    f"[build] semantic critic/corrector loop failed after "
                    f"{critic_calls} critic call(s) and {correction_count} "
                    f"correction(s): {last_review_error}"
                )
                return {
                    "shape": reviewed_result,
                    "valid": False,
                    "error": last_review_error,
                    "attempts": attempt + 1,
                    "review_attempts": review_attempts,
                    "llm_review_applied": False,
                    "semantic_review": semantic_review,
                    "hints": _hints_from_shape(reviewed_result, prefixes),
                    "fallback": False,
                    "error_type": error_type,
                    "message": (
                        "Semantic critique and correction did not converge within "
                        f"{review_max_attempts} correction attempt(s)."
                    ),
                }
        else:
            validation = validate_shape_content(
                last_result,
                prefixes,
                validation_profiles_from_payload(payload),
            )
            if not validation.get("valid"):
                logger.info(
                    f"[build] generated shape failed validation profile on attempt "
                    f"{attempt + 1}"
                )
                hints = _hints_from_shape(last_result, prefixes)
                return {
                    "shape": last_result,
                    "attempts": attempt + 1,
                    "review_attempts": 0,
                    "llm_review_applied": False,
                    "semantic_review": semantic_review,
                    "hints": hints,
                    "fallback": False,
                    **validation,
                }

        astrea_merge = None
        try:
            merged_result, astrea_merge, merged_validation = _merge_astrea_for_rule(
                payload,
                last_result,
                prefixes,
                target_roles,
                ontology_content,
                ontology_filename,
                target,
                grounding_catalog,
            )
            if astrea_merge and astrea_merge.get("applied"):
                last_result = merged_result
                validation = merged_validation
                logger.info(
                    "[build] merged the role-focused Astrea fragment before review "
                    f"using {astrea_merge['strategy']}."
                )
            elif astrea_merge:
                logger.warn(
                    "[build] Astrea merge was not applied: "
                    + "; ".join(astrea_merge.get("warnings") or [])
                )
        except ValueError as exc:
            failure_policy = str(
                payload.get("astrea_failure_policy") or "continue"
            ).strip().lower()
            astrea_merge = {
                "requested": True,
                "applied": False,
                "strategy": str(
                    payload.get("astrea_merge_technique")
                    or payload.get("merge_strategy")
                    or "generated-priority"
                ),
                "warnings": [str(exc)],
            }
            if failure_policy == "fail":
                logger.error(f"[build] focused Astrea merge failed: {exc}")
                return {
                    "shape": last_result,
                    "valid": False,
                    "error": str(exc),
                    "attempts": attempt + 1,
                    "review_attempts": review_attempts,
                    "llm_review_applied": llm_review_applied,
                    "semantic_review": semantic_review,
                    "hints": _hints_from_shape(last_result, prefixes),
                    "fallback": False,
                    "error_type": "merge",
                    "astrea_merge": astrea_merge,
                    "message": "The focused Astrea merge failed before human review.",
                }
            logger.warn(
                f"[build] focused Astrea merge failed; keeping generated shape: {exc}"
            )

        logger.info(f"[build] valid SHACL on attempt {attempt + 1}")
        hints = _hints_from_shape(last_result, prefixes)
        result_message = (
            validation.get("message")
            if validation.get("profile_count")
            else f"Valid SHACL generated by '{model_id}' (attempt {attempt + 1})."
        )
        if astrea_merge and astrea_merge.get("applied"):
            result_message += (
                " The matching Astrea fragment was merged before human review "
                f"using {astrea_merge['strategy']}."
            )
        elif astrea_merge and astrea_merge.get("warnings"):
            result_message += " Astrea merge was not applied; see merge warnings."
        return {
            "shape": last_result,
            "valid": True,
            "error": None,
            "attempts": attempt + 1,
            "review_attempts": review_attempts,
            "llm_review_applied": llm_review_applied,
            "semantic_review": semantic_review,
            "hints": hints,
            "fallback": False,
            "error_type": "none",
            "astrea_evidence_active": bool(astrea_shapes),
            "astrea_merge": astrea_merge,
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
            "message": result_message,
        }

    # Retries exhausted: return the invalid shape with the parse error.
    logger.error(f"[build] exhausted {MAX_RETRIES} attempts; last {error_type} error: {error_message}")
    return {"shape": last_result, "valid": False, "error": error_message, "attempts": MAX_RETRIES,
            "hints": [], "fallback": False, "error_type": error_type,
            "message": f"Reached {MAX_RETRIES} attempts; returning last output with its {error_type} error."}
