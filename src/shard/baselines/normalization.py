"""Semantics-preserving normalization for ontology-derived SHACL baselines."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, MutableMapping, Sequence, Set

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, SH, XSD


_INTEGER_PREDICATES = (
    SH.minCount,
    SH.maxCount,
    SH.minLength,
    SH.maxLength,
    SH.qualifiedMinCount,
    SH.qualifiedMaxCount,
)
_MINIMUM_PREDICATES = (
    SH.minCount,
    SH.minLength,
    SH.qualifiedMinCount,
    SH.minInclusive,
    SH.minExclusive,
)
_MAXIMUM_PREDICATES = (
    SH.maxCount,
    SH.maxLength,
    SH.qualifiedMaxCount,
    SH.maxInclusive,
    SH.maxExclusive,
)
_BOOLEAN_PREDICATES = (
    SH.closed,
    SH.deactivated,
    SH.uniqueLang,
    SH.qualifiedValueShapesDisjoint,
)
_STRING_PREDICATES = (SH.pattern, SH.flags, SH.message)
_TRUE_IS_STRICTER = (SH.closed, SH.uniqueLang, SH.qualifiedValueShapesDisjoint)
_LIST_PREDICATES = (
    SH["and"],
    SH["or"],
    SH.xone,
    SH["in"],
    SH.languageIn,
    SH.ignoredProperties,
    SH.alternativePath,
)

_RDF_TERM_BLANK = "blank"
_RDF_TERM_IRI = "iri"
_RDF_TERM_LITERAL = "literal"
_NODE_KIND_MEMBERS = {
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


def _new_statistics() -> Dict[str, int]:
    return {
        "candidate_shapes": 0,
        "normalized_shapes": 0,
        "collapsed_shapes": 0,
        "unrestricted_shapes": 0,
        "skipped_shapes": 0,
        "removed_values": 0,
        "integer_literals_normalized": 0,
        "boolean_literals_normalized": 0,
        "string_literals_normalized": 0,
        "malformed_list_nodes_repaired": 0,
        "malformed_list_members_preserved": 0,
        "numeric_parameters_collapsed": 0,
        "boolean_parameters_collapsed": 0,
        "list_parameters_merged": 0,
        "datatype_parameters_conjoined": 0,
        "pattern_parameters_conjoined": 0,
        "qualified_shapes_conjoined": 0,
        "severity_parameters_collapsed": 0,
        "shape_types_repaired": 0,
        "retained_shapes": 0,
        "quarantined_shapes": 0,
    }


def _replace(graph: Graph, subject: Any, predicate: URIRef, old: Any, new: Any) -> None:
    graph.remove((subject, predicate, old))
    graph.add((subject, predicate, new))


def _integer_value(value: Any) -> int | None:
    if not isinstance(value, Literal):
        return None
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite() or decimal != decimal.to_integral_value():
            return None
        number = int(decimal)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _boolean_value(value: Any) -> bool | None:
    if not isinstance(value, Literal):
        return None
    converted = value.toPython()
    if isinstance(converted, bool):
        return converted
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    return None


def _decimal_value(value: Any) -> Decimal | None:
    if not isinstance(value, Literal):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _list_members(graph: Graph, head: Any) -> List[Any] | None:
    if head == RDF.nil:
        return []
    if not isinstance(head, (BNode, URIRef)):
        return None
    try:
        return list(Collection(graph, head))
    except Exception:
        return None


def _list_head(graph: Graph, members: Sequence[Any]) -> Any:
    if not members:
        return RDF.nil
    head = BNode()
    Collection(graph, head, list(members))
    return head


def _remove_list(graph: Graph, head: Any) -> None:
    current = head
    visited = set()
    while isinstance(current, BNode) and current not in visited:
        visited.add(current)
        rest = graph.value(current, RDF.rest)
        graph.remove((current, None, None))
        current = rest


def _reachable_list_nodes(graph: Graph, head: Any) -> List[Any] | None:
    """Return a finite list chain whose nodes each have exactly one rdf:rest."""
    if head == RDF.nil:
        return []
    nodes = []
    current = head
    visited = set()
    while current != RDF.nil:
        if not isinstance(current, (BNode, URIRef)) or current in visited:
            return None
        visited.add(current)
        first_values = list(graph.objects(current, RDF.first))
        rest_values = list(graph.objects(current, RDF.rest))
        if not first_values or len(rest_values) != 1:
            return None
        nodes.append(current)
        current = rest_values[0]
    return nodes


def normalize_malformed_lists(
    graph: Graph,
    statistics: MutableMapping[str, int],
) -> None:
    """Linearize SHACL lists whose cells contain repeated rdf:first values.

    RDF collections require exactly one rdf:first and rdf:rest per non-empty
    cell. Astrea occasionally places several alternatives in one rdf:first
    slot. The graph contains every intended member but is not a valid RDF list.
    This repair creates one canonical cell per member without dropping any
    alternative. It only touches finite chains with an unambiguous rdf:rest.
    """
    heads = {
        head
        for predicate in _LIST_PREDICATES
        for head in graph.objects(None, predicate)
    }
    processed = set()
    for head in heads:
        nodes = _reachable_list_nodes(graph, head)
        if nodes is None:
            continue
        for node in reversed(nodes):
            if node in processed:
                continue
            processed.add(node)
            first_values = sorted(set(graph.objects(node, RDF.first)), key=str)
            if len(first_values) <= 1:
                continue
            original_rest = graph.value(node, RDF.rest)
            graph.remove((node, RDF.first, None))
            graph.remove((node, RDF.rest, None))
            current = node
            for index, value in enumerate(first_values):
                graph.add((current, RDF.first, value))
                if index == len(first_values) - 1:
                    graph.add((current, RDF.rest, original_rest))
                else:
                    following = BNode()
                    graph.add((current, RDF.rest, following))
                    current = following
            statistics["malformed_list_nodes_repaired"] += 1
            statistics["malformed_list_members_preserved"] += len(first_values)


def normalize_literal_datatypes(
    graph: Graph,
    statistics: MutableMapping[str, int],
) -> None:
    """Canonicalize unambiguous SHACL parameter literal datatypes."""
    for predicate in _INTEGER_PREDICATES:
        for subject, value in list(graph.subject_objects(predicate)):
            number = _integer_value(value)
            if number is None or value.datatype == XSD.integer:
                continue
            _replace(graph, subject, predicate, value, Literal(number, datatype=XSD.integer))
            statistics["integer_literals_normalized"] += 1

    for predicate in _BOOLEAN_PREDICATES:
        for subject, value in list(graph.subject_objects(predicate)):
            boolean = _boolean_value(value)
            if boolean is None or value.datatype == XSD.boolean:
                continue
            _replace(graph, subject, predicate, value, Literal(boolean, datatype=XSD.boolean))
            statistics["boolean_literals_normalized"] += 1

    for predicate in _STRING_PREDICATES:
        for subject, value in list(graph.subject_objects(predicate)):
            if not isinstance(value, Literal):
                continue
            if predicate == SH.message and (
                value.language or value.datatype == XSD.string
            ):
                continue
            if predicate != SH.message and (
                not value.language and value.datatype == XSD.string
            ):
                continue
            _replace(graph, subject, predicate, value, Literal(str(value), datatype=XSD.string))
            statistics["string_literals_normalized"] += 1

    for head in list(graph.objects(None, SH.languageIn)):
        current = head
        visited = set()
        while isinstance(current, BNode) and current not in visited:
            visited.add(current)
            value = graph.value(current, RDF.first)
            if isinstance(value, Literal) and (
                value.language or value.datatype != XSD.string
            ):
                _replace(
                    graph,
                    current,
                    RDF.first,
                    value,
                    Literal(str(value), datatype=XSD.string),
                )
                statistics["string_literals_normalized"] += 1
            current = graph.value(current, RDF.rest)


def normalize_node_kinds(
    graph: Graph,
    statistics: MutableMapping[str, int],
) -> None:
    """Collapse repeated standard node kinds to their exact set-union form."""
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
        elif replacement is not None:
            graph.add((subject, SH.nodeKind, replacement))
            statistics["collapsed_shapes"] += 1
        else:
            statistics["skipped_shapes"] += 1


def _collapse_numeric_parameters(
    graph: Graph,
    predicates: Iterable[URIRef],
    chooser,
    statistics: MutableMapping[str, int],
) -> None:
    for predicate in predicates:
        for subject in set(graph.subjects(predicate, None)):
            values = list(graph.objects(subject, predicate))
            if len(values) <= 1:
                continue
            comparable = [(value, _decimal_value(value)) for value in values]
            if any(number is None for _, number in comparable):
                continue
            selected = chooser(comparable, key=lambda item: item[1])[0]
            graph.remove((subject, predicate, None))
            graph.add((subject, predicate, selected))
            statistics["numeric_parameters_collapsed"] += len(values) - 1


def _collapse_boolean_parameters(
    graph: Graph,
    statistics: MutableMapping[str, int],
) -> None:
    for predicate in _BOOLEAN_PREDICATES:
        for subject in set(graph.subjects(predicate, None)):
            values = list(graph.objects(subject, predicate))
            if len(values) <= 1:
                continue
            booleans = [_boolean_value(value) for value in values]
            if any(value is None for value in booleans):
                continue
            selected = any(booleans) if predicate in _TRUE_IS_STRICTER else all(booleans)
            graph.remove((subject, predicate, None))
            graph.add((subject, predicate, Literal(selected, datatype=XSD.boolean)))
            statistics["boolean_parameters_collapsed"] += len(values) - 1


def _collapse_severity(graph: Graph, statistics: MutableMapping[str, int]) -> None:
    rank = {SH.Info: 1, SH.Warning: 2, SH.Violation: 3}
    for subject in set(graph.subjects(SH.severity, None)):
        values = list(graph.objects(subject, SH.severity))
        if len(values) <= 1 or any(value not in rank for value in values):
            continue
        selected = max(values, key=lambda value: rank[value])
        graph.remove((subject, SH.severity, None))
        graph.add((subject, SH.severity, selected))
        statistics["severity_parameters_collapsed"] += len(values) - 1


def _merge_list_parameters(
    graph: Graph,
    predicate: URIRef,
    operation: str,
    statistics: MutableMapping[str, int],
) -> None:
    for subject in set(graph.subjects(predicate, None)):
        heads = list(graph.objects(subject, predicate))
        if len(heads) <= 1:
            continue
        lists = [_list_members(graph, head) for head in heads]
        if any(items is None for items in lists):
            continue
        if operation == "intersection":
            selected = set(lists[0])
            for items in lists[1:]:
                selected &= set(items)
        else:
            selected = {item for items in lists for item in items}
        for head in heads:
            _remove_list(graph, head)
        graph.remove((subject, predicate, None))
        graph.add((subject, predicate, _list_head(graph, sorted(selected, key=str))))
        statistics["list_parameters_merged"] += len(heads) - 1


def _conjoin_parameter_values(
    graph: Graph,
    predicate: URIRef,
    statistics_key: str,
    statistics: MutableMapping[str, int],
) -> None:
    for subject in set(graph.subjects(predicate, None)):
        values = list(graph.objects(subject, predicate))
        if len(values) <= 1:
            continue
        members = []
        for value in values:
            member = BNode()
            graph.add((member, predicate, value))
            members.append(member)
        graph.remove((subject, predicate, None))
        graph.add((subject, SH["and"], _list_head(graph, members)))
        statistics[statistics_key] += len(values) - 1


def _conjoin_qualified_value_shapes(
    graph: Graph,
    statistics: MutableMapping[str, int],
) -> None:
    """Replace repeated qualified value shapes with one conjunctive value shape."""
    for subject in set(graph.subjects(SH.qualifiedValueShape, None)):
        values = list(graph.objects(subject, SH.qualifiedValueShape))
        if len(values) <= 1:
            continue
        combined_shape = BNode()
        graph.add((combined_shape, SH["and"], _list_head(graph, values)))
        graph.remove((subject, SH.qualifiedValueShape, None))
        graph.add((subject, SH.qualifiedValueShape, combined_shape))
        statistics["qualified_shapes_conjoined"] += len(values) - 1


def _conjoin_patterns(graph: Graph, statistics: MutableMapping[str, int]) -> None:
    for subject in set(graph.subjects(SH.pattern, None)):
        patterns = list(graph.objects(subject, SH.pattern))
        if len(patterns) <= 1:
            continue
        flags = list(graph.objects(subject, SH.flags))
        members = []
        for pattern in patterns:
            member = BNode()
            graph.add((member, SH.pattern, pattern))
            for flag in flags[:1]:
                graph.add((member, SH.flags, flag))
            members.append(member)
        graph.remove((subject, SH.pattern, None))
        graph.remove((subject, SH.flags, None))
        graph.add((subject, SH["and"], _list_head(graph, members)))
        statistics["pattern_parameters_conjoined"] += len(patterns) - 1


def _merge_flags(graph: Graph) -> None:
    for subject in set(graph.subjects(SH.flags, None)):
        values = list(graph.objects(subject, SH.flags))
        if len(values) <= 1 or any(not isinstance(value, Literal) for value in values):
            continue
        characters = "".join(sorted(set("".join(str(value) for value in values))))
        graph.remove((subject, SH.flags, None))
        graph.add((subject, SH.flags, Literal(characters, datatype=XSD.string)))


def _repair_shape_types(graph: Graph, statistics: MutableMapping[str, int]) -> None:
    for shape in set(graph.subjects(RDF.type, SH.NodeShape)) & set(
        graph.subjects(RDF.type, SH.PropertyShape)
    ):
        if len(set(graph.objects(shape, SH.path))) == 1:
            graph.remove((shape, RDF.type, SH.NodeShape))
        elif not set(graph.subjects(SH.property, shape)):
            graph.remove((shape, RDF.type, SH.PropertyShape))
        else:
            continue
        statistics["shape_types_repaired"] += 1

    for shape in set(graph.subjects(RDF.type, SH.NodeShape)):
        if set(graph.objects(shape, SH.path)) or set(graph.subjects(SH.property, shape)):
            graph.remove((shape, RDF.type, SH.NodeShape))
            graph.add((shape, RDF.type, SH.PropertyShape))
            statistics["shape_types_repaired"] += 1


def normalize_astrea_graph(graph: Graph) -> Dict[str, int]:
    """Apply conservative, auditable repairs to an Astrea SHACL graph."""
    statistics = _new_statistics()
    normalize_malformed_lists(graph, statistics)
    normalize_literal_datatypes(graph, statistics)
    normalize_node_kinds(graph, statistics)
    _collapse_numeric_parameters(graph, _MINIMUM_PREDICATES, max, statistics)
    _collapse_numeric_parameters(graph, _MAXIMUM_PREDICATES, min, statistics)
    _collapse_boolean_parameters(graph, statistics)
    _collapse_severity(graph, statistics)
    _merge_list_parameters(graph, SH["in"], "intersection", statistics)
    _merge_list_parameters(graph, SH.languageIn, "intersection", statistics)
    _merge_list_parameters(graph, SH.ignoredProperties, "union", statistics)
    _merge_flags(graph)
    _conjoin_patterns(graph, statistics)
    _conjoin_parameter_values(
        graph, SH.datatype, "datatype_parameters_conjoined", statistics
    )
    _conjoin_qualified_value_shapes(graph, statistics)
    _repair_shape_types(graph, statistics)
    return statistics


def graph_copy(source: Graph) -> Graph:
    """Return an identity-preserving copy with the same namespace bindings."""
    output = Graph(bind_namespaces="none")
    for prefix, namespace in source.namespace_manager.namespaces():
        output.bind(prefix, namespace, override=True, replace=True)
    for triple in source:
        output.add(triple)
    return output


def shape_roots(graph: Graph) -> Set[Any]:
    """Return explicit and structurally implied top-level SHACL shape roots."""
    roots = set(graph.subjects(RDF.type, SH.NodeShape))
    roots.update(graph.subjects(RDF.type, SH.PropertyShape))
    for predicate in (SH.targetClass, SH.targetNode, SH.targetObjectsOf, SH.targetSubjectsOf):
        roots.update(graph.subjects(predicate, None))
    roots.update(graph.objects(None, SH.property))
    return roots


def _closure(graph: Graph, root: Any) -> Set[Any]:
    nodes = {root}
    pending = [root]
    while pending:
        subject = pending.pop()
        for obj in graph.objects(subject, None):
            if isinstance(obj, BNode) and obj not in nodes:
                nodes.add(obj)
                pending.append(obj)
    return nodes


def _quarantine_candidate(graph: Graph, focus_node: Any) -> Any:
    if set(graph.subjects(SH.property, focus_node)):
        return focus_node
    roots = shape_roots(graph)
    if focus_node in roots:
        return focus_node
    visited = {focus_node}
    pending = [focus_node]
    while pending:
        current = pending.pop(0)
        for subject in graph.subjects(None, current):
            if subject in roots:
                return subject
            if isinstance(subject, BNode) and subject not in visited:
                visited.add(subject)
                pending.append(subject)
    return focus_node


def quarantine_focus_nodes(
    graph: Graph,
    focus_nodes: Iterable[Any],
    quarantine: Graph,
) -> int:
    """Remove non-conforming shape fragments while preserving them separately."""
    removed = 0
    processed = set()
    for focus_node in focus_nodes:
        candidate = _quarantine_candidate(graph, focus_node)
        if candidate in processed or not any(graph.triples((candidate, None, None))):
            continue
        processed.add(candidate)
        closure = _closure(graph, candidate)
        for prefix, namespace in graph.namespace_manager.namespaces():
            quarantine.bind(prefix, namespace, override=False, replace=False)
        for subject in closure:
            for triple in graph.triples((subject, None, None)):
                quarantine.add(triple)
        graph.remove((None, None, candidate))
        for subject in closure:
            graph.remove((subject, None, None))
        removed += 1
    return removed
