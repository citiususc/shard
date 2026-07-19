"""
multiagent_stream.py  (br2shacl-ui)

Streaming, generic variant of multiagent.run_shacl_generation.

Differences from the vendored multiagent.run_shacl_generation:

  * STREAMING — instead of accumulating every shape and returning one big Turtle
    string at the end, it is a generator that *yields* one event per property as
    soon as that property's shape is produced, so the human-in-the-loop review
    queue fills up while generation continues in the background.

  * EMIT-ON-FAILURE — the original generator silently drops a property whose
    output never parses after N attempts. Here, when the 10th attempt still fails
    to parse, the shape is yielded anyway with status="invalid" and the rdflib
    parser error attached, so the human can fix it by hand.

  * GENERIC — the ERA namespace and ERA prefix block are no longer hard-coded.
    The caller passes the ontology base namespace and an (editable) prefix block,
    so any uploaded ontology works.

The four evidence-gathering agents (Astrea, Ontology, RAG, Evaluator) and the
graph control flow are reused verbatim from the vendored multiagent module; only
the generator step and the per-property driver are reimplemented to add the hooks
above.
"""

from __future__ import annotations

import gc
from typing import Dict, Iterator, List, Optional

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, SH

from langchain_core.output_parsers import StrOutputParser

from model_loader import get_chat_llm, DEFAULT_EVAL_MAX_NEW_TOKENS, DEFAULT_GEN_MAX_NEW_TOKENS

from multiagent import (
    _ShaclHistoryQueue,
    _AgentState,
    _astrea_agent,
    _ontology_agent,
    _rag_agent,
    _evaluator_agent,
)
from prompts import load_prompt_from_json
from utils import (
    clean_shacl_response,
    get_owl_properties_with_domain,
    update_node_shapes,
    generate_node_shapes_str,
)
import ns_utils
from Logger import logger

try:
    import torch
except Exception:
    torch = None


_MAX_GEN_RETRIES = 10
_MAX_EVAL_ITERATIONS = 3


def _extract_name_and_class(shape_str: str, prefixes: str, base_ns: str):
    """
    Generic version of utils.extract_name_and_class_from_shape.

    Returns (property_shape_qname, [affected_class_qname, ...]) reading the
    affectedClass predicate from the ontology *base* namespace rather than the
    hard-coded ERA namespace, so it works for any ontology.
    """
    g = Graph()
    g.parse(data=f"{prefixes}\n{shape_str}", format="turtle")
    affected_class = URIRef(base_ns + "affectedClass")

    for s in g.subjects(RDF.type, SH.PropertyShape):
        try:
            shape_qname = g.qname(s)
        except Exception:
            shape_qname = str(s)
        classes = []
        for c in g.objects(s, affected_class):
            try:
                classes.append(g.qname(c))
            except Exception:
                classes.append(str(c))
        return shape_qname, classes
    return None, []


def _gather_evidence(
    prop: str,
    ontology_graph: Graph,
    astrea_graph: Optional[Graph],
    retriever,
    eval_model,
    prompt_file: str,
    property_shapes: str,
    shacl_prefixes: str,
    shacl_history: _ShaclHistoryQueue,
    node_shapes: Dict[str, List[str]],
) -> _AgentState:
    """
    Run the evidence-gathering subgraph (Astrea → Ontology → RAG → Evaluator loop)
    exactly as multiagent._build_graph wires it, returning the final state ready
    for generation.
    """
    state = _AgentState(
        property=prop,
        new_queries=[prop],
        ontology_info="",
        rag="",
        astrea_shapes="",
        is_complete="no",
        property_shapes=property_shapes,
        shacl_prefixes=shacl_prefixes,
        shacl_history=shacl_history,
        node_shapes=node_shapes,
        iterations=0,
    )

    if astrea_graph is not None:
        state = _astrea_agent(state, astrea_graph, ontology_graph)

    while True:
        state = _ontology_agent(state, ontology_graph)
        if state["iterations"] == 0:
            state = _rag_agent(state, ontology_graph, retriever)
        state = _evaluator_agent(state, eval_model, prompt_file)
        if state["is_complete"] == "yes" or state["iterations"] >= _MAX_EVAL_ITERATIONS:
            break
    return state


