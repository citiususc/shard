# multiagent.py
from __future__ import annotations

import re
import gc
import os

try:                      # br2shacl-ui: torch is only needed for local HF inference.
    import torch          # In the Databricks-only demo it may be absent.
except Exception:
    torch = None

from collections import deque
from typing import Dict, TypedDict, Literal, List, Optional

from rdflib import Graph, URIRef, BNode
from rdflib.namespace import SH

from langgraph.graph import StateGraph, END
from langchain_core.output_parsers import StrOutputParser

from model_loader import (
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_TEMPERATURE,
    DEFAULT_EVAL_MAX_NEW_TOKENS,
    DEFAULT_GEN_MAX_NEW_TOKENS,
    get_chat_llm,
)

from prompts import load_prompt_from_json
from utils import *
from Logger import logger


# ---------------------------------------------------------------------------
# Paths — anchored to project root so the module works regardless of the
# working directory from which the process is launched.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROMPTS_DIR  = os.path.join(_PROJECT_ROOT, "src", "prompts")


# ---------------------------------
# Queue to keep track of SHACL shapes history
# ---------------------------------
class _ShaclHistoryQueue:
    def __init__(self, maxlen: int = 5, shacl_prefixes: str = ""):
        self.queue = deque(maxlen=maxlen)
        self.shacl_prefixes = shacl_prefixes.strip()

    def add(self, shape: str):
        self.queue.append(shape)

    def get_all(self) -> list:
        return list(self.queue)

    def to_string(self) -> str:
        """Return history content only — WITHOUT prefixes — for use in prompts."""
        return "\n\n".join(self.queue) if self.queue else ""

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f"_ShaclHistoryQueue({list(self.queue)}, maxlen={self.queue.maxlen})"


# ---------------------------------
# Agent state definition
# ---------------------------------
class _AgentState(TypedDict):
    property: str
    new_queries: List[str]
    ontology_info: str
    rag: str
    astrea_shapes: str
    is_complete: Literal["yes", "no"]
    property_shapes: str
    shacl_history: _ShaclHistoryQueue
    shacl_prefixes: str
    node_shapes: Dict[str, List[str]]
    iterations: int


# ---------------------------------
# Astrea Agent
# ---------------------------------
def _astrea_agent(state: _AgentState, astrea_graph: Graph, ontology_graph: Graph) -> _AgentState:
    if astrea_graph is None:
        logger.debug("Astrea Agent: no Astrea graph provided — skipping.")
        return {**state, "astrea_shapes": ""}

    prop_uri   = URIRef(state["property"])
    logger.info(f"Astrea Agent: retrieving Astrea shapes for property {state['property']}")
    shape_uris = list(astrea_graph.subjects(SH.path, prop_uri))

    if not shape_uris:
        logger.debug(f"Astrea Agent: no direct Astrea PropertyShapes found for {state['property']}")
        return {**state, "astrea_shapes": ""}

    logger.debug(
        f"Astrea Agent: found {len(shape_uris)} direct Astrea shape node(s) for {state['property']}"
    )

    subgraph = Graph()
    for prefix, ns in astrea_graph.namespaces():
        subgraph.bind(prefix, ns)

    visited: set = set()

    def _copy(subject):
        for p, o in astrea_graph.predicate_objects(subject):
            subgraph.add((subject, p, o))
            if isinstance(o, BNode) and o not in visited:
                visited.add(o)
                _copy(o)

    for uri in shape_uris:
        _copy(uri)

    serialized = subgraph.serialize(format="turtle").strip()
    logger.debug(
        f"Astrea Agent: serialized Astrea context for {state['property']} "
        f"({len(serialized)} chars)"
    )
    return {**state, "astrea_shapes": serialized}


