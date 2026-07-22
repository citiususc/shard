"""Generate ontology-derived baseline shapes through external translators."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Tuple

from rdflib import Graph
from rdflib.namespace import RDF, SH

from shard.application.shape_validation import (
    GENERIC_SHACL_PROFILE_PATH,
    validate_shape_content,
)
from shard.baselines import (
    graph_copy,
    normalize_astrea_graph,
    parse_baseline_shapes,
    quarantine_focus_nodes,
    shape_roots,
)
from shard.domain.ontology import parse_ontology_graph
from shard.integrations.astrea import generate_astrea_shapes
from shard.observability import logger


def _baseline_filename(ontology_filename: str) -> str:
    stem = Path(str(ontology_filename or "ontology.ttl")).stem or "ontology"
    return f"{stem}_astrea.ttl"


def _generic_validation_report(graph: Graph) -> Tuple[bool, Graph, str]:
    """Validate one graph and retain the structured generic-profile report."""
    from pyshacl import validate as pyshacl_validate

    profile = Graph(bind_namespaces="none")
    profile.parse(str(GENERIC_SHACL_PROFILE_PATH), format="turtle")
    conforms, report_graph, report_text = pyshacl_validate(
        data_graph=graph,
        shacl_graph=profile,
        inference="none",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
        meta_shacl=False,
        advanced=True,
    )
    return bool(conforms), report_graph, str(report_text or "").strip()


def _conforming_merge_subset(
    evidence_graph: Graph,
) -> Tuple[Graph, Graph, int]:
    """Return a conforming merge graph and separately preserved rejects.

    Remaining generic-profile violations are isolated by their validation focus
    nodes after deterministic normalization. Removed triples are copied to the
    quarantine graph, so no information returned by Astrea disappears silently.
    """
    merge_graph = graph_copy(evidence_graph)
    quarantine = Graph(bind_namespaces="none")
    quarantined_shapes = 0
    previous_size = -1

    while len(merge_graph) and len(merge_graph) != previous_size:
        previous_size = len(merge_graph)
        conforms, report_graph, _ = _generic_validation_report(merge_graph)
        if conforms:
            break
        focus_nodes = {
            focus
            for result in report_graph.subjects(RDF.type, SH.ValidationResult)
            for focus in report_graph.objects(result, SH.focusNode)
        }
        if not focus_nodes:
            break
        removed = quarantine_focus_nodes(merge_graph, focus_nodes, quarantine)
        if not removed:
            break
        quarantined_shapes += removed

    return merge_graph, quarantine, quarantined_shapes


def _additional_repair_count(normalization: Dict[str, int]) -> int:
    keys = (
        "integer_literals_normalized",
        "boolean_literals_normalized",
        "string_literals_normalized",
        "malformed_list_nodes_repaired",
        "numeric_parameters_collapsed",
        "boolean_parameters_collapsed",
        "list_parameters_merged",
        "datatype_parameters_conjoined",
        "pattern_parameters_conjoined",
        "qualified_shapes_conjoined",
        "severity_parameters_collapsed",
        "shape_types_repaired",
    )
    return sum(normalization.get(key, 0) for key in keys)


def generate_astrea_baseline(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generate, normalize and partition an Astrea baseline safely."""
    ontology_content = str(payload.get("ontology_content") or "")
    ontology_filename = str(payload.get("ontology_filename") or "ontology.ttl")
    if not ontology_content.strip():
        raise ValueError("Missing ontology_content for Astrea generation.")

    ontology_graph = parse_ontology_graph(ontology_content, ontology_filename)
    ontology_turtle = ontology_graph.serialize(format="turtle")
    result = generate_astrea_shapes(ontology_turtle)
    baseline_graph = parse_baseline_shapes(
        result["shape_document"], "astrea.ttl", normalize=False
    )
    normalization = normalize_astrea_graph(baseline_graph)

    evidence_document = baseline_graph.serialize(format="turtle")
    validation = validate_shape_content(evidence_document, "", [], inference="none")
    evidence_safe = validation.get("syntax_valid") is True

    if validation.get("valid") is True:
        # Avoid copying and validating a large graph a second time when the
        # complete normalized baseline is already safe for merge.
        merge_document = evidence_document
        merge_validation = dict(validation)
        retained_shapes = len(shape_roots(baseline_graph))
        quarantined_shapes = 0
        quarantine_document = ""
    else:
        merge_graph, quarantine_graph, quarantined_shapes = _conforming_merge_subset(
            baseline_graph
        )
        merge_document = (
            merge_graph.serialize(format="turtle") if len(merge_graph) else ""
        )
        if merge_document:
            merge_validation = validate_shape_content(
                merge_document, "", [], inference="none"
            )
        else:
            merge_validation = {
                **validation,
                "valid": False,
                "profile_valid": False,
                "message": "No conforming Astrea shape fragments remained available for merge.",
            }
        retained_shapes = len(shape_roots(merge_graph))
        quarantine_document = (
            quarantine_graph.serialize(format="turtle")
            if len(quarantine_graph)
            else ""
        )
    normalization["retained_shapes"] = retained_shapes
    normalization["quarantined_shapes"] = quarantined_shapes
    merge_safe = retained_shapes > 0 and merge_validation.get("valid") is True

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
    additional_repairs = _additional_repair_count(normalization)
    if additional_repairs:
        warnings.append(
            f"Applied {additional_repairs} additional semantics-preserving "
            "normalization operation(s) before SHACL for SHACL validation."
        )
    if quarantined_shapes:
        warnings.append(
            f"Quarantined {quarantined_shapes} non-conforming Astrea shape "
            f"fragment(s); {retained_shapes} conforming shape fragment(s) remain "
            "available for focused merge. Quarantined content remains available "
            "for audit and the complete normalized baseline remains available as evidence."
        )
    if evidence_safe and not merge_safe:
        violations = int(validation.get("violation_count") or 0)
        warnings.append(
            "Astrea generated syntactically valid evidence, but it did not conform "
            "to the generic SHACL for SHACL profile"
            + (f" ({violations} violation(s))" if violations else "")
            + "; no conforming fragment could be retained for merge."
        )

    ontology_hash = sha256(ontology_content.encode("utf-8")).hexdigest()
    baseline_name = _baseline_filename(ontology_filename)
    logger.info(
        f"[astrea] generated {result['shape_count']} baseline shape(s) "
        f"for '{ontology_filename}'; normalized {normalized_shapes} node-kind "
        f"shape(s), retained {retained_shapes}, quarantined "
        f"{quarantined_shapes}, merge_safe={merge_safe}."
    )
    for warning in warnings:
        logger.warn(f"[astrea] {warning}")

    if validation.get("valid"):
        message = (
            f"Astrea generated {result['shape_count']} validated baseline shape(s) "
            f"from '{ontology_filename}'."
        )
    elif merge_safe:
        violations = int(validation.get("violation_count") or 0)
        message = (
            "Astrea generated a syntactically valid baseline with "
            f"{violations} generic SHACL for SHACL violation(s). Deterministic "
            f"normalization and quarantine retained {retained_shapes} conforming "
            "shape fragment(s) for focused merge; the complete normalized baseline "
            "remains available as evidence."
        )
    else:
        violations = int(validation.get("violation_count") or 0)
        message = (
            "Astrea generated the baseline, but it did not conform to the generic "
            f"SHACL for SHACL profile; {violations} violation(s) found. The baseline "
            "remains available as evidence, but no conforming fragment is available "
            "for merge."
        )

    return {
        "available": evidence_safe,
        "evidence_safe": evidence_safe,
        "merge_safe": merge_safe,
        "source": "astrea-api",
        "name": baseline_name,
        "size": len(evidence_document.encode("utf-8")),
        "ontology_hash": ontology_hash,
        **{**result, "shape_document": evidence_document},
        "merge_shape_document": merge_document,
        "quarantined_shape_document": quarantine_document,
        "validation": validation,
        "merge_validation": merge_validation,
        "normalization": normalization,
        "warnings": warnings,
        "message": message,
    }
