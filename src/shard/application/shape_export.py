"""Prepare reviewed SHACL fragments for a lossless, compact Turtle export."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict, Mapping, Sequence, Set, Tuple

from rdflib import BNode, Graph, RDF, SH, URIRef
from rdflib.compare import to_canonical_graph

from shard.application.shape_validation import (
    validate_shape_content,
    validation_profiles_from_payload,
)


_SIGNATURE_ROOT = URIRef("urn:shard:export-signature-root")
_SIGNATURE_EDGE = URIRef("urn:shard:export-signature-edge")


def _copy_with_fresh_blank_nodes(source: Graph, target: Graph) -> None:
    """Copy one parsed fragment without allowing blank-node ids to collide."""
    mapping: Dict[BNode, BNode] = {}

    def mapped(term):
        if not isinstance(term, BNode):
            return term
        return mapping.setdefault(term, BNode())

    for subject, predicate, obj in source:
        target.add((mapped(subject), predicate, mapped(obj)))


def _copy_blank_node_closure(source: Graph, target: Graph, root: BNode) -> None:
    pending = [root]
    seen = set()
    while pending:
        subject = pending.pop()
        if subject in seen:
            continue
        seen.add(subject)
        for predicate, obj in source.predicate_objects(subject):
            target.add((subject, predicate, obj))
            if isinstance(obj, BNode):
                pending.append(obj)


def _rooted_signature(graph: Graph, root) -> str:
    """Return an isomorphism-stable signature for one RDF constraint subtree."""
    fragment = Graph(bind_namespaces="none")
    fragment.add((_SIGNATURE_ROOT, _SIGNATURE_EDGE, root))
    for predicate, obj in graph.predicate_objects(root):
        fragment.add((root, predicate, obj))
        if isinstance(obj, BNode):
            _copy_blank_node_closure(graph, fragment, obj)
    canonical = to_canonical_graph(fragment)
    rows = sorted(
        " ".join(term.n3() for term in triple)
        for triple in canonical
    )
    return sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _value_signature(graph: Graph, predicate, obj) -> str:
    fragment = Graph(bind_namespaces="none")
    fragment.add((_SIGNATURE_ROOT, predicate, obj))
    if isinstance(obj, BNode):
        _copy_blank_node_closure(graph, fragment, obj)
    canonical = to_canonical_graph(fragment)
    rows = sorted(
        " ".join(term.n3() for term in triple)
        for triple in canonical
    )
    return sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _constraint_inventory(graph: Graph) -> Set[Tuple[str, ...]]:
    """Describe every distinct attached and direct SHACL constraint."""
    inventory: Set[Tuple[str, ...]] = set()
    node_shapes = set(graph.subjects(RDF.type, SH.NodeShape))
    property_shapes = set(graph.subjects(RDF.type, SH.PropertyShape))

    for parent in node_shapes:
        parent_key = str(parent)
        for prop in graph.objects(parent, SH.property):
            inventory.add(("attached-property", parent_key, _rooted_signature(graph, prop)))
        for predicate, obj in graph.predicate_objects(parent):
            if predicate in {RDF.type, SH.targetClass, SH.property}:
                continue
            inventory.add((
                "node-value",
                parent_key,
                str(predicate),
                _value_signature(graph, predicate, obj),
            ))

    attached = set(graph.objects(None, SH.property))
    for prop in property_shapes:
        if prop not in attached:
            inventory.add(("standalone-property", _rooted_signature(graph, prop)))
    return inventory


def _remove_orphan_blank_node(graph: Graph, node) -> None:
    if not isinstance(node, BNode) or any(graph.triples((None, None, node))):
        return
    outgoing = list(graph.predicate_objects(node))
    for predicate, obj in outgoing:
        graph.remove((node, predicate, obj))
    for _predicate, obj in outgoing:
        _remove_orphan_blank_node(graph, obj)


def _remove_empty_node_shapes(graph: Graph) -> int:
    """Remove unreferenced target-only NodeShapes, which impose no constraint."""
    removed = 0
    for subject in list(set(graph.subjects(RDF.type, SH.NodeShape))):
        predicates = set(graph.predicates(subject, None))
        if not predicates.issubset({RDF.type, SH.targetClass}):
            continue
        if any(graph.triples((None, None, subject))):
            continue
        for triple in list(graph.triples((subject, None, None))):
            graph.remove(triple)
        removed += 1
    return removed


def _deduplicate_attached_property_shapes(graph: Graph) -> int:
    """Collapse only anonymous, structurally identical constraints per NodeShape."""
    removed = 0
    for parent in set(graph.subjects(RDF.type, SH.NodeShape)):
        seen: Dict[str, Any] = {}
        for prop in list(graph.objects(parent, SH.property)):
            if not isinstance(prop, BNode):
                continue
            signature = _rooted_signature(graph, prop)
            if signature not in seen:
                seen[signature] = prop
                continue
            graph.remove((parent, SH.property, prop))
            _remove_orphan_blank_node(graph, prop)
            removed += 1
    return removed


def _parse_documents(documents: Sequence[Mapping[str, Any]], prefixes: str) -> Graph:
    combined = Graph(bind_namespaces="none")
    for index, document in enumerate(documents):
        content = str(document.get("content") or "").strip()
        if not content:
            raise ValueError(f"Reviewed shape document {index + 1} is empty.")
        source = Graph(bind_namespaces="none")
        try:
            source.parse(data=f"{prefixes}\n{content}", format="turtle")
        except Exception as exc:
            name = str(document.get("name") or f"shape-{index + 1}.ttl")
            raise ValueError(f"Could not parse reviewed shape '{name}': {exc}") from exc
        for prefix, namespace in source.namespace_manager.namespaces():
            combined.bind(prefix, namespace, replace=False)
        _copy_with_fresh_blank_nodes(source, combined)
    return combined


def prepare_shape_export(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize reviewed shapes while proving that every distinct constraint remains."""
    documents = payload.get("shape_documents") or payload.get("documents") or []
    if not isinstance(documents, list) or not documents:
        raise ValueError("At least one reviewed SHACL document is required for export.")

    graph = _parse_documents(documents, str(payload.get("prefixes") or ""))
    input_triples = len(graph)
    input_node_shapes = len(set(graph.subjects(RDF.type, SH.NodeShape)))
    before = _constraint_inventory(graph)

    duplicate_constraints_removed = _deduplicate_attached_property_shapes(graph)
    empty_node_shapes_removed = _remove_empty_node_shapes(graph)
    after = _constraint_inventory(graph)
    if before != after:
        missing = len(before - after)
        added = len(after - before)
        raise RuntimeError(
            "Export normalization changed the reviewed constraint inventory "
            f"({missing} missing, {added} unexpected)."
        )

    shape_document = graph.serialize(format="turtle").strip()
    validation_payload = dict(payload)
    nested_validation = payload.get("validation")
    if (
        not validation_payload.get("validation_profiles")
        and isinstance(nested_validation, Mapping)
    ):
        validation_payload["validation_profiles"] = nested_validation.get("profiles") or []
    validation = validate_shape_content(
        shape_document,
        "",
        validation_profiles_from_payload(validation_payload),
    )
    return {
        **validation,
        "shape_document": shape_document,
        "statistics": {
            "source_documents": len(documents),
            "input_triples": input_triples,
            "output_triples": len(graph),
            "input_node_shapes": input_node_shapes,
            "output_node_shapes": len(set(graph.subjects(RDF.type, SH.NodeShape))),
            "distinct_constraints": len(after),
            "duplicate_constraints_removed": duplicate_constraints_removed,
            "empty_node_shapes_removed": empty_node_shapes_removed,
            "constraints_preserved": before == after,
        },
    }
