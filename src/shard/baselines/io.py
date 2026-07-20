"""Parse baseline SHACL documents and read them from request payloads."""

from pathlib import Path
from typing import Any, Dict, Tuple

from rdflib import Graph

def _guess_format(filename: str) -> str:
    return {
        ".ttl": "turtle",
        ".nt": "nt",
        ".rdf": "xml",
        ".owl": "xml",
        ".xml": "xml",
    }.get(Path(filename or "").suffix.lower(), "turtle")


def parse_baseline_shapes(content: str, filename: str = "astrea.ttl") -> Graph:
    """Parse an uploaded baseline-shape document without assuming a domain."""
    if not str(content or "").strip():
        raise ValueError("Missing SHACL shape content.")

    fmt = _guess_format(filename)
    graph = Graph(bind_namespaces="none")
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
                f"Could not parse SHACL shapes as {fmt} or {fallback}: {second_exc}"
            ) from first_exc


def baseline_from_payload(payload: Dict[str, Any]) -> Tuple[str, str]:
    """Return baseline content and filename from the shared service payload."""
    baseline = payload.get("astrea_baseline") or {}
    if isinstance(baseline, dict):
        content = str(baseline.get("content") or "")
        filename = str(baseline.get("name") or baseline.get("filename") or "astrea.ttl")
        return content, filename
    return str(payload.get("astrea_shapes") or baseline or ""), str(
        payload.get("astrea_filename") or "astrea.ttl"
    )

