#!/usr/bin/env python3
"""
build-shacl-shape service  (br2shacl-ui)  — Mode A (single rule)

Generates a SHACL shape for one selected ontology target from a hand-written
business rule, reusing the real text2shacl generator:

  * prompts/multiagent.json → generator_agent_property_without_astrea
    (and ..._with_error on a failed parse)
  * utils.clean_shacl_response to extract the Turtle
  * the rdflib parse-and-retry loop ported from multiagent._generator_agent
  * model_loader.get_chat_llm for inference (Databricks or HF, routed by id)

The hand-written rule plays the role of the RAG evidence. The ontology context is
rebuilt by re-parsing the uploaded ontology and calling the same helpers the
OntologyAgent uses (get_info_by_name, get_property_domain), so the model gets the
rich context, not just the flattened term.

Endpoint:  POST http://127.0.0.1:9102/build-shacl-shape
  request : {business_rule, target, prefixes, ontology_content, model, provider,
             temperature?, inference_config?, base_namespace?, shape_namespace?,
             shape_prefix?}
  response: {shape, valid, error, attempts, hints[], fallback, message}
"""

import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "text2shacl_core"))

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SH, XSD
from rdflib.util import guess_format
from langchain_core.output_parsers import StrOutputParser
from ontology_io import (
    ontology_base_namespace,
    ontology_prefix_block,
    ontology_shape_prefix,
    ontology_shapes_namespace,
    parse_ontology_graph,
)
from service_http import (
    new_request_id,
    read_json,
    reject_disabled_provider,
    send_health,
    send_json,
    send_options,
)

HOST = "127.0.0.1"
PORT = 9102
MAX_RETRIES = 10
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERIC_SHACL_PROFILE_NAME = "shacl-shacl.ttl"
GENERIC_SHACL_PROFILE_PATH = os.path.join(ROOT_DIR, GENERIC_SHACL_PROFILE_NAME)
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


def _runtime_config(payload):
    return payload.get("inference_config") or payload.get("model_config") or payload


def _rdf_format(filename):
    guessed = guess_format(filename or "") if filename else None
    return guessed or "turtle"


_STANDARD_NAMESPACES = (
    str(SH),
    str(RDF),
    str(RDFS),
    str(OWL),
    str(XSD),
)


def _validation_profiles(payload):
    profiles = payload.get("validation_profiles") or payload.get("shape_validation_profiles") or []
    if not isinstance(profiles, list):
        return []
    out = []
    for idx, item in enumerate(profiles):
        if isinstance(item, str):
            content = item
            name = f"profile-{idx + 1}.ttl"
        elif isinstance(item, dict):
            content = item.get("content") or ""
            name = item.get("name") or item.get("filename") or f"profile-{idx + 1}.ttl"
        else:
            continue
        if content.strip():
            out.append({"name": name, "content": content})
    return out


def _generic_validation_profile():
    with open(GENERIC_SHACL_PROFILE_PATH, "r", encoding="utf-8") as handle:
        return {
            "name": GENERIC_SHACL_PROFILE_NAME,
            "content": handle.read(),
            "scope": "generic",
            "path": GENERIC_SHACL_PROFILE_PATH,
        }


def _validation_metadata(generic_profile, domain_profiles):
    domain_names = [profile.get("name") or f"profile-{idx + 1}.ttl" for idx, profile in enumerate(domain_profiles)]
    active_profiles = ([generic_profile] if generic_profile else []) + list(domain_profiles)
    return {
        "profile_count": len(active_profiles),
        "profile_names": [profile.get("name") or f"profile-{idx + 1}.ttl" for idx, profile in enumerate(active_profiles)],
        "generic_profile_active": bool(generic_profile),
        "generic_profile_name": generic_profile["name"] if generic_profile else None,
        "domain_profile_count": len(domain_profiles),
        "domain_profile_names": domain_names,
        "validation_level": "syntax+generic+profile" if domain_profiles else "syntax+generic",
    }


def _validation_scope_text(metadata):
    if metadata.get("domain_profile_count"):
        names = ", ".join(metadata.get("domain_profile_names") or [])
        return f"generic SHACL2SHACL + domain profile ({names})"
    return "generic SHACL2SHACL"


