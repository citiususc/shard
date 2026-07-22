"""Generate ontology-derived baseline shapes through external translators."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Dict

from shard.application.shape_validation import validate_shape_content
from shard.baselines import normalize_astrea_node_kinds, parse_baseline_shapes
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
    baseline_graph = parse_baseline_shapes(result["shape_document"], "astrea.ttl")
    normalization = normalize_astrea_node_kinds(baseline_graph)
    result["shape_document"] = baseline_graph.serialize(format="turtle")
    validation = validate_shape_content(result["shape_document"], "", [])
    evidence_safe = validation.get("syntax_valid") is True
    merge_safe = validation.get("valid") is True
    warnings = []
    normalized_shapes = normalization["normalized_shapes"]
    if normalized_shapes:
        warnings.append(
            "Normalized alternative sh:nodeKind values on "
            f"{normalized_shapes} Astrea shape(s) before SHACL for SHACL validation."
        )
    if normalization["skipped_shapes"]:
        warnings.append(
            "Astrea returned non-standard sh:nodeKind alternatives on "
            f"{normalization['skipped_shapes']} shape(s); these values were preserved "
            "for profile validation."
        )
    if evidence_safe and not merge_safe:
        violations = int(validation.get("violation_count") or 0)
        warnings.append(
            "Astrea generated syntactically valid evidence, but it did not conform "
            "to the active SHACL for SHACL profile"
            + (f" ({violations} violation(s))" if violations else "")
            + "; whole-document merge is unsafe. Focused merges must be validated "
            "separately, and non-conforming fragments must be skipped."
        )
    ontology_hash = sha256(ontology_content.encode("utf-8")).hexdigest()
    baseline_name = _baseline_filename(ontology_filename)
    logger.info(
        f"[astrea] generated {result['shape_count']} baseline shape(s) "
        f"for '{ontology_filename}'; normalized {normalized_shapes} node-kind "
        f"shape(s), merge_safe={merge_safe}."
    )
    for warning in warnings:
        logger.warn(f"[astrea] {warning}")
    if merge_safe:
        message = (
            f"Astrea generated {result['shape_count']} validated baseline shape(s) "
            f"from '{ontology_filename}'."
        )
    else:
        violations = int(validation.get("violation_count") or 0)
        message = (
            "Astrea generated the baseline, but it did not conform to the active "
            f"SHACL for SHACL profile; {violations} violation(s) found. "
            "The baseline remains available as evidence; focused merges require "
            "per-shape validation."
        )
    return {
        "available": evidence_safe,
        "evidence_safe": evidence_safe,
        "merge_safe": merge_safe,
        "source": "astrea-api",
        "name": baseline_name,
        "size": len(result["shape_document"].encode("utf-8")),
        "ontology_hash": ontology_hash,
        **result,
        "validation": validation,
        "normalization": normalization,
        "warnings": warnings,
        "message": message,
    }
