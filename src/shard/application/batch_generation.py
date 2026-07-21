"""Orchestrate rule-first SHACL generation from a data-constraint batch.

Runs the full SHARD pipeline on an uploaded Data Constraints template
(.html or .md) and streams the result shape-by-shape over Server-Sent-Events,
so the human-in-the-loop review queue fills up while generation continues.

Rule-first pipeline (all inference routed through ``shard.inference``):
  1. parse the ontology
  2. validate and parse the Data Constraints template
  3. resolve each rule to role-grouped ontology terms
  4. call the shared builder once for the complete rule context
  5. validate and consolidate generated rule constraints by target class

The workflow emits progress per rule and generated constraint document,
including unresolved and invalid results.
"""

import hashlib
import json
import re
import time
from dataclasses import asdict

from shard.baselines import baseline_from_payload, parse_baseline_shapes
from shard.domain.business_rules import parse_business_rules_document
from shard.domain.limits import MAX_SEMANTIC_TARGETS, MAX_TOP_K
from shard.domain.ontology import (
    ontology_base_namespace,
    ontology_prefix_block,
    ontology_shape_prefix,
    ontology_shapes_namespace,
    parse_ontology_graph,
)
from shard.application.shape_consolidation import consolidate_rule_shapes
from shard.deployment.operational import operational_settings


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def runtime_config_from_payload(payload):
    return payload.get("inference_config") or payload.get("model_config") or payload


def _llm_model_id(payload):
    return payload.get("llm_model") or payload.get("model") or "system.ai.gemma-3-12b"


def prepare_astrea_graph(payload):
    """Parse active Astrea evidence once and cache the graph within the request."""
    use_mode = str(payload.get("astrea_use_mode") or "").strip().lower()
    if use_mode and use_mode not in {"baseline", "both"}:
        return None
    graph = payload.get("_astrea_graph")
    if graph is not None:
        return graph
    content, filename = baseline_from_payload(payload)
    if not content.strip():
        return None
    graph = parse_baseline_shapes(content, filename)
    payload["_astrea_graph"] = graph
    return graph


def parse_business_rules_template(content: str, filename: str):
    """Parse a Data Constraints template into the batch service representation."""
    doc = parse_business_rules_document(content, filename=filename or "")
    if not doc.rules:
        raise ValueError("The template is valid, but no data-constraint entries were found.")
    return {
        "format": "markdown" if doc.source_format == "md" else doc.source_format,
        "metadata": doc.metadata,
        "filename": filename or doc.filename or "business_rules_template",
        "rules": [
            {
                "number": rule.number,
                "title": rule.title,
                "business_rule": rule.text,
                "source_format": rule.source_format,
                "raw": rule.raw,
            }
            for rule in doc.rules
        ],
    }


def _parse_llm_selection(content: str):
    text = str(content or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            raise ValueError(f"Resolver LLM fallback did not return a JSON array: {text[:500]}")
        parsed = json.loads(match.group(0))

    if isinstance(parsed, dict):
        parsed = parsed.get("targets") or parsed.get("selected_targets") or []
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"Resolver LLM fallback returned unsupported JSON: {parsed!r}")
    return [str(item) for item in parsed]


def resolver_llm_from_payload(payload):
    """Build a resolver fallback that can only choose from allowed targets."""
    if not _truthy(payload.get("resolver_llm_fallback", payload.get("llm_fallback")), default=True):
        return None

    def choose_targets(resolver_payload):
        from shard.inference import get_chat_llm

        candidates = resolver_payload.get("allowed_candidates") or [
            {"target": target} for target in resolver_payload.get("allowed_targets") or []
        ]
        candidate_lines = "\n".join(
            "- {target} | type={type} | label={label} | domain={domain} | range={range} | score={score} | reasons={reasons}".format(
                target=item.get("target", ""),
                type=item.get("type", ""),
                label=item.get("label", ""),
                domain=item.get("domain", ""),
                range=item.get("range", ""),
                score=item.get("score", ""),
                reasons="; ".join(item.get("reasons") or []),
            )
            for item in candidates
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You resolve a data constraint to relevant ontology terms. "
                    "Choose only from the provided candidate IRIs. "
                    "Return a JSON array of relevant ontology-term IRIs and nothing else. "
                    "Return [] if none are justified."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Rule number: {resolver_payload.get('rule_number', '')}\n"
                    f"Title: {resolver_payload.get('title', '')}\n"
                    f"Rule text:\n{resolver_payload.get('text', '')}\n\n"
                    f"Allowed candidates:\n{candidate_lines}\n"
                ),
            },
        ]
        model = get_chat_llm(_llm_model_id(payload), kind="resolver", temperature=0.0, max_new_tokens=500)
        result = model.invoke(messages)
        return _parse_llm_selection(getattr(result, "content", result))

    return choose_targets


