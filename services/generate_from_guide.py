#!/usr/bin/env python3
"""
generate-from-guide service  (br2shacl-ui)  — Mode B (full guide)

Runs the full text2shacl pipeline on an uploaded Business Rules template
(.html or .md) and streams the result shape-by-shape over Server-Sent-Events,
so the human-in-the-loop review queue fills up while generation continues.

Pipeline (self-contained-lite, all inference via model_loader / Databricks):
  1. parse the ontology
  2. validate + parse the Business Rules template
  3. index the extracted business rules in memory
     — emits {"type":"status",...} progress events
  4. stream generation (multiagent_stream.stream_shacl_generation)
     — emits {"type":"start"} then one {"type":"shape"} per property,
       including invalid ones (10 failed attempts) with the parse error,
       then {"type":"done"} with the aggregated node shapes.

Transport: the client POSTs a JSON body and reads the streaming response with
fetch()+ReadableStream (not EventSource, which is GET-only), parsing on "\n\n".

Endpoint: POST http://127.0.0.1:9103/generate-from-guide
  request : {ontology_content, guide_content, guide_filename,
             llm_model, text_model, vision_model, embedding_model, temperature,
             provider, inference_config?, prefixes?, base_namespace?, shape_namespace?,
             shape_prefix?}
"""

import json
import hashlib
import os
import re
import sys
import time
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "text2shacl_core"))

from business_rules import parse_business_rules_document
from baseline_shapes import baseline_from_payload, parse_baseline_shapes
from ns_utils import ensure_legacy_era_aliases
from ontology_io import (
    ontology_base_namespace,
    ontology_prefix_block,
    ontology_shape_prefix,
    ontology_shapes_namespace,
    parse_ontology_graph,
)
from rdflib import BNode, Graph, Literal, RDF, SH, URIRef
from service_http import (
    new_request_id,
    read_json,
    reject_disabled_provider,
    send_health,
    send_json,
    send_options,
)

HOST = "127.0.0.1"
PORT = 9103
DEFAULT_ITERATION_MODE = "rule"
LEGACY_ITERATION_MODE = "property"


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_config(payload):
    return payload.get("inference_config") or payload.get("model_config") or payload


def _llm_model_id(payload):
    return payload.get("llm_model") or payload.get("model") or "system.ai.gemma-3-12b"


def _prepare_astrea_graph(payload):
    """Parse uploaded Astrea shapes once and cache the graph within the request."""
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
    """Parse a Business Rules template, preserving the legacy service shape."""
    doc = parse_business_rules_document(content, filename=filename or "")
    if not doc.rules:
        raise ValueError("The template is valid, but no business rule entries were found.")
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


def business_rule_chunks(parsed: dict, domain_context: str = "", generation_guidance: str = ""):
    meta = parsed.get("metadata") or {}
    domain_context = (domain_context or "").strip()
    generation_guidance = (generation_guidance or "").strip()
    chunks = []
    for rule in parsed.get("rules") or []:
        lines = [
            "BUSINESS RULE TEMPLATE ENTRY",
            f"Ontology: {meta.get('ontology', '')}",
        ]
        if domain_context:
            lines.extend(["Domain context:", domain_context])
        if generation_guidance:
            lines.extend(["SHACL generation guidance:", generation_guidance])
        lines.extend([
            f"Rule number: {rule.get('number', '')}",
            f"Rule title: {rule.get('title', '')}",
            "Business rule:",
            rule.get("business_rule", ""),
        ])
        chunks.append("\n".join(lines).strip())
    return chunks


def business_rules_preview_html(parsed: dict):
    """Small normalized HTML representation for logs/debugging and future reuse."""
    meta = parsed.get("metadata") or {}
    rules_html = []
    for rule in parsed.get("rules") or []:
        body = "".join(f"<p>{escape(p.strip())}</p>" for p in rule.get("business_rule", "").split("\n\n") if p.strip())
        rules_html.append(
            f"<section class=\"rule\"><h2>{escape(rule.get('number', ''))}: {escape(rule.get('title', ''))}</h2>{body}</section>"
        )
    return (
        "<!doctype html><html><body><header class=\"metadata\">"
        "<h1>Business Rules</h1>"
        f"<p>Ontology: {escape(meta.get('ontology', ''))}</p>"
        f"<p>Author: {escape(meta.get('author', ''))}</p>"
        f"<p>Date: {escape(meta.get('date', ''))}</p>"
        f"<p>Description: {escape(meta.get('description', ''))}</p>"
        "</header><main>"
        + "\n".join(rules_html)
        + "</main></body></html>"
    )


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