def validate_shape_content(shape, prefixes="", profiles=None):
    """Validate generated SHACL as Turtle and active SHACL2SHACL profiles."""
    domain_profiles = profiles or []
    try:
        generic_profile = _generic_validation_profile()
    except Exception as exc:
        metadata = _validation_metadata(None, domain_profiles)
        return {
            "valid": False,
            "syntax_valid": None,
            "profile_valid": False,
            **metadata,
            "error": f"Generic SHACL2SHACL profile '{GENERIC_SHACL_PROFILE_NAME}' could not be loaded: {exc}",
            "error_type": "profile",
            "message": "The generic SHACL2SHACL profile could not be loaded.",
        }
    metadata = _validation_metadata(generic_profile, domain_profiles)
    active_profiles = [generic_profile] + list(domain_profiles)
    full_shape = f"{prefixes or ''}\n{shape or ''}"

    try:
        data_graph = Graph(bind_namespaces="none")
        data_graph.parse(data=full_shape, format="turtle")
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": False,
            "profile_valid": None,
            **metadata,
            "error": str(exc),
            "error_type": "parse",
            "message": "Generated shape is not valid Turtle.",
        }

    try:
        from pyshacl import validate as pyshacl_validate
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": True,
            "profile_valid": False,
            **metadata,
            "error": f"pyshacl is required for shape validation profiles: {exc}",
            "error_type": "profile",
            "message": "Shape validation profile could not run because pyshacl is not installed.",
        }

    shapes_graph = Graph(bind_namespaces="none")
    try:
        for profile in active_profiles:
            shapes_graph.parse(
                data=profile["content"],
                format=_rdf_format(profile["name"]),
                publicID=profile["name"],
            )
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": True,
            "profile_valid": False,
            **metadata,
            "error": str(exc),
            "error_type": "profile",
            "message": "One of the shape validation profile files could not be parsed.",
        }

    try:
        conforms, _report_graph, report_text = pyshacl_validate(
            data_graph=data_graph,
            shacl_graph=shapes_graph,
            inference="rdfs",
            abort_on_first=False,
            allow_infos=True,
            allow_warnings=True,
            meta_shacl=False,
            advanced=True,
        )
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": True,
            "profile_valid": False,
            **metadata,
            "error": str(exc),
            "error_type": "profile",
            "message": "Shape validation profile execution failed.",
        }

    report_text = str(report_text or "").strip()
    if conforms:
        return {
            "valid": True,
            "syntax_valid": True,
            "profile_valid": True,
            **metadata,
            "error": None,
            "error_type": "none",
            "report_text": report_text,
            "message": f"Valid Turtle / SHACL. {_validation_scope_text(metadata)} OK.",
        }

    return {
        "valid": False,
        "syntax_valid": True,
        "profile_valid": False,
        **metadata,
        "error": report_text[:5000] if report_text else "Shape validation profile reported non-conformance.",
        "report_text": report_text[:5000],
        "error_type": "profile",
        "message": f"Generated shape does not conform to active SHACL2SHACL validation ({_validation_scope_text(metadata)}).",
    }


def _ontology_grounding_catalog(ontology_content, ontology_filename, target):
    """Return ontology term IRIs and target-scoped property hints."""
    from parse_ontology import parse_ontology

    parsed = parse_ontology(ontology_filename or "ontology.ttl", ontology_content or "")
    terms = parsed.get("entities") or []
    valid_classes = set()
    valid_properties = set()
    term_by_ref = {}
    graph = parse_ontology_graph(ontology_content, ontology_filename or "ontology.ttl")

    for term in terms:
        full_iri = term.get("full_iri")
        if not full_iri:
            continue
        ref = URIRef(str(full_iri))
        term_by_ref[str(ref)] = term
        if term.get("type") == "class":
            valid_classes.add(ref)
        elif term.get("type") == "property":
            valid_properties.add(ref)

    target_refs = {
        str(value)
        for value in (
            target.get("iri"),
            target.get("full_iri"),
            target.get("label"),
            target.get("id"),
            target.get("domain"),
        )
        if value
    }
    target_full = str(target.get("full_iri") or "")
    target_domain = str(target.get("domain") or "")
    scoped_properties = []
    for ref in sorted(valid_properties, key=str):
        term = term_by_ref.get(str(ref), {})
        same_target = str(term.get("full_iri") or "") == target_full
        same_domain = bool(target_domain and str(term.get("domain") or "") == target_domain)
        domain_is_target = bool(str(term.get("domain") or "") in target_refs)
        if same_target or same_domain or domain_is_target:
            scoped_properties.append(ref)

    return {
        "graph": graph,
        "valid_classes": valid_classes,
        "valid_properties": valid_properties,
        "valid_terms": valid_classes | valid_properties,
        "scoped_properties": scoped_properties,
    }


