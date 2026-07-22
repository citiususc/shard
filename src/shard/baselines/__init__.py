"""Baseline parsing, evidence selection and merge strategies."""

from .evidence import (
    baseline_context_for_roles,
    baseline_context_for_target,
    baseline_context_for_targets,
    focused_baseline_for_roles,
    focused_baseline_for_target,
    focused_baseline_for_targets,
)
from .io import (
    baseline_from_payload,
    normalize_astrea_node_kinds,
    normalize_shacl_cardinalities,
    parse_baseline_shapes,
)
from .merge import merge_shape_documents, merge_shape_graphs

__all__ = [
    "baseline_context_for_roles",
    "baseline_context_for_target",
    "baseline_context_for_targets",
    "baseline_from_payload",
    "focused_baseline_for_roles",
    "focused_baseline_for_target",
    "focused_baseline_for_targets",
    "merge_shape_documents",
    "merge_shape_graphs",
    "normalize_astrea_node_kinds",
    "normalize_shacl_cardinalities",
    "parse_baseline_shapes",
]