def _resolver_llm(payload):
    """Build a resolver fallback that can only choose from allowed targets."""
    if not _truthy(payload.get("resolver_llm_fallback", payload.get("llm_fallback")), default=True):
        return None

    def choose_targets(resolver_payload):
        from model_loader import get_chat_llm

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
                    "You resolve a business rule to ontology targets. "
                    "Choose only from the provided target IRIs. "
                    "Return a JSON array of selected target strings and nothing else. "
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


def _rule_text(rule):
    lines = []
    if rule.number:
        lines.append(f"Rule number: {rule.number}")
    if rule.title:
        lines.append(f"Rule title: {rule.title}")
    lines.extend(["Business rule:", rule.text])
    return "\n".join(lines).strip()


def _semantic_payload(payload, ontology_content, ontology_terms):
    return {
        **payload,
        "ontology_terms": ontology_terms,
        "ontology_hash": hashlib.sha1(ontology_content.encode("utf-8")).hexdigest(),
        "embedding_model": payload.get("embedding_model") or "system.ai.qwen3-embedding-0-6b",
        "inference_config": _runtime_config(payload),
    }


def _prepare_rule_resolver_embeddings(payload, semantic_payload):
    """Prepare ontology embeddings for rule-target resolution when configured."""
    if not _truthy(payload.get("wait_embeddings"), default=True):
        return None

    from Logger import logger
    from find_relevant_terms import embedding_status, prepare_embeddings

    timeout_seconds = int(payload.get("embedding_timeout", 900))
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
            logger.info(f"[guide-rule] resolver embeddings {line}")
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


def _qname(graph, node):
    try:
        return graph.qname(node)
    except Exception:
        return str(node)


def _shape_subjects(shape, prefixes):
    graph = Graph(bind_namespaces="none")
    graph.parse(data=f"{prefixes or ''}\n{shape or ''}", format="turtle")
    property_shapes = []
    node_shapes = []
    for subject in graph.subjects(RDF.type, SH.PropertyShape):
        classes = [_qname(graph, cls) for cls in graph.objects(subject, SH.targetClass)]
        property_shapes.append({"shape": _qname(graph, subject), "target_classes": classes})
    for subject in graph.subjects(RDF.type, SH.NodeShape):
        classes = [_qname(graph, cls) for cls in graph.objects(subject, SH.targetClass)]
        node_shapes.append({"shape": _qname(graph, subject), "target_classes": classes})
    return property_shapes, node_shapes


def _local_name(value):
    value = str(value or "").rstrip("/#")
    return value.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or "Resource"


def _shape_namespace(graph):
    namespaces = dict(graph.namespace_manager.namespaces())
    return namespaces.get("shape") or namespaces.get("onto-sh") or namespaces.get("era-sh")


def _node_key(graph, node):
    try:
        return node.n3(graph.namespace_manager)
    except Exception:
        return str(node)


def _literal_number(value):
    if not isinstance(value, Literal):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _copy_bnode_closure(source_graph, target_graph, node, seen=None):
    seen = seen or set()
    if not isinstance(node, BNode) or node in seen:
        return
    seen.add(node)
    for predicate, obj in source_graph.predicate_objects(node):
        target_graph.add((node, predicate, obj))
        _copy_bnode_closure(source_graph, target_graph, obj, seen)


_STRUCTURAL_BNODE_CONSTRAINTS = {
    SH["and"],
    SH["or"],
    SH["xone"],
    SH["not"],
    SH.node,
    SH.property,
    SH.qualifiedValueShape,
}


def _bnode_has_shacl_predicate(source_graph, node):
    if not isinstance(node, BNode):
        return False
    return any(str(predicate).startswith(str(SH)) for predicate in source_graph.predicates(node, None))


