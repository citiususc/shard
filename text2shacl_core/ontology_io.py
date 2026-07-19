"""
Shared ontology parsing helpers for the local demo services.

All services parse uploaded ontology content through this module so format
guessing, fallback behaviour and namespace derivation stay consistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from rdflib import Graph

import ns_utils


def guess_format(filename: str = "") -> str:
    suffix = Path(filename or "").suffix.lower()
    return {
        ".ttl": "turtle",
        ".trig": "trig",
        ".nt": "nt",
        ".nq": "nquads",
        ".rdf": "xml",
        ".owl": "xml",
        ".xml": "xml",
    }.get(suffix, "turtle")


def parse_ontology_graph(
    content: str,
    filename: str = "",
    *,
    format_hint: Optional[str] = None,
) -> Graph:
    """Parse ontology content into an rdflib Graph with a conservative fallback."""
    if not content:
        raise ValueError("Missing ontology content.")

    graph = Graph(bind_namespaces="none")
    fmt = format_hint or guess_format(filename)
    try:
        graph.parse(data=content, format=fmt)
        return graph
    except Exception as first_exc:
        fallback = "xml" if fmt != "xml" else "turtle"
        try:
            graph = Graph(bind_namespaces="none")
            graph.parse(data=content, format=fallback)
            return graph
        except Exception as second_exc:
            raise ValueError(
                f"Could not parse ontology as {fmt} or {fallback}: {second_exc}"
            ) from first_exc


def ontology_base_namespace(graph: Graph) -> str:
    return ns_utils.derive_base_namespace(graph)


def ontology_namespace_analysis(graph: Graph) -> Dict[str, Any]:
    return ns_utils.analyze_base_namespace(graph)


def ontology_shapes_namespace(graph: Graph, base_namespace: str) -> Tuple[str, str]:
    return ns_utils.derive_shapes_namespace(graph, base_namespace)


def ontology_shape_prefix(graph: Graph, shape_namespace: str) -> Tuple[str, str]:
    return ns_utils.derive_shape_prefix(graph, shape_namespace)


def ontology_prefix_block(
    graph: Graph,
    base_namespace: str,
    shape_namespace: Optional[str] = None,
    shape_prefix: Optional[str] = None,
    *,
    include_legacy_aliases: bool = False,
) -> str:
    return ns_utils.build_prefix_block(
        graph,
        base_namespace,
        shape_ns=shape_namespace,
        shape_prefix=shape_prefix,
        include_legacy_aliases=include_legacy_aliases,
    )
