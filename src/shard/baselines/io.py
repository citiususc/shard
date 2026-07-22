"""Parse baseline SHACL documents and read them from request payloads."""

from pathlib import Path
from typing import Any, Dict, FrozenSet, Tuple

from rdflib import Graph, Literal
from rdflib.namespace import SH, XSD


_CARDINALITY_PREDICATES = (
    SH.minCount,
    SH.maxCount,
    SH.qualifiedMinCount,
    SH.qualifiedMaxCount,
)

_RDF_TERM_BLANK = "blank"
_RDF_TERM_IRI = "iri"
_RDF_TERM_LITERAL = "literal"
_NODE_KIND_MEMBERS: Dict[Any, FrozenSet[str]] = {
    SH.BlankNode: frozenset({_RDF_TERM_BLANK}),
    SH.IRI: frozenset({_RDF_TERM_IRI}),
    SH.Literal: frozenset({_RDF_TERM_LITERAL}),
    SH.BlankNodeOrIRI: frozenset({_RDF_TERM_BLANK, _RDF_TERM_IRI}),
    SH.BlankNodeOrLiteral: frozenset({_RDF_TERM_BLANK, _RDF_TERM_LITERAL}),
    SH.IRIOrLiteral: frozenset({_RDF_TERM_IRI, _RDF_TERM_LITERAL}),
}
_NODE_KIND_BY_MEMBERS = {
    members: node_kind for node_kind, members in _NODE_KIND_MEMBERS.items()
}


def normalize_shacl_cardinalities(graph: Graph) -> Graph:
    """Canonicalize SHACL cardinality literals for meta-SHACL validation.

    Astrea may serialize non-negative counts with ``xsd:nonNegativeInteger``.
    SHACL permits the value, but the generic SHACL-for-SHACL profile in this
    project requires the canonical ``xsd:integer`` datatype.
    """
    replacements = []
    for predicate in _CARDINALITY_PREDICATES:
        for subject, value in graph.subject_objects(predicate):
            if not isinstance(value, Literal):
                continue
            try:
                count = int(str(value))
            except (TypeError, ValueError):
                continue
            if count < 0 or value.datatype == XSD.integer:
                continue
            replacements.append(
                (subject, predicate, value, Literal(count, datatype=XSD.integer))
            )

    for subject, predicate, old_value, new_value in replacements:
        graph.remove((subject, predicate, old_value))
        graph.add((subject, predicate, new_value))
    return graph


def normalize_astrea_node_kinds(graph: Graph) -> Dict[str, int]:
    """Collapse Astrea's alternative ``sh:nodeKind`` values safely.

    Astrea can emit several node-kind values on one shape to represent an
    allowed union. SHACL treats repeated constraint parameters conjunctively,
    while SHACL for SHACL permits at most one ``sh:nodeKind`` value. The six
    standard node kinds are sets over blank nodes, IRIs and literals, so their
    union can always be represented by one composite node kind or, when all
    three RDF term categories are allowed, by omitting the vacuous constraint.

    Values outside the standard SHACL node-kind vocabulary are left untouched
    so that profile validation reports them instead of silently discarding
    upstream information.
    """
    statistics = {
        "candidate_shapes": 0,
        "normalized_shapes": 0,
        "collapsed_shapes": 0,
        "unrestricted_shapes": 0,
        "skipped_shapes": 0,
        "removed_values": 0,
    }
    all_term_categories = frozenset(
        {_RDF_TERM_BLANK, _RDF_TERM_IRI, _RDF_TERM_LITERAL}
    )

    for subject in set(graph.subjects(SH.nodeKind, None)):
        values = set(graph.objects(subject, SH.nodeKind))
        if len(values) <= 1:
            continue
        statistics["candidate_shapes"] += 1
        if not values.issubset(_NODE_KIND_MEMBERS):
            statistics["skipped_shapes"] += 1
            continue

        allowed_terms = frozenset().union(
            *(_NODE_KIND_MEMBERS[value] for value in values)
        )
        replacement = _NODE_KIND_BY_MEMBERS.get(allowed_terms)
        graph.remove((subject, SH.nodeKind, None))
        statistics["removed_values"] += len(values)
        statistics["normalized_shapes"] += 1

        if allowed_terms == all_term_categories:
            statistics["unrestricted_shapes"] += 1
            continue
        if replacement is not None:
            graph.add((subject, SH.nodeKind, replacement))
            statistics["collapsed_shapes"] += 1
            continue

        # Every non-empty subset of the three RDF term categories has a
        # standard SHACL node-kind representation; keep this guard defensive.
        statistics["skipped_shapes"] += 1

    return statistics


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
        return normalize_shacl_cardinalities(graph)
    except Exception as first_exc:
        fallback = "xml" if fmt != "xml" else "turtle"
        try:
            graph = Graph(bind_namespaces="none")
            graph.parse(data=content, format=fallback)
            return normalize_shacl_cardinalities(graph)
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