# ---------------------------------
# Ontology Agent
# ---------------------------------
def _ontology_agent(state: _AgentState, ontology_graph: Graph) -> _AgentState:
    subjects      = list(state["new_queries"])
    ontology_info = state["ontology_info"]

    logger.info(
        f"Ontology Agent: processing property {state['property']} "
        f"(iteration={state['iterations']}, pending_queries={len(subjects)})"
    )
    logger.debug(f"Ontology Agent: pending queries -> {subjects}")

    if not subjects:
        logger.debug("Ontology Agent: no pending ontology queries.")
        return {**state, "new_queries": []}

    if state["iterations"] == 0:
        prop      = subjects[0]
        prop_info = get_info_by_name(ontology_graph, prop)
        if prop_info is None:
            logger.warn(f"Ontology Agent: no ontology info found for property {prop}")
            return {**state, "new_queries": subjects[1:]}

        ontology_info += f"# {prop}\n{prop_info}\n\n"
        logger.debug(f"Ontology Agent: added base ontology info for property {prop}")

        prop_domain = get_property_domain(ontology_graph, prop)
        if prop_domain:
            ontology_info += f"Domain of {prop}: {prop_domain}\n\n"
            logger.debug(
                f"Ontology Agent: found {len(prop_domain)} domain class(es) for {prop}: {prop_domain}"
            )
        else:
            logger.debug(f"Ontology Agent: no explicit domain found for property {prop}")

        for owl_class in prop_domain:
            owl_class_info = get_info_by_name(ontology_graph, owl_class)
            if owl_class_info:
                ontology_info += f"## {owl_class}\n{owl_class_info}\n\n"
                logger.debug(f"Ontology Agent: added ontology info for domain class {owl_class}")
            else:
                logger.debug(f"Ontology Agent: no ontology info found for domain class {owl_class}")

        subjects = subjects[1:]

    else:
        logger.debug("Ontology Agent: processing follow-up ontology queries.")
        for subject in subjects:
            subject_info = get_info_by_name(ontology_graph, subject)
            if subject_info:
                ontology_info += f"# {subject}\n{subject_info}\n\n"
                logger.debug(f"Ontology Agent: added ontology info for follow-up subject {subject}")
            else:
                logger.warn(f"Ontology Agent: no ontology info found for follow-up subject {subject}")
        subjects = []

    logger.debug(
        f"Ontology Agent: accumulated ontology context length = {len(ontology_info)} chars"
    )
    return {**state, "ontology_info": ontology_info, "new_queries": subjects}


# ---------------------------------
# RAG Agent
# ---------------------------------
def _rag_agent(state: _AgentState, ontology_graph: Graph, retriever) -> _AgentState:
    logger.info(f"RAG Agent: fetching documents for entity {state['property']}")

    query = get_entity_label_and_description(ontology_graph, state["property"])
    logger.debug(f"RAG Agent: query type={type(query).__name__}, length={len(query) if query else 0}")
    logger.debug(f"RAG Agent: query content -> {query!r}")
    logger.debug(f"RAG Agent: query -> {query}")

    vs = retriever.vectorstore
    try:
        vs_count = vs._collection.count()
        logger.debug(f"RAG Agent: Chroma collection has {vs_count} documents")
    except Exception as e:
        logger.warn(f"RAG Agent: could not count Chroma docs: {e}")

    raw_hits = []
    try:
        raw_hits = vs.similarity_search(query, k=4)
        logger.debug(f"RAG Agent: Chroma similarity_search returned {len(raw_hits)} raw hits")
        for i, h in enumerate(raw_hits):
            doc_id = h.metadata.get("doc_id", "<NO doc_id>")
            logger.debug(f"  hit {i}: doc_id={doc_id} | content preview: {h.page_content[:80]!r}")
    except Exception as e:
        logger.warn(f"RAG Agent: similarity_search failed: {e}")

    if raw_hits:
        ids       = [h.metadata.get("doc_id") for h in raw_hits if h.metadata.get("doc_id")]
        originals = retriever.docstore.mget(ids)
        for doc_id, orig in zip(ids, originals):
            status = "FOUND" if orig is not None else "MISSING"
            logger.debug(f"  docstore lookup {doc_id}: {status}")

    docs = retriever.invoke(query)

    context_text = ""
    doc_count    = 0
    if docs:
        for doc in docs:
            doc_count += 1
            if isinstance(doc, bytes):
                context_text += doc.decode("utf-8", errors="ignore") + "\n"
            elif hasattr(doc, "page_content"):
                context_text += str(doc.page_content) + "\n"
            else:
                context_text += str(doc) + "\n"

    logger.debug(
        f"RAG Agent: retrieved {doc_count} document(s) for {state['property']} "
        f"({len(context_text.strip())} chars of context)"
    )

    return {**state, "rag": context_text.strip()}


