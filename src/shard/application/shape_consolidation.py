"""Consolidate per-rule SHACL fragments into target-class NodeShapes."""

from rdflib import BNode, Graph, Literal, RDF, SH, URIRef


def _qname(graph, node):
    try:
        return graph.qname(node)
    except Exception:
        return str(node)


def _local_name(value):
    value = str(value or "").rstrip("/#")
    return value.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or "Resource"


def _shape_namespace(graph):
    namespaces = dict(graph.namespace_manager.namespaces())
    if namespaces.get("shape"):
        return namespaces["shape"]
    candidates = [
        (str(prefix), namespace)
        for prefix, namespace in namespaces.items()
        if prefix and (str(prefix).endswith("-sh") or "shape" in str(prefix).lower())
    ]
    candidates.sort(key=lambda item: (not item[0].endswith("-sh"), len(item[0]), item[0]))
    return candidates[0][1] if candidates else None


def _node_key(graph, node):
    try:
        return node.n3(graph.namespace_manager)
    except Exception:
        return str(node)


def _literal_number(value):
    if not isinstance(value, Literal):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _copy_bnode_closure(source_graph, target_graph, node, seen=None):
    seen = seen or set()
    if not isinstance(node, BNode) or node in seen:
        return
    seen.add(node)
    for predicate, obj in source_graph.predicate_objects(node):
        target_graph.add((node, predicate, obj))
        _copy_bnode_closure(source_graph, target_graph, obj, seen)


_STRUCTURAL_BNODE_CONSTRAINTS = {
    SH["and"],
    SH["or"],
    SH["xone"],
    SH["not"],
    SH.node,
    SH.property,
    SH.qualifiedValueShape,
    SH.sparql,
}


def _bnode_has_shacl_predicate(source_graph, node):
    if not isinstance(node, BNode):
        return False
    return any(str(predicate).startswith(str(SH)) for predicate in source_graph.predicates(node, None))


def _merge_constraint_value(values, predicate, obj, logger):
    existing_keys = {_node_key(Graph(), value) for value in values}
    obj_key = _node_key(Graph(), obj)
    if obj_key in existing_keys:
        return values
    if not values:
        return [obj]

    if predicate in {SH.minCount, SH.minLength, SH.minInclusive, SH.minExclusive}:
        obj_number = _literal_number(obj)
        current_numbers = [_literal_number(value) for value in values]
        if obj_number is not None and all(value is not None for value in current_numbers):
            best = max(values + [obj], key=lambda value: _literal_number(value))
            return [best]

    if predicate in {SH.maxCount, SH.maxLength, SH.maxInclusive, SH.maxExclusive}:
        obj_number = _literal_number(obj)
        current_numbers = [_literal_number(value) for value in values]
        if obj_number is not None and all(value is not None for value in current_numbers):
            best = min(values + [obj], key=lambda value: _literal_number(value))
            return [best]

    if predicate in {SH.datatype, SH["class"], SH.nodeKind, SH.severity, SH.node}:
        logger.warn(
            f"[batch-rule] conflicting {predicate} values while consolidating; keeping the first."
        )
        return values

    return values + [obj]


def _add_property_constraints(grouped, source_graph, bnode_graph, class_uri, path_uri, source_node, logger):
    path_constraints = grouped.setdefault(class_uri, {}).setdefault(path_uri, {})
    for predicate, obj in source_graph.predicate_objects(source_node):
        if predicate in {RDF.type, SH.path, SH.targetClass}:
            continue
        if (
            isinstance(obj, BNode)
            and predicate not in _STRUCTURAL_BNODE_CONSTRAINTS
            and _bnode_has_shacl_predicate(source_graph, obj)
        ):
            logger.warn(
                f"[batch-rule] dropping malformed blank-node value for {predicate}; "
                "SHACL constraint blank nodes are only kept for structural shape predicates."
            )
            continue
        if isinstance(obj, BNode):
            _copy_bnode_closure(source_graph, bnode_graph, obj)
        values = path_constraints.setdefault(predicate, [])
        path_constraints[predicate] = _merge_constraint_value(values, predicate, obj, logger)


def _add_node_constraints(
    grouped,
    source_graph,
    bnode_graph,
    class_uri,
    source_node,
    logger,
):
    incoming = {}
    for predicate, obj in source_graph.predicate_objects(source_node):
        if predicate in {RDF.type, SH.targetClass, SH.property}:
            continue
        if isinstance(obj, BNode):
            _copy_bnode_closure(source_graph, bnode_graph, obj)
        values = incoming.setdefault(predicate, [])
        incoming[predicate] = _merge_constraint_value(values, predicate, obj, logger)
    if not incoming:
        return
    constraints = grouped.setdefault(class_uri, {})
    for predicate, values in incoming.items():
        for obj in values:
            existing = constraints.setdefault(predicate, [])
            constraints[predicate] = _merge_constraint_value(
                existing, predicate, obj, logger
            )