def _target_lookup(ontology_terms):
    lookup = {}
    for term in ontology_terms:
        for key in (term.get("iri"), term.get("full_iri"), term.get("label"), term.get("id")):
            if key:
                lookup[str(key)] = term
    return lookup


def _resolution_role_terms(resolution, target_by_key):
    roles = {}
    missing = []
    for role in ("focus_nodes", "constraint_paths", "related_terms"):
        terms = []
        for target in getattr(resolution, role, []) or []:
            term = target_by_key.get(str(target))
            if term:
                terms.append(term)
            else:
                missing.append(target)
        roles[role] = terms
    return roles, missing


def _primary_resolution_term(target_roles):
    for role in ("focus_nodes", "constraint_paths", "related_terms"):
        if target_roles.get(role):
            return target_roles[role][0]
    return None


def _rule_text(rule):
    lines = []
    if rule.number:
        lines.append(f"Rule number: {rule.number}")
    if rule.title:
        lines.append(f"Rule title: {rule.title}")
    lines.extend(["Data constraint:", rule.text])
    return "\n".join(lines).strip()


def _semantic_payload(payload, ontology_content, ontology_terms):
    return {
        **payload,
        "ontology_terms": ontology_terms,
        "ontology_hash": hashlib.sha1(ontology_content.encode("utf-8")).hexdigest(),
        "embedding_model": payload.get("embedding_model") or "system.ai.qwen3-embedding-0-6b",
        "inference_config": runtime_config_from_payload(payload),
    }


def _prepare_rule_resolver_embeddings(payload, semantic_payload):
    """Prepare ontology embeddings for rule-context resolution when configured."""
    if not _truthy(payload.get("wait_embeddings"), default=True):
        return None

    from shard.application.term_ranking import embedding_status, prepare_embeddings
    from shard.observability import logger

    timeout_seconds = int(
        payload.get("embedding_timeout", operational_settings().embedding_timeout_seconds)
    )
    poll_seconds = float(payload.get("embedding_poll_seconds", 2.0))
    started = time.monotonic()
    result = prepare_embeddings(semantic_payload)
    status = result.get("status", "unknown")
    if status in {"disabled", "error", "cancelled"}:
        message = result.get("message") or f"Embedding preparation failed: {result}"
        if semantic_payload.get("strict_semantic"):
            raise RuntimeError(message)
        logger.warn(message)
        return result

    last_line = ""
    while True:
        current = embedding_status(semantic_payload)
        status = current.get("status", "unknown")
        line = f"{status}:{current.get('completed', 0)}/{current.get('total', 0)}"
        if line != last_line:
            logger.info(f"[batch-rule] resolver embeddings {line}")
            last_line = line
        if status == "ready":
            return current
        if status in {"disabled", "error", "cancelled"}:
            message = current.get("message") or f"Embedding preparation failed: {current}"
            if semantic_payload.get("strict_semantic"):
                raise RuntimeError(message)
            logger.warn(message)
            return current
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for resolver embeddings after {timeout_seconds}s.")
        time.sleep(poll_seconds)


def _emit(event_callback, event):
    if callable(event_callback):
        event_callback(event)


def _shape_status(result):
    if result.get("valid"):
        return "valid"
    if result.get("not_found"):
        return "skipped"
    return "invalid"


