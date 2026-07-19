"""Targeted baseline-shape evidence and RDF-aware SHACL merge strategies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, SH


MERGE_TECHNIQUES = {"priority-llm", "restrictive"}

_HIGHER_WINS = {
    SH.minCount,
    SH.minInclusive,
    SH.minExclusive,
    SH.minLength,
}
_LOWER_WINS = {
    SH.maxCount,
    SH.maxInclusive,
    SH.maxExclusive,
    SH.maxLength,
}
_TRUE_WINS = {SH.closed, SH.uniqueLang}
_SET_INTERSECTION = {SH["in"], SH.languageIn}
_EQUALITY_CHECK = {SH.datatype, SH["class"]}
_KEEP_ALL = {SH.pattern, SH.hasValue}
_GENERATED_METADATA = {
    SH.name,
    SH.description,
    SH.message,
    SH.order,
    SH.severity,
}
_NODE_KIND_SPECIFICITY = {
    SH.BlankNodeOrIRI: 1,
    SH.BlankNodeOrLiteral: 1,
    SH.IRIOrLiteral: 1,
    SH.BlankNode: 2,
    SH.IRI: 2,
    SH.Literal: 2,
}


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


class _GraphCopier:
    """Copy RDF subgraphs while isolating blank-node identities by source graph."""

    def __init__(self, source: Graph, output: Graph):
        self.source = source
        self.output = output
        self._bnode_map: Dict[BNode, BNode] = {}
        self._visited: Set[Any] = set()

    def mapped(self, node: Any) -> Any:
        if not isinstance(node, BNode):
            return node
        return self._bnode_map.setdefault(node, BNode())

    def copy_root(self, root: Any) -> Any:
        mapped_root = self.mapped(root)
        if root in self._visited:
            return mapped_root
        self._visited.add(root)
        for predicate, obj in self.source.predicate_objects(root):
            mapped_obj = self.mapped(obj)
            self.output.add((mapped_root, predicate, mapped_obj))
            if isinstance(obj, BNode):
                self.copy_root(obj)
        return mapped_root

    def copy_predicates(self, root: Any, predicates: Iterable[URIRef]) -> Any:
        mapped_root = self.mapped(root)
        for predicate in predicates:
            for obj in self.source.objects(root, predicate):
                mapped_obj = self.mapped(obj)
                self.output.add((mapped_root, predicate, mapped_obj))
                if isinstance(obj, BNode):
                    self.copy_root(obj)
        return mapped_root


def _bind_namespaces(output: Graph, *sources: Graph) -> None:
    for source_index, source in enumerate(sources):
        for prefix, namespace in source.namespace_manager.namespaces():
            output.bind(
                prefix,
                namespace,
                override=source_index == 0,
                replace=source_index == 0,
            )


def focused_baseline_for_target(graph: Graph, target: Dict[str, Any]) -> str:
    """Serialize only baseline shapes relevant to one ontology target."""
    target_value = str(target.get("full_iri") or target.get("iri") or "").strip()
    if not target_value.startswith(("http://", "https://", "urn:")):
        return ""

    target_uri = URIRef(target_value)
    output = Graph(bind_namespaces="none")
    _bind_namespaces(output, graph)
    copier = _GraphCopier(graph, output)
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

    if not matched:
        return ""
    return output.serialize(format="turtle").strip()


def baseline_context_for_target(payload: Dict[str, Any], target: Dict[str, Any]) -> str:
    """Resolve target-specific Astrea evidence from a service payload."""
    graph = payload.get("_astrea_graph")
    if graph is None:
        content, filename = baseline_from_payload(payload)
        if not content.strip():
            return ""
        graph = parse_baseline_shapes(content, filename)
    return focused_baseline_for_target(graph, target)


def _shape_roots(graph: Graph, shape_type: URIRef) -> Set[Any]:
    return set(graph.subjects(RDF.type, shape_type))


def _index_property_shapes(graph: Graph) -> Dict[URIRef, List[Any]]:
    result: Dict[URIRef, List[Any]] = {}
    for shape in _shape_roots(graph, SH.PropertyShape):
        path = graph.value(shape, SH.path)
        if isinstance(path, URIRef):
            result.setdefault(path, []).append(shape)
    return result


def _covered_property_paths(graph: Graph) -> Set[URIRef]:
    """Return simple paths from standalone and nested property shapes."""
    return {
        path
        for path in graph.objects(None, SH.path)
        if isinstance(path, URIRef)
    }


def _index_node_shapes(graph: Graph) -> Dict[URIRef, List[Any]]:
    result: Dict[URIRef, List[Any]] = {}
    for shape in _shape_roots(graph, SH.NodeShape):
        for target_class in graph.objects(shape, SH.targetClass):
            if isinstance(target_class, URIRef):
                result.setdefault(target_class, []).append(shape)
    return result


def _all_indexed_roots(index: Dict[URIRef, List[Any]]) -> Set[Any]:
    return {shape for shapes in index.values() for shape in shapes}


@dataclass
class _Value:
    node: Any
    graph: Optional[Graph]
    source: str


class _MergerBase:
    def __init__(self, astrea: Graph, generated: Graph):
        self.astrea = astrea
        self.generated = generated
        self.output = Graph(bind_namespaces="none")
        _bind_namespaces(self.output, generated, astrea)
        self.copiers = {
            id(astrea): _GraphCopier(astrea, self.output),
            id(generated): _GraphCopier(generated, self.output),
        }
        self.warnings: List[str] = []
        self.stats: Dict[str, int] = {}

    def _copy(self, graph: Graph, root: Any) -> Any:
        return self.copiers[id(graph)].copy_root(root)

    def result(self) -> Tuple[Graph, Dict[str, Any]]:
        raise NotImplementedError


class _GeneratedFirstMerger(_MergerBase):
    """Keep generated shapes for covered targets and use Astrea as fallback."""

    def result(self) -> Tuple[Graph, Dict[str, Any]]:
        generated_paths = _covered_property_paths(self.generated)
        generated_nodes = _index_node_shapes(self.generated)
        astrea_properties = _index_property_shapes(self.astrea)
        astrea_nodes = _index_node_shapes(self.astrea)

        generated_roots = (
            _shape_roots(self.generated, SH.PropertyShape)
            | _shape_roots(self.generated, SH.NodeShape)
        )
        for root in generated_roots:
            self._copy(self.generated, root)

        astrea_fallback_roots: Set[Any] = set()
        for path, shapes in astrea_properties.items():
            if path not in generated_paths:
                astrea_fallback_roots.update(shapes)
        for target_class, shapes in astrea_nodes.items():
            if target_class not in generated_nodes:
                astrea_fallback_roots.update(shapes)
        for root in astrea_fallback_roots:
            self._copy(self.astrea, root)

        self.stats = {
            "generated_shapes": len(generated_roots),
            "astrea_fallback_shapes": len(astrea_fallback_roots),
            "generated_paths": len(generated_paths),
            "astrea_fallback_paths": sum(
                1 for path in astrea_properties if path not in generated_paths
            ),
            "generated_target_classes": len(generated_nodes),
            "astrea_fallback_target_classes": sum(
                1 for target_class in astrea_nodes if target_class not in generated_nodes
            ),
        }
        return self.output, {"stats": self.stats, "warnings": self.warnings}


class _RestrictiveMerger(_MergerBase):
    """Merge shared shapes by retaining the strongest compatible constraints."""

    def result(self) -> Tuple[Graph, Dict[str, Any]]:
        astrea_properties = _index_property_shapes(self.astrea)
        generated_properties = _index_property_shapes(self.generated)
        astrea_nodes = _index_node_shapes(self.astrea)
        generated_nodes = _index_node_shapes(self.generated)
        merged_paths = merged_classes = 0

        for path in sorted(set(astrea_properties) | set(generated_properties), key=str):
            astrea_shapes = astrea_properties.get(path, [])
            generated_shapes = generated_properties.get(path, [])
            if astrea_shapes and generated_shapes:
                self._merge_property_group(path, astrea_shapes, generated_shapes)
                merged_paths += 1
            else:
                source = self.generated if generated_shapes else self.astrea
                for shape in generated_shapes or astrea_shapes:
                    self._copy(source, shape)

        for target_class in sorted(set(astrea_nodes) | set(generated_nodes), key=str):
            astrea_shapes = astrea_nodes.get(target_class, [])
            generated_shapes = generated_nodes.get(target_class, [])
            if astrea_shapes and generated_shapes:
                self._merge_node_group(target_class, astrea_shapes, generated_shapes)
                merged_classes += 1
            else:
                source = self.generated if generated_shapes else self.astrea
                for shape in generated_shapes or astrea_shapes:
                    self._copy(source, shape)

        indexed_generated = _all_indexed_roots(generated_properties) | _all_indexed_roots(generated_nodes)
        indexed_astrea = _all_indexed_roots(astrea_properties) | _all_indexed_roots(astrea_nodes)
        all_generated = _shape_roots(self.generated, SH.PropertyShape) | _shape_roots(
            self.generated, SH.NodeShape
        )
        all_astrea = _shape_roots(self.astrea, SH.PropertyShape) | _shape_roots(
            self.astrea, SH.NodeShape
        )
        for root in all_generated - indexed_generated:
            self._copy(self.generated, root)
        for root in all_astrea - indexed_astrea:
            self._copy(self.astrea, root)

        self.stats = {
            "merged_paths": merged_paths,
            "merged_target_classes": merged_classes,
            "generated_shapes": len(all_generated),
            "astrea_shapes": len(all_astrea),
        }
        return self.output, {"stats": self.stats, "warnings": self.warnings}

    def _values(
        self,
        graph: Graph,
        shapes: Sequence[Any],
        predicate: URIRef,
        source: str,
    ) -> List[_Value]:
        return [
            _Value(obj, graph, source)
            for shape in shapes
            for obj in graph.objects(shape, predicate)
        ]

    def _predicates(self, graph: Graph, shapes: Sequence[Any]) -> Set[URIRef]:
        return {
            predicate
            for shape in shapes
            for predicate in graph.predicates(shape, None)
        }

    def _canonical(self, graph: Graph, node: Any) -> Any:
        return self.copiers[id(graph)].mapped(node)

    def _merge_property_group(
        self,
        path: URIRef,
        astrea_shapes: Sequence[Any],
        generated_shapes: Sequence[Any],
    ) -> Any:
        canonical = self._canonical(self.generated, generated_shapes[0])
        self.output.add((canonical, RDF.type, SH.PropertyShape))
        self.output.add((canonical, SH.path, path))
        predicates = self._predicates(self.astrea, astrea_shapes) | self._predicates(
            self.generated, generated_shapes
        )
        for predicate in sorted(predicates - {RDF.type, SH.path}, key=str):
            values = self._values(self.generated, generated_shapes, predicate, "generated")
            values += self._values(self.astrea, astrea_shapes, predicate, "astrea")
            self._add_values(canonical, predicate, self._merge_constraint(predicate, values, canonical))
        return canonical

    def _merge_node_group(
        self,
        target_class: URIRef,
        astrea_shapes: Sequence[Any],
        generated_shapes: Sequence[Any],
    ) -> None:
        canonical = self._canonical(self.generated, generated_shapes[0])
        self.output.add((canonical, RDF.type, SH.NodeShape))
        self.output.add((canonical, SH.targetClass, target_class))
        predicates = self._predicates(self.astrea, astrea_shapes) | self._predicates(
            self.generated, generated_shapes
        )
        for predicate in sorted(predicates - {RDF.type, SH.targetClass, SH.property}, key=str):
            values = self._values(self.generated, generated_shapes, predicate, "generated")
            values += self._values(self.astrea, astrea_shapes, predicate, "astrea")
            self._add_values(canonical, predicate, self._merge_constraint(predicate, values, canonical))

        generated_properties = [
            (_simple_path(self.generated, node), node)
            for shape in generated_shapes
            for node in self.generated.objects(shape, SH.property)
        ]
        astrea_properties = [
            (_simple_path(self.astrea, node), node)
            for shape in astrea_shapes
            for node in self.astrea.objects(shape, SH.property)
        ]
        by_path: Dict[URIRef, Dict[str, List[Any]]] = {}
        complex_nodes: List[Tuple[Graph, Any]] = []
        for source, graph, entries in (
            ("generated", self.generated, generated_properties),
            ("astrea", self.astrea, astrea_properties),
        ):
            for path, node in entries:
                if path is None:
                    complex_nodes.append((graph, node))
                else:
                    by_path.setdefault(path, {"generated": [], "astrea": []})[source].append(node)

        for path in sorted(by_path, key=str):
            groups = by_path[path]
            if groups["generated"] and groups["astrea"]:
                prop_node = self._merge_nested_property(
                    path, groups["astrea"], groups["generated"]
                )
                self.output.add((canonical, SH.property, prop_node))
            else:
                graph = self.generated if groups["generated"] else self.astrea
                for node in groups["generated"] or groups["astrea"]:
                    self.output.add((canonical, SH.property, self._copy(graph, node)))

        for graph, node in complex_nodes:
            self.output.add((canonical, SH.property, self._copy(graph, node)))

    def _merge_nested_property(
        self,
        path: URIRef,
        astrea_nodes: Sequence[Any],
        generated_nodes: Sequence[Any],
    ) -> Any:
        canonical = self._canonical(self.generated, generated_nodes[0])
        self.output.add((canonical, SH.path, path))
        if any((node, RDF.type, SH.PropertyShape) in graph for graph, nodes in (
            (self.generated, generated_nodes),
            (self.astrea, astrea_nodes),
        ) for node in nodes):
            self.output.add((canonical, RDF.type, SH.PropertyShape))
        predicates = self._predicates(self.astrea, astrea_nodes) | self._predicates(
            self.generated, generated_nodes
        )
        for predicate in sorted(predicates - {RDF.type, SH.path}, key=str):
            values = self._values(self.generated, generated_nodes, predicate, "generated")
            values += self._values(self.astrea, astrea_nodes, predicate, "astrea")
            self._add_values(canonical, predicate, self._merge_constraint(predicate, values, canonical))
        return canonical

    def _add_values(self, subject: Any, predicate: URIRef, values: Sequence[_Value]) -> None:
        for value in values:
            node = value.node
            if value.graph is not None and isinstance(node, BNode):
                node = self._copy(value.graph, node)
            self.output.add((subject, predicate, node))

    def _merge_constraint(
        self,
        predicate: URIRef,
        values: Sequence[_Value],
        shape: Any,
    ) -> List[_Value]:
        values = _dedupe_values(values)
        if not values:
            return []

        if predicate in _HIGHER_WINS or predicate in _LOWER_WINS:
            numeric = [(value, _as_number(value.node)) for value in values]
            numeric = [(value, number) for value, number in numeric if number is not None]
            if not numeric:
                return [values[0]]
            chooser = max if predicate in _HIGHER_WINS else min
            return [chooser(numeric, key=lambda item: item[1])[0]]

        if predicate in _TRUE_WINS:
            truth = any(_as_boolean(value.node) is True for value in values)
            return [_Value(Literal(truth), None, "merged")]

        if predicate in _SET_INTERSECTION:
            lists = [(value, _list_members(value.graph, value.node)) for value in values]
            lists = [(value, members) for value, members in lists if members is not None]
            if not lists:
                return [values[0]]
            intersection = set(lists[0][1])
            for _, members in lists[1:]:
                intersection &= set(members)
            if not intersection:
                self.warnings.append(
                    f"{shape} {predicate}: empty intersection; kept the generated list."
                )
                generated = next((value for value, _ in lists if value.source == "generated"), lists[0][0])
                return [generated]
            head = BNode()
            Collection(self.output, head, sorted(intersection, key=str))
            return [_Value(head, None, "merged")]

        if predicate == SH.nodeKind:
            kinds = [value for value in values if isinstance(value.node, URIRef)]
            if not kinds:
                return [values[0]]
            best_score = max(_NODE_KIND_SPECIFICITY.get(value.node, -1) for value in kinds)
            best = [value for value in kinds if _NODE_KIND_SPECIFICITY.get(value.node, -1) == best_score]
            return [next((value for value in best if value.source == "generated"), best[0])]

        if predicate in _EQUALITY_CHECK:
            if len({value.node for value in values}) > 1:
                self.warnings.append(
                    f"{shape} {predicate}: conflicting values; kept the generated value."
                )
            return [next((value for value in values if value.source == "generated"), values[0])]

        if predicate in _KEEP_ALL:
            return list(values)

        if predicate in _GENERATED_METADATA:
            generated = [value for value in values if value.source == "generated"]
            return generated or [values[0]]

        return list(values)


def _simple_path(graph: Graph, shape: Any) -> Optional[URIRef]:
    path = graph.value(shape, SH.path)
    return path if isinstance(path, URIRef) else None


def _dedupe_values(values: Sequence[_Value]) -> List[_Value]:
    seen: Set[Any] = set()
    result: List[_Value] = []
    for value in values:
        key = (
            (id(value.graph), value.node)
            if isinstance(value.node, BNode) and value.graph is not None
            else value.node
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _as_number(value: Any) -> Optional[float]:
    if not isinstance(value, Literal):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_boolean(value: Any) -> Optional[bool]:
    if not isinstance(value, Literal):
        return None
    converted = value.toPython()
    return converted if isinstance(converted, bool) else None


def _list_members(graph: Optional[Graph], head: Any) -> Optional[List[Any]]:
    if graph is None or not isinstance(head, (BNode, URIRef)):
        return None
    try:
        return list(Collection(graph, head))
    except Exception:
        return None


def merge_shape_graphs(
    astrea: Graph,
    generated: Graph,
    technique: str,
) -> Tuple[Graph, Dict[str, Any]]:
    """Merge parsed Astrea and generated SHACL graphs using one strategy."""
    normalized = str(technique or "").strip().lower()
    if normalized not in MERGE_TECHNIQUES:
        raise ValueError(
            f"Unknown Astrea merge technique '{technique}'. "
            f"Choose from: {', '.join(sorted(MERGE_TECHNIQUES))}."
        )
    merger = (
        _GeneratedFirstMerger(astrea, generated)
        if normalized == "priority-llm"
        else _RestrictiveMerger(astrea, generated)
    )
    output, details = merger.result()
    return output, {"technique": normalized, "triples": len(output), **details}


def merge_shape_documents(
    astrea_content: str,
    generated_content: str,
    technique: str,
    *,
    astrea_filename: str = "astrea.ttl",
    generated_filename: str = "generated_shapes.ttl",
) -> Dict[str, Any]:
    """Parse and merge two SHACL documents, returning serialized Turtle."""
    astrea = parse_baseline_shapes(astrea_content, astrea_filename)
    generated = parse_baseline_shapes(generated_content, generated_filename)
    merged, details = merge_shape_graphs(astrea, generated, technique)
    return {
        "shape_document": merged.serialize(format="turtle").strip(),
        **details,
    }