# ---------------------------------
# Evaluator Agent
# ---------------------------------
def _evaluator_agent(state: _AgentState, eval_model, prompt_file: str) -> _AgentState:
    logger.info(
        f"Evaluator Agent: evaluating completeness for {state['property']} "
        f"(iteration={state['iterations']})"
    )
    logger.debug(
        f"Evaluator Agent: ontology chars={len(state['ontology_info'])}, "
        f"astrea chars={len(state['astrea_shapes'])}, rag chars={len(state['rag'])}"
    )

    prompt   = load_prompt_from_json(prompt_file, "evaluator_agent")
    response = prompt | eval_model | StrOutputParser()

    result_raw = response.invoke({
        "property":     state["property"],
        "ontology":     state["ontology_info"],
        "astrea_shapes": state["astrea_shapes"],
        "rag":          state["rag"],
    }).strip()

    logger.debug(f"Evaluator Agent raw output for {state['property']}: {result_raw}")

    result_lower = result_raw.lower()

    if result_lower == "yes" or result_lower.startswith("yes"):
        is_complete             = "yes"
        new_queries: List[str] = []
    else:
        is_complete = "no"
        matches     = re.findall(r"\[([^\]]+)\]", result_raw)
        if matches:
            new_queries = [
                item.strip().strip('"').strip("'")
                for item in matches[0].split(",")
                if item.strip()
            ]
        else:
            new_queries = []

    logger.info(
        f"Evaluator Agent result for {state['property']}: {is_complete} "
        f"(new_queries={new_queries}, next_iteration={state['iterations'] + 1})"
    )

    return {
        **state,
        "new_queries": new_queries,
        "is_complete": is_complete,
        "iterations":  state["iterations"] + 1,
    }