def _generate_rule_first(payload, *, semantic_ranker=None, resolver_llm=None, shape_builder=None, event_callback=None):
    """Generate batch shapes by resolving each data constraint before generation."""
    from shard.application.ontology_catalog import parse_ontology
    from shard.application.target_resolution import (
        DEFAULT_SEMANTIC_MAX_TARGETS,
        DEFAULT_SEMANTIC_TARGET_MARGIN,
        DEFAULT_SEMANTIC_THRESHOLD,
        resolve_rule_target,
    )

    started_at = time.monotonic()
    workflow_timeout = operational_settings().batch_workflow_timeout_seconds

    def ensure_workflow_deadline():
        if time.monotonic() - started_at > workflow_timeout:
            raise TimeoutError(
                f"Batch workflow exceeded its configured {workflow_timeout:g}s timeout."
            )

    if shape_builder is None:
        from shard.application.shape_generation import build_shape
        shape_builder = build_shape

    ontology_content = payload.get("ontology_content", "")
    ontology_filename = payload.get("ontology_filename", "ontology.ttl")
    batch_content = payload.get("batch_content", "")
    batch_filename = payload.get("batch_filename", "")

    parsed_ontology = parse_ontology(ontology_filename, ontology_content)
    ensure_workflow_deadline()
    astrea_graph = prepare_astrea_graph(payload)
    ontology_terms = parsed_ontology.get("entities") or []
    prefixes = payload.get("prefixes") or parsed_ontology.get("prefixes") or ""
    base_ns = payload.get("base_namespace") or parsed_ontology.get("base_namespace") or ""
    shape_ns = payload.get("shape_namespace") or parsed_ontology.get("shape_namespace") or ""
    shape_prefix = payload.get("shape_prefix") or parsed_ontology.get("shape_prefix") or "shape"
    target_by_key = _target_lookup(ontology_terms)

    doc = parse_business_rules_document(batch_content, filename=batch_filename)
    rules = doc.rules
    if not rules:
        raise ValueError("The template is valid, but no data-constraint entries were found.")
    _emit(event_callback, {
        "type": "start",
        "unit": "rule",
        "total": len(rules),
        "prefixes": prefixes,
        "base_namespace": base_ns,
        "shape_namespace": shape_ns,
        "shape_prefix": shape_prefix,
        "astrea_evidence_active": astrea_graph is not None,
        "astrea_baseline_name": baseline_from_payload(payload)[1] if astrea_graph is not None else "",
    })

    semantic_payload = _semantic_payload(payload, ontology_content, ontology_terms)
    _prepare_rule_resolver_embeddings(payload, semantic_payload)
    ensure_workflow_deadline()
    llm = resolver_llm if resolver_llm is not None else resolver_llm_from_payload(payload)

    generated_shapes = []
    rule_rows = []
    unresolved = []

    for index, rule in enumerate(rules, start=1):
        ensure_workflow_deadline()
        _emit(event_callback, {
            "type": "status",
            "stage": "rule",
            "current": index,
            "total": len(rules),
            "rule_number": rule.number,
            "title": rule.title,
            "message": f"Resolving rule {rule.number or index}: {rule.title}",
        })
        resolution = resolve_rule_target(
            rule,
            ontology_terms,
            index_map=payload.get("index_map") or None,
            llm=llm,
            semantic_payload=semantic_payload,
            semantic_ranker=semantic_ranker,
            semantic_threshold=float(payload.get("semantic_threshold", DEFAULT_SEMANTIC_THRESHOLD)),
            semantic_target_margin=float(
                payload.get("semantic_target_margin", DEFAULT_SEMANTIC_TARGET_MARGIN)
            ),
            semantic_max_targets=max(
                1,
                min(
                    MAX_SEMANTIC_TARGETS,
                    int(payload.get("semantic_max_targets", DEFAULT_SEMANTIC_MAX_TARGETS)),
                ),
            ),
            top_k=max(1, min(MAX_TOP_K, int(payload.get("top_k", 10)))),
        )
        ensure_workflow_deadline()
        resolution_dict = asdict(resolution)
        _emit(event_callback, {
            "type": "status",
            "stage": "resolution",
            "current": index,
            "total": len(rules),
            "rule_number": rule.number,
            "title": rule.title,
            "resolved_by": resolution.resolved_by,
            "confidence": resolution.confidence,
            "targets": resolution.targets,
            "focus_nodes": resolution.focus_nodes,
            "constraint_paths": resolution.constraint_paths,
            "related_terms": resolution.related_terms,
            "message": (
                f"Rule {rule.number or index} resolved by {resolution.resolved_by}"
                if resolution.targets else f"Rule {rule.number or index} was not resolved"
            ),
        })
        rule_entry = {
            "index": index,
            "rule_number": rule.number,
            "title": rule.title,
            "text": rule.text,
            "resolution": resolution_dict,
            "generated": [],
        }

        if resolution.resolved_by == "none" or not resolution.targets:
            unresolved.append({
                "rule_number": rule.number,
                "title": rule.title,
                "text": rule.text,
                "resolved_by": resolution.resolved_by,
                "candidates": resolution.candidates,
            })
            rule_rows.append(rule_entry)
            _emit(event_callback, {
                "type": "shape",
                "index": index,
                "total": len(rules),
                "rule_number": rule.number,
                "title": rule.title,
                "target": None,
                "property": None,
                "targets": [],
                "focus_nodes": [],
                "constraint_paths": [],
                "related_terms": [],
                "resolved_by": resolution.resolved_by,
                "status": "skipped",
                "shape": "",
                "error": "Rule could not be resolved to an ontology target.",
                "attempts": 0,
                "business_rule": rule.text,
            })
            continue

        target_roles, missing_targets = _resolution_role_terms(resolution, target_by_key)
        primary_term = _primary_resolution_term(target_roles)
        if missing_targets or not primary_term:
            unresolved.append({
                "rule_number": rule.number,
                "title": rule.title,
                "text": rule.text,
                "resolved_by": resolution.resolved_by,
                "missing_targets": missing_targets,
                "candidates": resolution.candidates,
            })
            rule_rows.append(rule_entry)
            _emit(event_callback, {
                "type": "shape",
                "index": index,
                "total": len(rules),
                "rule_number": rule.number,
                "title": rule.title,
                "target": None,
                "property": None,
                "targets": resolution.targets,
                "focus_nodes": resolution.focus_nodes,
                "constraint_paths": resolution.constraint_paths,
                "related_terms": resolution.related_terms,
                "resolved_by": resolution.resolved_by,
                "status": "skipped",
                "shape": "",
                "error": "One or more resolved terms were not found in the ontology catalog.",
                "attempts": 0,
                "business_rule": rule.text,
            })
            continue

        primary_target = primary_term.get("iri") or primary_term.get("full_iri")
        _emit(event_callback, {
            "type": "status",
            "stage": "generation",
            "current": index,
            "total": len(rules),
            "rule_number": rule.number,
            "title": rule.title,
            "target": primary_target,
            "targets": resolution.targets,
            "focus_nodes": resolution.focus_nodes,
            "constraint_paths": resolution.constraint_paths,
            "related_terms": resolution.related_terms,
            "message": f"Generating one constraint document for rule {rule.number or index}",
        })
        shape_payload = {
            **payload,
            "business_rule": _rule_text(rule),
            "target": {**primary_term, "ontology_filename": ontology_filename},
            "target_roles": target_roles,
            "_ontology_terms": ontology_terms,
            "ontology_content": ontology_content,
            "ontology_filename": ontology_filename,
            "prefixes": prefixes,
            "base_namespace": base_ns,
            "shape_namespace": shape_ns,
            "shape_prefix": shape_prefix,
            "model": _llm_model_id(payload),
            "temperature": float(payload.get("temperature", 0.5)),
            "domain_context": payload.get("domain_context", ""),
            "generation_guidance": payload.get("generation_guidance", ""),
        }
        result = shape_builder(shape_payload)
        ensure_workflow_deadline()
        if result.get("error_type") == "timeout":
            raise TimeoutError(
                result.get("error") or "The generation provider timed out."
            )
        shape_row = {
            "rule_number": rule.number,
            "rule_title": rule.title,
            "target": primary_target,
            "target_type": "rule-context",
            "targets": resolution.targets,
            "focus_nodes": resolution.focus_nodes,
            "constraint_paths": resolution.constraint_paths,
            "related_terms": resolution.related_terms,
            "resolved_by": resolution.resolved_by,
            "confidence": resolution.confidence,
            **result,
        }
        generated_shapes.append(shape_row)
        rule_entry["generated"].append(shape_row)
        _emit(event_callback, {
            "type": "shape",
            "index": index,
            "total": len(rules),
            "rule_number": rule.number,
            "title": rule.title,
            "target": primary_target,
            "property": (resolution.constraint_paths or [primary_target])[0],
            "targets": resolution.targets,
            "focus_nodes": resolution.focus_nodes,
            "constraint_paths": resolution.constraint_paths,
            "related_terms": resolution.related_terms,
            "target_type": "rule-context",
            "resolved_by": resolution.resolved_by,
            "confidence": resolution.confidence,
            "status": _shape_status(result),
            "shape": result.get("shape", "") if result.get("valid") else "",
            "error": result.get("error") or result.get("message"),
            "error_type": result.get("error_type"),
            "attempts": result.get("attempts", 0),
            "llm_review_applied": result.get("llm_review_applied", False),
            "review_attempts": result.get("review_attempts", 0),
            "semantic_review": result.get("semantic_review") or {},
            "syntax_valid": result.get("syntax_valid"),
            "profile_valid": result.get("profile_valid"),
            "profile_count": result.get("profile_count", 0),
            "profile_names": result.get("profile_names", []),
            "generic_profile_active": result.get("generic_profile_active", False),
            "generic_profile_name": result.get("generic_profile_name"),
            "domain_profile_count": result.get("domain_profile_count", 0),
            "domain_profile_names": result.get("domain_profile_names", []),
            "validation_level": result.get("validation_level"),
            "business_rule": rule.text,
        })

        rule_rows.append(rule_entry)

    ensure_workflow_deadline()
    consolidated = consolidate_rule_shapes(
        generated_shapes,
        prefixes,
        shape_namespace=shape_ns,
        shape_prefix=shape_prefix,
    )
    shape_parts = [part for part in [consolidated["node_shapes"], consolidated["property_shapes"]] if part]
    shape_body = "\n\n".join(shape_parts).strip()
    shape_document = f"{prefixes.strip()}\n\n{shape_body}".strip() if shape_body else prefixes.strip()
    valid_count = sum(1 for item in generated_shapes if item.get("valid"))
    invalid_count = len(generated_shapes) - valid_count
    resolved_term_count = sum(
        len(row.get("resolution", {}).get("targets") or [])
        for row in rule_rows
    )
    summary = {
        "rules_total": len(rules),
        "rules_unresolved": len(unresolved),
        "targets_total": resolved_term_count,
        "generated_total": len(generated_shapes),
        "valid": valid_count,
        "invalid": invalid_count,
    }
    _emit(event_callback, {
        "type": "done",
        "unit": "rule",
        "total": len(rules),
        "valid": valid_count,
        "invalid": invalid_count,
        "skipped": len(unresolved),
        "node_shapes": consolidated["node_shapes"],
        "property_shapes": consolidated["property_shapes"],
        "shape_document": shape_document,
        "summary": summary,
        "unresolved_rules": unresolved,
    })

    return {
        "prefixes": prefixes,
        "base_namespace": base_ns,
        "shape_namespace": shape_ns,
        "shape_prefix": shape_prefix,
        "rules": rule_rows,
        "shapes": generated_shapes,
        "node_shapes": consolidated["node_shapes"],
        "property_shapes": consolidated["property_shapes"],
        "shape_document": shape_document,
        "unresolved_rules": unresolved,
        "consolidation": consolidated["consolidation"],
        "summary": summary,
    }