def _extract_business_rule_context(rag_text: str) -> str:
    """Return the first business-rule chunk retrieved for a generated shape."""
    text = (rag_text or "").strip()
    if not text:
        return ""

    chunks = [c.strip() for c in text.split("BUSINESS RULE TEMPLATE ENTRY") if c.strip()]
    chunk = chunks[0] if chunks else text
    lines = [line.rstrip() for line in chunk.splitlines()]

    number = ""
    title = ""
    business_lines: List[str] = []
    in_business_rule = False

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("rule number:"):
            number = stripped.split(":", 1)[1].strip()
            continue
        if lower.startswith("rule title:"):
            title = stripped.split(":", 1)[1].strip()
            continue
        if lower == "business rule:":
            in_business_rule = True
            continue
        if in_business_rule:
            business_lines.append(line)

    business_rule = "\n".join(business_lines).strip()
    if business_rule:
        heading = " — ".join(part for part in (number, title) if part)
        return f"{heading}\n\n{business_rule}".strip() if heading else business_rule

    return chunk[:2000]


def _generate_shape(state: _AgentState, gen_model, prompt_file: str, base_ns: str):
    """
    Generation step with emit-on-failure semantics.

    Returns a dict:
        {status: "valid"|"invalid"|"skipped",
         shape: str, error: str|None, attempts: int,
         property_name: str|None, affected_classes: list[str]}
    """
    attempt = 0
    error_message: Optional[str] = None
    last_result: str = ""
    has_astrea = bool(state["astrea_shapes"])

    while attempt < _MAX_GEN_RETRIES:
        if error_message and has_astrea:
            prompt_key = "generator_agent_property_with_error"
        elif error_message and not has_astrea:
            prompt_key = "generator_agent_property_without_astrea_with_error"
        elif not error_message and has_astrea:
            prompt_key = "generator_agent_property"
        else:
            prompt_key = "generator_agent_property_without_astrea"

        prompt = load_prompt_from_json(prompt_file, prompt_key)
        response = prompt | gen_model | StrOutputParser()

        invoke_vars = {
            "property":      state["property"],
            "prefixes":      state["shacl_prefixes"],
            "ontology_info": state["ontology_info"],
            "rag":           state["rag"],
            "shacl_history": str(state["shacl_history"]),
        }
        if has_astrea:
            invoke_vars["astrea_shapes"] = state["astrea_shapes"]
        if error_message:
            invoke_vars["previous_invalid_shapes"] = last_result or ""
            invoke_vars["error"] = error_message

        result = response.invoke(invoke_vars)
        last_result = clean_shacl_response(result)

        if "SHACL shapes not found" in result:
            return {
                "status": "skipped", "shape": "", "error": None,
                "attempts": attempt + 1, "property_name": None, "affected_classes": [],
            }

        try:
            Graph().parse(data=f"{state['shacl_prefixes']}\n{last_result}", format="turtle")
        except Exception as e:
            error_message = str(e)
            attempt += 1
            continue

        # Valid Turtle
        prop_name, affected = _extract_name_and_class(last_result, state["shacl_prefixes"], base_ns)
        return {
            "status": "valid", "shape": last_result, "error": None,
            "attempts": attempt + 1, "property_name": prop_name, "affected_classes": affected,
        }

    # Exhausted all retries — emit the last (invalid) result with the parse error.
    return {
        "status": "invalid", "shape": last_result, "error": error_message,
        "attempts": _MAX_GEN_RETRIES, "property_name": None, "affected_classes": [],
    }


