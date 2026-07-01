"""
Shared ontology parsing helpers for the local demo services.

All services parse uploaded ontology content through this module so format
guessing, fallback behaviour and namespace derivation stay consistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

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


def ontology_prefix_block(graph: Graph, base_namespace: str) -> str:
    return ns_utils.build_prefix_block(graph, base_namespace)