def _display_iri(graph, value):
    try:
        return graph.namespace_manager.normalizeUri(value)
    except Exception:
        return f"<{value}>"


def _display_iri_list(graph, values, limit=40):
    labels = [_display_iri(graph, value) for value in sorted(values, key=str)]
    if len(labels) > limit:
        labels = labels[:limit] + [f"... ({len(values) - limit} more)"]
    return ", ".join(labels) if labels else "(none)"


def _allowed_ontology_terms_text(catalog):
    graph = catalog["graph"]
    return "\n".join([
        "Allowed ontology classes:",
        _display_iri_list(graph, catalog["valid_classes"]),
        "Allowed ontology properties for this target/domain:",
        _display_iri_list(graph, catalog["scoped_properties"] or catalog["valid_properties"]),
    ])


def validate_shape_grounding(shape, prefixes="", ontology_content="", ontology_filename="", target=None, catalog=None):
    """Validate that generated shape references only ontology terms for key SHACL IRIs."""
    if not ontology_content:
        return {"valid": True, "error": None, "error_type": "none", "invalid_iris": []}

    target = target or {}
    catalog = catalog or _ontology_grounding_catalog(ontology_content, ontology_filename, target)
    graph = Graph(bind_namespaces="none")
    graph.parse(data=f"{prefixes or ''}\n{shape or ''}", format="turtle")

    checks = [
        (SH.path, catalog["valid_properties"], "property"),
        (SH.targetClass, catalog["valid_classes"], "class"),
        (SH["class"], catalog["valid_classes"], "class"),
        (SH.node, catalog["valid_terms"], "ontology term"),
    ]
    invalid = []
    for predicate, allowed, expected_kind in checks:
        for value in graph.objects(None, predicate):
            if not isinstance(value, URIRef):
                continue
            value_text = str(value)
            if value_text.startswith(_STANDARD_NAMESPACES):
                continue
            if value not in allowed:
                invalid.append({
                    "predicate": _display_iri(graph, predicate),
                    "iri": _display_iri(graph, value),
                    "expected": expected_kind,
                })

    if not invalid:
        return {"valid": True, "error": None, "error_type": "none", "invalid_iris": []}

    first = invalid[0]
    error = "\n".join([
        f"Invalid IRI: {first['iri']} does not exist in the ontology as a valid {first['expected']} for {first['predicate']}.",
        _allowed_ontology_terms_text(catalog),
        "Use only IRIs from the provided ontology. Do not invent properties or classes.",
    ])
    return {
        "valid": False,
        "syntax_valid": True,
        "profile_valid": None,
        "profile_count": 0,
        "profile_names": [],
        "error": error,
        "error_type": "grounding",
        "invalid_iris": invalid,
        "message": "Generated shape references ontology IRIs that are not present in the uploaded ontology.",
    }