def _merge_constraint_value(values, predicate, obj, logger):
    existing_keys = {_node_key(Graph(), value) for value in values}
    obj_key = _node_key(Graph(), obj)
    if obj_key in existing_keys:
        return values

    if predicate in {SH.minCount, SH.minLength, SH.minInclusive, SH.minExclusive}:
        obj_number = _literal_number(obj)
        current_numbers = [_literal_number(value) for value in values]
        if obj_number is not None and all(value is not None for value in current_numbers):
            best = max(values + [obj], key=lambda value: _literal_number(value))
            return [best]

    if predicate in {SH.maxCount, SH.maxLength, SH.maxInclusive, SH.maxExclusive}:
        obj_number = _literal_number(obj)
        current_numbers = [_literal_number(value) for value in values]
        if obj_number is not None and all(value is not None for value in current_numbers):
            best = min(values + [obj], key=lambda value: _literal_number(value))
            return [best]

    if predicate in {SH.datatype, SH["class"], SH.nodeKind, SH.severity, SH.node}:
        logger.warn(
            f"[guide-rule] conflicting {predicate} values while consolidating; keeping the first."
        )
        return values

    return values + [obj]


def _add_property_constraints(grouped, source_graph, bnode_graph, class_uri, path_uri, source_node, logger):
    path_constraints = grouped.setdefault(class_uri, {}).setdefault(path_uri, {})
    for predicate, obj in source_graph.predicate_objects(source_node):
        if predicate in {RDF.type, SH.path, SH.targetClass}:
            continue
        if (
            isinstance(obj, BNode)
            and predicate not in _STRUCTURAL_BNODE_CONSTRAINTS
            and _bnode_has_shacl_predicate(source_graph, obj)
        ):
            logger.warn(
                f"[guide-rule] dropping malformed blank-node value for {predicate}; "
                "SHACL constraint blank nodes are only kept for structural shape predicates."
            )
            continue
        if isinstance(obj, BNode):
            _copy_bnode_closure(source_graph, bnode_graph, obj)
        values = path_constraints.setdefault(predicate, [])
        path_constraints[predicate] = _merge_constraint_value(values, predicate, obj, logger)


def _collect_consolidation_input(shape_graph, bnode_graph, grouped, logger):
    for subject in shape_graph.subjects(RDF.type, SH.PropertyShape):
        paths = [value for value in shape_graph.objects(subject, SH.path) if isinstance(value, URIRef)]
        classes = [value for value in shape_graph.objects(subject, SH.targetClass) if isinstance(value, URIRef)]
        for class_uri in classes:
            for path_uri in paths:
                _add_property_constraints(grouped, shape_graph, bnode_graph, class_uri, path_uri, subject, logger)

    for subject in shape_graph.subjects(RDF.type, SH.NodeShape):
        classes = [value for value in shape_graph.objects(subject, SH.targetClass) if isinstance(value, URIRef)]
        for prop_node in shape_graph.objects(subject, SH.property):
            paths = [value for value in shape_graph.objects(prop_node, SH.path) if isinstance(value, URIRef)]
            for class_uri in classes:
                for path_uri in paths:
                    _add_property_constraints(grouped, shape_graph, bnode_graph, class_uri, path_uri, prop_node, logger)


def _strip_prefix_declarations(turtle):
    return str(turtle or "").strip()


def _consolidate_rule_shapes(generated_shapes, prefixes, shape_namespace="", shape_prefix="shape"):
    """Group generated PropertyShapes under NodeShapes by sh:targetClass."""
    from Logger import logger

    grouped = {}
    bnode_graph = Graph(bind_namespaces="none")
    consolidation = []
    namespace_source = Graph(bind_namespaces="none")
    namespace_source.parse(data=prefixes or "", format="turtle")

    for item in generated_shapes:
        shape = item.get("shape") or ""
        if not shape.strip() or not item.get("valid"):
            continue
        try:
            shape_graph = Graph(bind_namespaces="none")
            shape_graph.parse(data=f"{prefixes or ''}\n{shape}", format="turtle")
        except Exception as exc:
            item["consolidation_error"] = str(exc)
            continue

        for prefix, namespace in shape_graph.namespace_manager.namespaces():
            namespace_source.bind(prefix, namespace, replace=True)
            bnode_graph.bind(prefix, namespace, replace=True)
        _collect_consolidation_input(shape_graph, bnode_graph, grouped, logger)

    out_graph = Graph(bind_namespaces="none")
    for prefix, namespace in namespace_source.namespace_manager.namespaces():
        out_graph.bind(prefix, namespace, replace=True)
    shape_ns = URIRef(shape_namespace) if shape_namespace else _shape_namespace(namespace_source)
    if shape_ns is None:
        shape_ns = URIRef("urn:shape:")
    if shape_prefix:
        out_graph.bind(shape_prefix, shape_ns, override=True, replace=True)

    for class_uri in sorted(grouped, key=str):
        subject = URIRef(f"{shape_ns}{_local_name(class_uri)}Shape")
        out_graph.add((subject, RDF.type, SH.NodeShape))
        out_graph.add((subject, SH.targetClass, class_uri))
        path_map = grouped[class_uri]
        for path_uri in sorted(path_map, key=str):
            prop_node = BNode()
            out_graph.add((subject, SH.property, prop_node))
            out_graph.add((prop_node, SH.path, path_uri))
            for predicate in sorted(path_map[path_uri], key=str):
                for obj in path_map[path_uri][predicate]:
                    out_graph.add((prop_node, predicate, obj))
                    _copy_bnode_closure(bnode_graph, out_graph, obj)
            consolidation.append({
                "shape": _qname(out_graph, subject),
                "kind": "NodeShape",
                "target_class": _qname(out_graph, class_uri),
                "path": _qname(out_graph, path_uri),
            })

    node_shapes = _strip_prefix_declarations(out_graph.serialize(format="turtle"))
    return {
        "node_shapes": node_shapes,
        "property_shapes": "",
        "node_shape_map": {
            _qname(out_graph, class_uri): [_qname(out_graph, path_uri) for path_uri in sorted(paths, key=str)]
            for class_uri, paths in grouped.items()
        },
        "consolidation": consolidation,
    }


