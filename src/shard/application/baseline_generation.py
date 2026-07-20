"""Generate ontology-derived baseline shapes through external translators."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Dict

from shard.application.shape_validation import validate_shape_content
from shard.domain.ontology import parse_ontology_graph
from shard.integrations.astrea import generate_astrea_shapes
from shard.observability import logger


def _baseline_filename(ontology_filename: str) -> str:
    stem = Path(str(ontology_filename or "ontology.ttl")).stem or "ontology"
    return f"{stem}_astrea.ttl"


def generate_astrea_baseline(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a validated Astrea baseline for uploaded ontology content."""
    ontology_content = str(payload.get("ontology_content") or "")
    ontology_filename = str(payload.get("ontology_filename") or "ontology.ttl")
    if not ontology_content.strip():
        raise ValueError("Missing ontology_content for Astrea generation.")

    ontology_graph = parse_ontology_graph(ontology_content, ontology_filename)
    ontology_turtle = ontology_graph.serialize(format="turtle")
    result = generate_astrea_shapes(ontology_turtle)
    validation = validate_shape_content(result["shape_document"], "", [])
    ontology_hash = sha256(ontology_content.encode("utf-8")).hexdigest()
    baseline_name = _baseline_filename(ontology_filename)
    logger.info(
        f"[astrea] generated {result['shape_count']} baseline shape(s) "
        f"for '{ontology_filename}'."
    )
    return {
        "available": True,
        "source": "astrea-api",
        "name": baseline_name,
        "size": len(result["shape_document"].encode("utf-8")),
        "ontology_hash": ontology_hash,
        **result,
        "validation": validation,
        "message": (
            f"Astrea generated {result['shape_count']} baseline shape(s) "
            f"from '{ontology_filename}'."
        ),
    }
