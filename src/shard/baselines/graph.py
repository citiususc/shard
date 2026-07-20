"""Internal RDF graph-copying helpers for baseline operations."""

from typing import Any, Dict, Iterable, Set

from rdflib import BNode, Graph, URIRef

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