def _collect_consolidation_input(
    shape_graph,
    bnode_graph,
    grouped,
    node_constraints,
    logger,
):
    for subject in shape_graph.subjects(RDF.type, SH.PropertyShape):
        paths = [value for value in shape_graph.objects(subject, SH.path) if isinstance(value, URIRef)]
        classes = [value for value in shape_graph.objects(subject, SH.targetClass) if isinstance(value, URIRef)]
        for class_uri in classes:
            for path_uri in paths:
                _add_property_constraints(grouped, shape_graph, bnode_graph, class_uri, path_uri, subject, logger)

    for subject in shape_graph.subjects(RDF.type, SH.NodeShape):
        classes = [value for value in shape_graph.objects(subject, SH.targetClass) if isinstance(value, URIRef)]
        for class_uri in classes:
            _add_node_constraints(
                node_constraints,
                shape_graph,
                bnode_graph,
                class_uri,
                subject,
                logger,
            )
        for prop_node in shape_graph.objects(subject, SH.property):
            paths = [value for value in shape_graph.objects(prop_node, SH.path) if isinstance(value, URIRef)]
            for class_uri in classes:
                for path_uri in paths:
                    _add_property_constraints(grouped, shape_graph, bnode_graph, class_uri, path_uri, prop_node, logger)


def _strip_prefix_declarations(turtle):
    return str(turtle or "").strip()


def consolidate_rule_shapes(generated_shapes, prefixes, shape_namespace="", shape_prefix="shape"):
    """Group generated PropertyShapes under NodeShapes by sh:targetClass."""
    from shard.observability import logger

    grouped = {}
    node_constraints = {}
    bnode_graph = Graph(bind_namespaces="none")
    consolidation = []
    namespace_source = Graph(bind_namespaces="none")
    namespace_source.parse(data=prefixes or "", format="turtle")

    for item in generated_shapes:
        shape = item.get("shape") or ""
        if not shape.strip() or not item.get("valid"):
            continue
        try:
            shape_graph = Graph(bind_namespaces="none")
            shape_graph.parse(data=f"{prefixes or ''}\n{shape}", format="turtle")
        except Exception as exc:
            item["consolidation_error"] = str(exc)
            continue

        for prefix, namespace in shape_graph.namespace_manager.namespaces():
            namespace_source.bind(prefix, namespace, replace=True)
            bnode_graph.bind(prefix, namespace, replace=True)
        _collect_consolidation_input(
            shape_graph,
            bnode_graph,
            grouped,
            node_constraints,
            logger,
        )

    out_graph = Graph(bind_namespaces="none")
    for prefix, namespace in namespace_source.namespace_manager.namespaces():
        out_graph.bind(prefix, namespace, replace=True)
    shape_ns = URIRef(shape_namespace) if shape_namespace else _shape_namespace(namespace_source)
    if shape_ns is None:
        shape_ns = URIRef("urn:shape:")
    if shape_prefix:
        out_graph.bind(shape_prefix, shape_ns, override=True, replace=True)

    for class_uri in sorted(set(grouped) | set(node_constraints), key=str):
        subject = URIRef(f"{shape_ns}{_local_name(class_uri)}Shape")
        out_graph.add((subject, RDF.type, SH.NodeShape))
        out_graph.add((subject, SH.targetClass, class_uri))
        for predicate in sorted(node_constraints.get(class_uri, {}), key=str):
            for obj in node_constraints[class_uri][predicate]:
                out_graph.add((subject, predicate, obj))
                _copy_bnode_closure(bnode_graph, out_graph, obj)
        if node_constraints.get(class_uri):
            consolidation.append({
                "shape": _qname(out_graph, subject),
                "kind": "NodeShape",
                "target_class": _qname(out_graph, class_uri),
                "path": "",
            })

        path_map = grouped.get(class_uri, {})
        for path_uri in sorted(path_map, key=str):
            prop_node = BNode()
            out_graph.add((subject, SH.property, prop_node))
            out_graph.add((prop_node, SH.path, path_uri))
            for predicate in sorted(path_map[path_uri], key=str):
                for obj in path_map[path_uri][predicate]:
                    out_graph.add((prop_node, predicate, obj))
                    _copy_bnode_closure(bnode_graph, out_graph, obj)
            consolidation.append({
                "shape": _qname(out_graph, subject),
                "kind": "NodeShape",
                "target_class": _qname(out_graph, class_uri),
                "path": _qname(out_graph, path_uri),
            })

    node_shapes = _strip_prefix_declarations(out_graph.serialize(format="turtle"))
    return {
        "node_shapes": node_shapes,
        "property_shapes": "",
        "node_shape_map": {
            _qname(out_graph, class_uri): [
                _qname(out_graph, path_uri)
                for path_uri in sorted(grouped.get(class_uri, {}), key=str)
            ]
            for class_uri in set(grouped) | set(node_constraints)
        },
        "consolidation": consolidation,
    }