def generate_batch_shapes(payload, *, semantic_ranker=None, resolver_llm=None, shape_builder=None, event_callback=None):
    """Generate SHACL shapes by resolving and processing each data constraint."""
    return _generate_rule_first(
        payload,
        semantic_ranker=semantic_ranker,
        resolver_llm=resolver_llm,
        shape_builder=shape_builder,
        event_callback=event_callback,
    )


def stream_batch_generation(payload, emit):
    """Run batch generation and emit the existing SSE event sequence."""
    ontology_content = payload.get("ontology_content", "")

    emit({"type": "status", "stage": "parsing", "message": "Parsing ontology…"})
    onto = parse_ontology_graph(
        ontology_content,
        payload.get("ontology_filename", "ontology.ttl"),
    )
    base_ns = payload.get("base_namespace") or ontology_base_namespace(onto)
    detected_shape_ns, _ = ontology_shapes_namespace(onto, base_ns)
    shape_ns = payload.get("shape_namespace") or detected_shape_ns
    detected_shape_prefix, _ = ontology_shape_prefix(onto, shape_ns)
    shape_prefix = payload.get("shape_prefix") or detected_shape_prefix
    prefixes = payload.get("prefixes") or ontology_prefix_block(
        onto, base_ns, shape_ns, shape_prefix,
    )

    parsed_batch = payload.get("_business_rules") or parse_business_rules_template(
        payload.get("batch_content", ""),
        payload.get("batch_filename", ""),
    )
    rule_count = len(parsed_batch.get("rules") or [])
    emit({
        "type": "status",
        "stage": "template",
        "current": rule_count,
        "total": rule_count,
        "message": f"Validated Data Constraints template: {rule_count} constraint(s).",
    })

    payload.setdefault("prefixes", prefixes)
    payload.setdefault("base_namespace", base_ns)
    payload.setdefault("shape_namespace", shape_ns)
    payload.setdefault("shape_prefix", shape_prefix)
    emit({
        "type": "status",
        "stage": "preprocessing",
        "current": 0,
        "total": rule_count,
        "message": "Preparing batch generation…",
    })
    generate_batch_shapes(payload, event_callback=emit)