def _generate_property_first(payload):
    """Legacy non-streaming baseline: keep the current property-driven pipeline."""
    from rag_inmemory import build_inmemory_retriever_from_texts
    from multiagent import run_shacl_generation

    ontology_content = payload.get("ontology_content", "")
    onto = parse_ontology_graph(ontology_content, payload.get("ontology_filename", "ontology.ttl"))
    parsed_guide = payload.get("_business_rules") or parse_business_rules_template(
        payload.get("guide_content", ""),
        payload.get("guide_filename", ""),
    )
    chunks = business_rule_chunks(
        parsed_guide,
        domain_context=payload.get("domain_context", ""),
        generation_guidance=payload.get("generation_guidance", ""),
    )
    retriever = build_inmemory_retriever_from_texts(
        texts=chunks,
        embedding_model_id=payload.get("embedding_model") or "system.ai.qwen3-embedding-0-6b",
    )
    astrea_graph = _prepare_astrea_graph(payload)
    shacl = run_shacl_generation(
        ontology_graph=onto,
        astrea_graph=astrea_graph,
        retriever=retriever,
        llm_model_id=_llm_model_id(payload),
        temperature=float(payload.get("temperature", 0.5)),
    )
    return {
        "iteration_mode": LEGACY_ITERATION_MODE,
        "shape_document": shacl,
        "summary": {"mode": LEGACY_ITERATION_MODE},
        "unresolved_rules": [],
        "rules": [],
        "shapes": [],
    }


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
    """Generate guide shapes by resolving each business rule before generation."""
    from parse_ontology import parse_ontology
    from rule_target_resolver import DEFAULT_SEMANTIC_THRESHOLD, resolve_rule_target

    if shape_builder is None:
        from build_shacl_shapes import build_shape
        shape_builder = build_shape

    ontology_content = payload.get("ontology_content", "")
    ontology_filename = payload.get("ontology_filename", "ontology.ttl")
    guide_content = payload.get("guide_content", "")
    guide_filename = payload.get("guide_filename", "")

    parsed_ontology = parse_ontology(ontology_filename, ontology_content)
    astrea_graph = _prepare_astrea_graph(payload)
    ontology_terms = parsed_ontology.get("entities") or []
    prefixes = payload.get("prefixes") or parsed_ontology.get("prefixes") or ""
    base_ns = payload.get("base_namespace") or parsed_ontology.get("base_namespace") or ""
    shape_ns = payload.get("shape_namespace") or parsed_ontology.get("shape_namespace") or ""
    shape_prefix = payload.get("shape_prefix") or parsed_ontology.get("shape_prefix") or "shape"
    target_by_key = _target_lookup(ontology_terms)

    doc = parse_business_rules_document(guide_content, filename=guide_filename)
    rules = doc.rules
    if not rules:
        raise ValueError("The template is valid, but no business rule entries were found.")
    _emit(event_callback, {
        "type": "start",
        "iteration_mode": DEFAULT_ITERATION_MODE,
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
    llm = resolver_llm if resolver_llm is not None else _resolver_llm(payload)

    generated_shapes = []
    rule_rows = []
    unresolved = []

    for index, rule in enumerate(rules, start=1):
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
            top_k=max(1, min(50, int(payload.get("top_k", 10)))),
        )
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
                "resolved_by": resolution.resolved_by,
                "status": "skipped",
                "shape": "",
                "error": "Rule could not be resolved to an ontology target.",
                "attempts": 0,
                "business_rule": rule.text,
            })
            continue

        for target_index, target in enumerate(resolution.targets, start=1):
            term = target_by_key.get(str(target))
            if not term:
                unresolved.append({
                    "rule_number": rule.number,
                    "title": rule.title,
                    "text": rule.text,
                    "resolved_by": resolution.resolved_by,
                    "missing_target": target,
                    "candidates": resolution.candidates,
                })
                _emit(event_callback, {
                    "type": "shape",
                    "index": index,
                    "total": len(rules),
                    "target_index": target_index,
                    "target_total": len(resolution.targets),
                    "rule_number": rule.number,
                    "title": rule.title,
                    "target": target,
                    "property": target,
                    "resolved_by": resolution.resolved_by,
                    "status": "skipped",
                    "shape": "",
                    "error": f"Resolved target {target} was not found in the ontology catalog.",
                    "attempts": 0,
                    "business_rule": rule.text,
                })
                continue

            _emit(event_callback, {
                "type": "status",
                "stage": "generation",
                "current": index,
                "total": len(rules),
                "target_index": target_index,
                "target_total": len(resolution.targets),
                "rule_number": rule.number,
                "title": rule.title,
                "target": target,
                "message": f"Generating shape for rule {rule.number or index} target {target}",
            })
            shape_payload = {
                **payload,
                "business_rule": _rule_text(rule),
                "target": {**term, "ontology_filename": ontology_filename},
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
            shape_row = {
                "rule_number": rule.number,
                "rule_title": rule.title,
                "target": target,
                "target_type": term.get("type"),
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
                "target_index": target_index,
                "target_total": len(resolution.targets),
                "rule_number": rule.number,
                "title": rule.title,
                "target": target,
                "property": target,
                "target_type": term.get("type"),
                "resolved_by": resolution.resolved_by,
                "confidence": resolution.confidence,
                "status": _shape_status(result),
                "shape": result.get("shape", "") if result.get("valid") else "",
                "error": result.get("error") or result.get("message"),
                "error_type": result.get("error_type"),
                "attempts": result.get("attempts", 0),
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

    consolidated = _consolidate_rule_shapes(
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
    summary = {
        "mode": DEFAULT_ITERATION_MODE,
        "rules_total": len(rules),
        "rules_unresolved": len(unresolved),
        "targets_total": len(generated_shapes),
        "valid": valid_count,
        "invalid": invalid_count,
    }
    _emit(event_callback, {
        "type": "done",
        "iteration_mode": DEFAULT_ITERATION_MODE,
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
        "iteration_mode": DEFAULT_ITERATION_MODE,
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


def generate_guide_shapes(payload, *, semantic_ranker=None, resolver_llm=None, shape_builder=None, event_callback=None):
    """Generate SHACL shapes from a guide using rule-first or legacy property-first iteration."""
    iteration_mode = str(payload.get("iteration_mode") or DEFAULT_ITERATION_MODE).strip().lower()
    if iteration_mode == LEGACY_ITERATION_MODE:
        return _generate_property_first(payload)
    if iteration_mode != DEFAULT_ITERATION_MODE:
        raise ValueError("iteration_mode must be 'rule' or 'property'.")
    return _generate_rule_first(
        payload,
        semantic_ranker=semantic_ranker,
        resolver_llm=resolver_llm,
        shape_builder=shape_builder,
        event_callback=event_callback,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        send_options(self)

    def do_GET(self):
        self.request_id = new_request_id(self.headers)
        if self.path == "/health":
            send_health(self, "generate-from-guide", request_id=self.request_id)
            return
        send_json(self, 404, {"error": "unknown endpoint"}, request_id=self.request_id)

    def _sse(self, event: dict):
        event.setdefault("request_id", getattr(self, "request_id", None))
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def do_POST(self):
        self.request_id = new_request_id(self.headers)
        if self.path != "/generate-from-guide":
            send_json(self, 404, {"error": "unknown endpoint"}, request_id=self.request_id)
            return

        try:
            payload = read_json(self)
        except ValueError as exc:
            send_json(self, 400, {"error": str(exc)}, request_id=self.request_id)
            return
        if reject_disabled_provider(self, payload, request_id=self.request_id):
            return
        if not payload.get("ontology_content"):
            send_json(self, 400, {"error": "Missing ontology_content."}, request_id=self.request_id)
            return
        try:
            parsed_guide = parse_business_rules_template(
                payload.get("guide_content", ""),
                payload.get("guide_filename", ""),
            )
            payload["_business_rules"] = parsed_guide
            payload["_business_rules_html"] = business_rules_preview_html(parsed_guide)
        except Exception as exc:
            send_json(self, 400, {"error": str(exc)}, request_id=self.request_id)
            return
        try:
            parse_ontology_graph(
                payload.get("ontology_content", ""),
                payload.get("ontology_filename", "ontology.ttl"),
            )
        except Exception as exc:
            send_json(self, 400, {"error": str(exc)}, request_id=self.request_id)
            return
        try:
            _prepare_astrea_graph(payload)
        except Exception as exc:
            send_json(self, 400, {"error": str(exc)}, request_id=self.request_id)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Request-ID", self.request_id)
        self._cors()
        self.end_headers()

        try:
            from runtime_config import inference_config
            from Logger import logger
            with logger.request_context(self.request_id), inference_config(_runtime_config(payload)):
                self._run(payload)
        except Exception as exc:
            self._sse({"type": "error", "message": str(exc),
                       "trace": traceback.format_exc()[-1500:]})

    def _run(self, payload):
        ontology_content = payload.get("ontology_content", "")
        iteration_mode = str(payload.get("iteration_mode") or DEFAULT_ITERATION_MODE).strip().lower()

        self._sse({"type": "status", "stage": "parsing", "message": "Parsing ontology…"})
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

        parsed_guide = payload.get("_business_rules") or parse_business_rules_template(
            payload.get("guide_content", ""),
            payload.get("guide_filename", ""),
        )
        chunks = business_rule_chunks(
            parsed_guide,
            domain_context=payload.get("domain_context", ""),
            generation_guidance=payload.get("generation_guidance", ""),
        )
        self._sse({"type": "status", "stage": "template",
                   "current": len(chunks), "total": len(chunks),
                   "message": f"Validated Business Rules template: {len(chunks)} rule(s)."})

        if iteration_mode == DEFAULT_ITERATION_MODE:
            payload.setdefault("prefixes", prefixes)
            payload.setdefault("base_namespace", base_ns)
            payload.setdefault("shape_namespace", shape_ns)
            payload.setdefault("shape_prefix", shape_prefix)
            self._sse({"type": "status", "stage": "preprocessing",
                       "current": 0, "total": len(chunks),
                       "message": "Preparing rule-first generation…"})
            generate_guide_shapes(payload, event_callback=self._sse)
            return

        if iteration_mode != LEGACY_ITERATION_MODE:
            raise ValueError("iteration_mode must be 'rule' or 'property'.")

        from rag_inmemory import build_inmemory_retriever_from_texts
        from multiagent_stream import stream_shacl_generation

        astrea_graph = _prepare_astrea_graph(payload)

        self._sse({"type": "status", "stage": "preprocessing",
                   "current": 0, "total": len(chunks),
                   "message": "Indexing business rules…"})
        retriever = build_inmemory_retriever_from_texts(
            texts=chunks,
            embedding_model_id=payload.get("embedding_model") or "system.ai.qwen3-embedding-0-6b",
        )
        self._sse({"type": "status", "stage": "preprocessing",
                   "current": len(chunks), "total": len(chunks),
                   "message": f"Indexed {len(chunks)} business rule(s)."})

        # --- Stream generation --------------------------------------------- #
        legacy_prefixes = ensure_legacy_era_aliases(prefixes, base_ns, shape_ns)
        for event in stream_shacl_generation(
            ontology_graph=onto,
            retriever=retriever,
            llm_model_id=payload.get("llm_model") or "system.ai.gemma-3-12b",
            temperature=float(payload.get("temperature", 0.5)),
            astrea_graph=astrea_graph,
            base_namespace=base_ns,
            prefix_block=legacy_prefixes,
        ):
            self._sse(event)


if __name__ == "__main__":
    print(f"generate-from-guide service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