# ---------------------------------
# Generator Agent
# ---------------------------------
def _generator_agent(state: _AgentState, gen_model, prompt_file: str) -> _AgentState:
    max_retries              = 10
    attempt                  = 0
    error_message: Optional[str] = None
    last_result:   Optional[str] = None

    logger.info(
        f"Generator Agent: generating SHACL restrictions for {state['property']} "
        f"(max_retries={max_retries})"
    )
    logger.debug(
        f"Generator Agent input sizes for {state['property']}: "
        f"ontology={len(state['ontology_info'])}, "
        f"astrea={len(state['astrea_shapes'])}, "
        f"rag={len(state['rag'])}, "
        f"history={len(str(state['shacl_history']))}"
    )

    while attempt < max_retries:
        logger.info(
            f"Generator Agent attempt {attempt + 1}/{max_retries} for {state['property']}"
        )

        has_astrea = bool(state["astrea_shapes"])

        if error_message and has_astrea:
            prompt_key = "generator_agent_property_with_error"
        elif error_message and not has_astrea:
            prompt_key = "generator_agent_property_without_astrea_with_error"
        elif not error_message and has_astrea:
            prompt_key = "generator_agent_property"
        else:
            prompt_key = "generator_agent_property_without_astrea"

        if error_message:
            logger.debug(f"Generator Agent: retrying with previous parse error: {error_message}")

        prompt   = load_prompt_from_json(prompt_file, prompt_key)
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
            invoke_vars["error"]                   = error_message

        result      = response.invoke(invoke_vars)
        last_result = clean_shacl_response(result)

        logger.debug(f"Generator Agent raw output for {state['property']}:\n{result}")

        if "SHACL shapes not found" in result:
            logger.debug(
                f"Generator Agent: model reported 'SHACL shapes not found' for {state['property']}"
            )
            return {**state}

        logger.debug(
            f"Generator Agent cleaned output length for {state['property']}: {len(last_result)} chars"
        )

        try:
            temp_graph   = Graph()
            combined_ttl = f"{state['shacl_prefixes']}\n{last_result}"
            temp_graph.parse(data=combined_ttl, format="turtle")
            logger.debug(
                f"Generator Agent: Turtle validation succeeded for {state['property']}"
            )
        except Exception as e:
            error_message = str(e)
            logger.warn(
                f"Generator Agent: invalid Turtle on attempt {attempt + 1}/{max_retries} "
                f"for {state['property']}: {error_message}"
            )
            logger.debug(
                f"Generator Agent: invalid SHACL shapes: {last_result}/{max_retries} "
                f"for {state['property']}: {error_message}"
            )
            attempt += 1
            continue

        state["shacl_history"].add(last_result)
        property_name, affected_classes = extract_name_and_class_from_shape(
            last_result, state["shacl_prefixes"]
        )
        new_node_shapes = update_node_shapes(
            state["node_shapes"], affected_classes, property_name
        )

        logger.info(f"Generator Agent: valid SHACL generated for {state['property']}")
        logger.debug(f"Generator Agent final SHACL for {state['property']}:\n{last_result}")
        logger.debug(
            f"Generator Agent: property_name={property_name}, affected_classes={affected_classes}"
        )

        return {
            **state,
            "property_shapes": f"{state['property_shapes']}\n\n{last_result}",
            "node_shapes":     new_node_shapes,
        }

    logger.error(
        f"Generator Agent: reached maximum {max_retries} attempts for {state['property']}"
    )
    logger.debug(f"Generator Agent last raw result for {state['property']}:\n{last_result}")
    return {**state}


# ---------------------------------
# Build LangGraph workflow
# ---------------------------------
def _build_graph(
    ontology_graph: Graph,
    astrea_graph: Optional[Graph],
    retriever,
    eval_model,
    gen_model,
    prompt_file: str,
):
    logger.debug("Building LangGraph workflow for multi-agent SHACL generation.")

    builder = StateGraph(_AgentState)

    builder.add_node("OntologyAgent",  lambda state: _ontology_agent(state, ontology_graph))
    builder.add_node("RAGAgent",       lambda state: _rag_agent(state, ontology_graph, retriever))
    builder.add_node("EvaluatorAgent", lambda state: _evaluator_agent(state, eval_model, prompt_file))
    builder.add_node("GeneratorAgent", lambda state: _generator_agent(state, gen_model, prompt_file))

    if astrea_graph is not None:
        logger.debug("LangGraph: including AstreaAgent (astrea_graph provided).")
        builder.add_node(
            "AstreaAgent",
            lambda state: _astrea_agent(state, astrea_graph, ontology_graph),
        )
        builder.set_entry_point("AstreaAgent")
        builder.add_edge("AstreaAgent", "OntologyAgent")
    else:
        logger.debug("LangGraph: skipping AstreaAgent (astrea_graph is None).")
        builder.set_entry_point("OntologyAgent")

    builder.add_conditional_edges(
        "OntologyAgent",
        lambda state: "EvaluatorAgent" if state["iterations"] > 0 else "RAGAgent",
    )
    builder.add_edge("RAGAgent", "EvaluatorAgent")

    builder.add_conditional_edges(
        "EvaluatorAgent",
        lambda state: (
            "GeneratorAgent"
            if (state["is_complete"] == "yes" or state["iterations"] >= 3)
            else "OntologyAgent"
        ),
    )
    builder.add_edge("GeneratorAgent", END)

    logger.debug("LangGraph workflow compiled successfully.")
    return builder.compile()


