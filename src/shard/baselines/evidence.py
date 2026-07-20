"""Select rule-focused baseline evidence for shape generation."""

from typing import Any, Dict, Iterable

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, SH

from shard.baselines.graph import _GraphCopier, _bind_namespaces
from shard.baselines.io import baseline_from_payload, parse_baseline_shapes

def _copy_target_evidence(
    graph: Graph,
    output: Graph,
    copier: _GraphCopier,
    target: Dict[str, Any],
) -> bool:
    target_value = str(target.get("full_iri") or target.get("iri") or "").strip()
    if not target_value.startswith(("http://", "https://", "urn:")):
        return False

    target_uri = URIRef(target_value)
    matched = False

    for shape in set(graph.subjects(SH.targetClass, target_uri)):
        copier.copy_root(shape)
        matched = True

    for property_shape in set(graph.subjects(SH.path, target_uri)):
        owners = set(graph.subjects(SH.property, property_shape))
        if owners:
            mapped_property = copier.copy_root(property_shape)
            for owner in owners:
                mapped_owner = copier.copy_predicates(
                    owner,
                    (RDF.type, SH.targetClass, SH.closed, SH.ignoredProperties),
                )
                output.add((mapped_owner, SH.property, mapped_property))
        else:
            copier.copy_root(property_shape)
        matched = True

    return matched


def focused_baseline_for_targets(
    graph: Graph,
    targets: Iterable[Dict[str, Any]],
) -> str:
    """Serialize baseline shapes relevant to every ontology term in one rule."""
    output = Graph(bind_namespaces="none")
    _bind_namespaces(output, graph)
    copier = _GraphCopier(graph, output)
    matched = False
    for target in targets:
        if _copy_target_evidence(graph, output, copier, target):
            matched = True

    if not matched:
        return ""
    return output.serialize(format="turtle").strip()


def focused_baseline_for_target(graph: Graph, target: Dict[str, Any]) -> str:
    """Serialize only baseline shapes relevant to one ontology target."""
    return focused_baseline_for_targets(graph, [target])


def baseline_context_for_target(payload: Dict[str, Any], target: Dict[str, Any]) -> str:
    """Resolve target-specific Astrea evidence from a service payload."""
    use_mode = str(payload.get("astrea_use_mode") or "").strip().lower()
    if use_mode and use_mode not in {"baseline", "both"}:
        return ""
    graph = payload.get("_astrea_graph")
    if graph is None:
        content, filename = baseline_from_payload(payload)
        if not content.strip():
            return ""
        graph = parse_baseline_shapes(content, filename)
    return focused_baseline_for_target(graph, target)


def baseline_context_for_targets(
    payload: Dict[str, Any],
    targets: Iterable[Dict[str, Any]],
) -> str:
    """Resolve Astrea evidence for all ontology terms participating in a rule."""
    use_mode = str(payload.get("astrea_use_mode") or "").strip().lower()
    if use_mode and use_mode not in {"baseline", "both"}:
        return ""
    graph = payload.get("_astrea_graph")
    if graph is None:
        content, filename = baseline_from_payload(payload)
        if not content.strip():
            return ""
        graph = parse_baseline_shapes(content, filename)
    return focused_baseline_for_targets(graph, targets)