def _build_ontology_info(ontology_content, target):
    """Rebuild ontology context for the target, mirroring OntologyAgent iter 0."""
    from utils import get_info_by_name, get_property_domain

    if not ontology_content:
        # No ontology content: fall back to the flattened note from the UI.
        return f"# {target.get('iri')}\n{target.get('ontologyNote', '')}\n"

    g = parse_ontology_graph(ontology_content, target.get("ontology_filename", "ontology.ttl"))

    prop = target.get("full_iri") or target.get("iri")
    info = ""
    prop_info = get_info_by_name(g, prop)
    if prop_info:
        info += f"# {prop}\n{prop_info}\n\n"

    if target.get("type") == "property":
        domain = get_property_domain(g, prop)
        if domain:
            info += f"Domain of {prop}: {domain}\n\n"
            for owl_class in domain:
                cls_info = get_info_by_name(g, owl_class)
                if cls_info:
                    info += f"## {owl_class}\n{cls_info}\n\n"
    return info or f"# {prop}\n{target.get('ontologyNote', '')}\n"


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
    from baseline_shapes import baseline_context_for_target
    from model_loader import get_chat_llm, DEFAULT_GEN_MAX_NEW_TOKENS
    from prompts import load_prompt_from_json
    from utils import clean_shacl_response
    from Logger import logger

    target = payload.get("target") or {}
    rule = payload.get("business_rule", "")
    domain_context = (payload.get("domain_context") or "").strip() or "(none provided)"
    generation_guidance = (payload.get("generation_guidance") or "").strip() or "(none provided)"
    prefixes = payload.get("prefixes") or ""
    ontology_content = payload.get("ontology_content", "")
    temperature = float(payload.get("temperature", 0.5))
    model_id = payload.get("model") or "system.ai.gemma-3-12b"

    base_ns = payload.get("base_namespace") or ""
    shape_ns = payload.get("shape_namespace") or ""
    shape_prefix = str(payload.get("shape_prefix") or "").strip()
    ontology_graph = None
    if ontology_content and (not base_ns or not shape_ns or not shape_prefix or not prefixes):
        ontology_graph = parse_ontology_graph(
            ontology_content,
            payload.get("ontology_filename", "ontology.ttl"),
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

    logger.info(f"[build] target={target.get('iri')} type={target.get('type')} model={model_id}")
    try:
        astrea_shapes = baseline_context_for_target(payload, target)
    except ValueError as exc:
        return {
            "shape": "", "valid": False, "error": str(exc), "attempts": 0,
            "hints": [], "fallback": False, "error_type": "config",
            "message": f"Invalid Astrea baseline shapes: {exc}",
        }
    if astrea_shapes:
        logger.info(
            f"[build] using target-specific Astrea baseline evidence "
            f"({len(astrea_shapes)} chars)."
        )
    else:
        logger.info("[build] no Astrea baseline evidence matched the selected target.")
    ontology_info = _build_ontology_info(ontology_content, target)
    grounding_catalog = None
    if ontology_content:
        grounding_catalog = _ontology_grounding_catalog(
            ontology_content,
            payload.get("ontology_filename", target.get("ontology_filename", "ontology.ttl")),
            target,
        )
        ontology_info = "\n\n".join([
            ontology_info.strip(),
            "# Allowed ontology IRIs for generated shapes",
            _allowed_ontology_terms_text(grounding_catalog),
        ]).strip()

    prompt_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "text2shacl_core", "prompts")
    prompt_file = os.path.join(prompt_dir, "rule_general.json")

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
            "property": target.get("full_iri") or target.get("iri"),
            "prefixes": prefixes,
            "shape_prefix": shape_prefix,
            "ontology_info": ontology_info,
            "domain_context": domain_context,
            "rule": rule,
            "generation_guidance": generation_guidance,
            "astrea_shapes": astrea_shapes or "(none matched for this target)",
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
            payload.get("ontology_filename", target.get("ontology_filename", "ontology.ttl")),
            target,
            grounding_catalog,
        )
        if not grounding.get("valid"):
            e = grounding.get("error")
            logger.warn(f"[build] grounding failed on attempt {attempt + 1}: {e}")
            error_message = str(e)
            error_type = "grounding"
            attempt += 1
            continue

        validation = validate_shape_content(last_result, prefixes, _validation_profiles(payload))
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


def validate_model(payload):
    """Lightweight availability check before a custom model is added in the UI."""
    provider = str(payload.get("provider") or "").strip().lower()
    model = str(payload.get("model") or "").strip()
    role = str(payload.get("role") or "chat").strip().lower()

    if not model:
        return {"ok": False, "message": "Enter a model id first."}
    if provider not in {"databricks", "huggingface"}:
        return {"ok": False, "message": "Choose Databricks or Hugging Face first."}

    if provider == "databricks":
        import httpx
        from runtime_config import get_databricks_base_url, get_databricks_token
        from model_loader_databricks import normalize_model_id

        model = normalize_model_id(model)
        token = get_databricks_token()
        base_url = get_databricks_base_url()
        if not token or not base_url:
            return {
                "ok": False,
                "message": "Databricks token and base URL are required to validate this model.",
            }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if role == "embedding":
            url = f"{base_url}/embeddings"
            body = {"model": model, "input": ["ping"]}
        else:
            url = f"{base_url}/chat/completions"
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            }

        try:
            res = httpx.post(url, headers=headers, json=body, timeout=20)
        except Exception as exc:
            return {"ok": False, "message": f"Could not reach Databricks endpoint: {exc}"}

        if 200 <= res.status_code < 300:
            return {"ok": True, "message": f"Model '{model}' is available."}
        detail = res.text[:500]
        return {
            "ok": False,
            "message": f"Databricks rejected '{model}' ({res.status_code}): {detail}",
        }

    # Hugging Face: check repository visibility/access without downloading weights.
    try:
        from huggingface_hub import model_info
        from runtime_config import get_hf_token

        info = model_info(model, token=get_hf_token() or None)
        pipeline = getattr(info, "pipeline_tag", None)
        suffix = f" ({pipeline})" if pipeline else ""
        return {"ok": True, "message": f"Model '{model}' is available on Hugging Face{suffix}."}
    except Exception as exc:
        return {"ok": False, "message": f"Hugging Face model '{model}' is not available: {exc}"}