# ---------------------------------
# Main orchestrator
# ---------------------------------
def run_shacl_generation(
    ontology_graph: Graph,
    astrea_graph: Optional[Graph],
    retriever,
    llm_model_id: str = DEFAULT_LLM_MODEL_ID,
    temperature: float = DEFAULT_TEMPERATURE,
    prompting_technique: str = "multiagent",
    eval_max_new_tokens: int = DEFAULT_EVAL_MAX_NEW_TOKENS,
    gen_max_new_tokens: int  = DEFAULT_GEN_MAX_NEW_TOKENS,
) -> str:
    """
    Generate SHACL constraints via a multiagent LangGraph pipeline.

    Parameters
    ----------
    ontology_graph      : parsed RDFLib Graph of the ERA ontology
    astrea_graph        : parsed RDFLib Graph of Astrea-generated baseline shapes,
                          or None to run without the Astrea agent
    retriever           : LangChain MultiVectorRetriever from rag.load_retriever()
    llm_model_id        : model ID for evaluator and generator (Databricks or HF)
    temperature         : sampling temperature (0 = greedy)
    prompting_technique : stem of the prompt file under src/prompts/ (without .json)
    eval_max_new_tokens : token budget for the evaluator
    gen_max_new_tokens  : token budget for the generator
    """
    logger.info("Starting multi-agent SHACL generation.")
    logger.debug(
        f"llm_model_id='{llm_model_id}', temperature={temperature:.2f}, "
        f"prompting_technique='{prompting_technique}', "
        f"astrea_graph={'provided' if astrea_graph is not None else 'None'}, "
        f"eval_max_new_tokens={eval_max_new_tokens}, gen_max_new_tokens={gen_max_new_tokens}"
    )

    prompt_file = os.path.join(_PROMPTS_DIR, f"{prompting_technique}.json")
    logger.debug(f"Resolved prompt file: {prompt_file}")

    eval_model = get_chat_llm(
        llm_model_id=llm_model_id,
        kind="evaluator",
        temperature=temperature,
        max_new_tokens=eval_max_new_tokens,
    )
    gen_model = get_chat_llm(
        llm_model_id=llm_model_id,
        kind="generator",
        temperature=temperature,
        max_new_tokens=gen_max_new_tokens,
    )

    logger.debug("Evaluator and generator models loaded successfully.")

    multiagent_graph = _build_graph(
        ontology_graph, astrea_graph, retriever, eval_model, gen_model, prompt_file
    )

    shacl_prefixes = """\
@prefix era: <http://data.europa.eu/949/> .
@prefix era-sh: <http://data.europa.eu/949/shapes/> .
@prefix geosparql: <http://www.opengis.net/ont/geosparql#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix time: <http://www.w3.org/2006/time#> .
@prefix wgs1: <http://www.w3.org/2003/01/geo/wgs84_pos#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix org: <http://www.w3.org/ns/org#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> ."""

    property_shapes = ""
    shacl_history   = _ShaclHistoryQueue(maxlen=5, shacl_prefixes=shacl_prefixes)
    node_shapes: Dict[str, List[str]] = {}

    properties = get_owl_properties_with_domain(ontology_graph)
    logger.info(f"Found {len(properties)} ontology property(ies) with domain.")

    for idx, prop in enumerate(properties, start=1):
        logger.info(f"Processing property {idx}/{len(properties)}: {prop}")
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
        state           = multiagent_graph.invoke(state)
        property_shapes = state["property_shapes"]
        node_shapes     = state["node_shapes"]
        shacl_history   = state["shacl_history"]

        logger.debug(
            f"Completed property {prop}: property_shapes_chars={len(property_shapes)}, "
            f"node_shapes_count={len(node_shapes)}, history_size={len(shacl_history.get_all())}"
        )

        gc.collect()
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    str_node_shapes = generate_node_shapes_str(node_shapes)
    shacl_completed = f"{shacl_prefixes}\n\n{str_node_shapes}\n\n{property_shapes}"

    logger.info("All SHACL constraints generated successfully.")
    logger.debug(
        f"Final SHACL output length: {len(shacl_completed)} chars "
        f"({len(node_shapes)} node shape entry/entries)."
    )
    return shacl_completed