def stream_shacl_generation(
    ontology_graph: Graph,
    retriever,
    llm_model_id: str,
    temperature: float = 0.5,
    prompting_technique: str = "multiagent",
    astrea_graph: Optional[Graph] = None,
    base_namespace: Optional[str] = None,
    prefix_block: Optional[str] = None,
    prompt_file: Optional[str] = None,
) -> Iterator[dict]:
    """
    Generator yielding one event per ontology property.

    First event:   {"type": "start",    "total": Y, "prefixes": "<@prefix block>"}
    Per property:  {"type": "shape",     "index": n, "total": Y, "property": uri,
                    "status": "valid"|"invalid"|"skipped", "shape": ttl,
                    "error": str|None, "attempts": k}
    Final event:   {"type": "done",      "total": Y, "valid": v, "invalid": i,
                    "skipped": s, "node_shapes": "<turtle>"}

    The shapes are yielded WITHOUT the prefix block (prefixes are managed once, in
    the editable UI panel). node_shapes is the aggregated sh:NodeShape block, as
    in the original pipeline's final assembly.
    """
    import os as _os
    if prompt_file is None:
        prompt_file = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                    "prompts", f"{prompting_technique}.json")

    base_ns = base_namespace or ns_utils.derive_base_namespace(ontology_graph)
    prefixes = prefix_block or ns_utils.build_prefix_block(ontology_graph, base_ns)
    prefix_namespaces = dict(ns_utils.split_prefix_block(prefixes))
    shape_ns = prefix_namespaces.get("shape") or ns_utils.shapes_namespace(base_ns)
    prefixes = ns_utils.ensure_legacy_era_aliases(prefixes, base_ns, shape_ns)

    logger.info(f"[stream] base_namespace={base_ns}")

    eval_model = get_chat_llm(llm_model_id, kind="evaluator", temperature=temperature,
                              max_new_tokens=DEFAULT_EVAL_MAX_NEW_TOKENS)
    gen_model = get_chat_llm(llm_model_id, kind="generator", temperature=temperature,
                             max_new_tokens=DEFAULT_GEN_MAX_NEW_TOKENS)

    properties = get_owl_properties_with_domain(ontology_graph, namespace=base_ns)
    total = len(properties)
    logger.info(f"[stream] {total} property(ies) with domain under {base_ns}.")

    yield {"type": "start", "total": total, "prefixes": prefixes, "base_namespace": base_ns}

    property_shapes = ""
    shacl_history = _ShaclHistoryQueue(maxlen=5, shacl_prefixes=prefixes)
    node_shapes: Dict[str, List[str]] = {}
    counts = {"valid": 0, "invalid": 0, "skipped": 0}

    for idx, prop in enumerate(properties, start=1):
        logger.info(f"[stream] property {idx}/{total}: {prop}")
        try:
            state = _gather_evidence(
                prop, ontology_graph, astrea_graph, retriever, eval_model,
                prompt_file, property_shapes, prefixes, shacl_history, node_shapes,
            )
            business_rule_context = _extract_business_rule_context(state.get("rag", ""))
            result = _generate_shape(state, gen_model, prompt_file, base_ns)
        except Exception as e:  # never let one property kill the stream
            logger.error(f"[stream] property {prop} crashed: {e}")
            business_rule_context = ""
            result = {"status": "invalid", "shape": "", "error": f"pipeline error: {e}",
                      "attempts": 0, "property_name": None, "affected_classes": []}

        counts[result["status"]] = counts.get(result["status"], 0) + 1

        if result["status"] == "valid":
            property_shapes = f"{property_shapes}\n\n{result['shape']}"
            shacl_history.add(result["shape"])
            if result["property_name"]:
                node_shapes = update_node_shapes(
                    node_shapes, result["affected_classes"], result["property_name"]
                )

        yield {
            "type": "shape",
            "index": idx,
            "total": total,
            "property": prop,
            "status": result["status"],
            "shape": result["shape"],
            "error": result["error"],
            "attempts": result["attempts"],
            "business_rule": business_rule_context,
        }

        gc.collect()
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    yield {
        "type": "done",
        "total": total,
        "valid": counts["valid"],
        "invalid": counts["invalid"],
        "skipped": counts["skipped"],
        "node_shapes": generate_node_shapes_str(node_shapes),
    }