def merge_shapes(payload):
    """Merge final generated shapes with an uploaded Astrea baseline."""
    from baseline_shapes import baseline_from_payload, merge_shape_documents

    generated = str(
        payload.get("generated_shapes")
        or payload.get("generated_content")
        or payload.get("shape_document")
        or ""
    )
    if not generated.strip():
        raise ValueError("Missing generated SHACL content to merge.")
    astrea_content, astrea_filename = baseline_from_payload(payload)
    if not astrea_content.strip():
        raise ValueError("Load an Astrea baseline TTL before using a merge strategy.")

    technique = str(payload.get("technique") or payload.get("merge_mode") or "").strip().lower()
    merged = merge_shape_documents(
        astrea_content,
        generated,
        technique,
        astrea_filename=astrea_filename,
        generated_filename=str(payload.get("generated_filename") or "generated_shapes.ttl"),
    )
    validation = validate_shape_content(
        merged["shape_document"],
        "",
        _validation_profiles(payload),
    )
    return {
        **merged,
        **validation,
        "astrea_baseline_name": astrea_filename,
        "merge_message": (
            f"Merged generated shapes with '{astrea_filename}' using {technique}."
        ),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, status, payload):
        send_json(self, status, payload, request_id=getattr(self, "request_id", None))

    def do_OPTIONS(self):
        send_options(self)

    def do_GET(self):
        self.request_id = new_request_id(self.headers)
        if self.path == "/health":
            send_health(self, "build-shacl-shape", request_id=self.request_id)
            return
        self._send_json(404, {"error": "unknown endpoint"})

    def do_POST(self):
        self.request_id = new_request_id(self.headers)
        try:
            payload = read_json(self)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        # Real rdflib Turtle validation for the editor "Check" button.
        if self.path == "/validate-shape":
            shape = payload.get("shape", "")
            prefixes = payload.get("prefixes", "")
            self._send_json(200, validate_shape_content(shape, prefixes, _validation_profiles(payload)))
            return

        if self.path == "/merge-shapes":
            try:
                self._send_json(200, merge_shapes(payload))
            except ValueError as exc:
                self._send_json(400, {"valid": False, "error": str(exc), "error_type": "merge"})
            except Exception as exc:
                self._send_json(500, {"valid": False, "error": str(exc), "error_type": "merge"})
            return

        if self.path in {"/validate-model", "/build-shacl-shape"} and reject_disabled_provider(
                self, payload, request_id=self.request_id):
            return

        if self.path == "/validate-model":
            from runtime_config import inference_config
            with inference_config(_runtime_config(payload)):
                self._send_json(200, validate_model(payload))
            return

        if self.path != "/build-shacl-shape":
            self._send_json(404, {"error": "unknown endpoint"})
            return

        from Logger import logger
        logger.set_verbosity(3)
        try:
            from runtime_config import inference_config
            with logger.request_context(self.request_id) as log_lines, inference_config(_runtime_config(payload)):
                result = build_shape(payload)
        except Exception as exc:
            status = 400 if isinstance(exc, ValueError) else 500
            error_type = "request" if status == 400 else "service"
            self._send_json(status, {"shape": "", "valid": False, "error": str(exc),
                                     "attempts": 0, "hints": [], "fallback": True,
                                     "logs": "\n".join(log_lines) if "log_lines" in locals() else "",
                                     "error_type": error_type,
                                     "message": f"build-shacl-shape failed: {exc}"})
            return
        status = 502 if result.get("error_type") == "backend" else 200
        self._send_json(status, {"provider": payload.get("provider"),
                                 "model": payload.get("model"),
                                 "logs": "\n".join(log_lines), **result})


if __name__ == "__main__":
    print(f"build-shacl-shape service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
