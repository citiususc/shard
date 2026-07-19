"""
ns_utils.py  (br2shacl-ui)

Genericity helpers so the pipeline is not hard-wired to the ERA namespace.

text2shacl assumed a single ontology (ERA, http://data.europa.eu/949/) in two
places:

  * utils.get_owl_properties_with_domain(g, namespace="http://data.europa.eu/949/")
    filters out every property/domain outside the ERA namespace.
  * multiagent.run_shacl_generation hard-codes an ERA-specific @prefix block and
    the era: / era-sh: shape namespaces.

For an arbitrary uploaded ontology those assumptions discard everything. These
helpers derive a sensible base namespace and a matching prefix block from the
graph itself, while still letting the caller override both (the UI exposes
separate ontology/shape namespace fields and an editable prefixes panel).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from rdflib import Graph, RDF, RDFS, OWL, URIRef
from rdflib.namespace import split_uri


# Prefixes every SHACL document needs regardless of the source ontology.
_ESSENTIAL_PREFIXES: Dict[str, str] = {
    "sh":   "http://www.w3.org/ns/shacl#",
    "rdf":  "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl":  "http://www.w3.org/2002/07/owl#",
    "xsd":  "http://www.w3.org/2001/XMLSchema#",
}

# Conventional aliases for broadly reused vocabularies. They are added only
# when their namespace occurs in the uploaded graph and no source prefix is
# already bound to that namespace.
_KNOWN_PREFIXES: Dict[str, str] = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "geosparql": "http://www.opengis.net/ont/geosparql#",
    "prov": "http://www.w3.org/ns/prov#",
    "sf": "http://www.opengis.net/ont/sf#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "time": "http://www.w3.org/2006/time#",
    "wgs": "http://www.w3.org/2003/01/geo/wgs84_pos#",
}

_SHAPES_SEGMENT = "shapes/"

_STANDARD_NAMESPACES = set(_ESSENTIAL_PREFIXES.values()) | {
    "http://www.w3.org/2004/02/skos/core#",
    "http://purl.org/dc/elements/1.1/",
    "http://purl.org/dc/terms/",
}

_ONTOLOGY_TERM_TYPES = (
    OWL.Class,
    RDFS.Class,
    OWL.ObjectProperty,
    OWL.DatatypeProperty,
    OWL.AnnotationProperty,
    RDF.Property,
)


def _ontology_terms(graph: Graph) -> set[URIRef]:
    """Return unique named classes and properties used for namespace coverage."""
    terms: set[URIRef] = set()
    for rdf_type in _ONTOLOGY_TERM_TYPES:
        terms.update(
            subject for subject in graph.subjects(RDF.type, rdf_type)
            if isinstance(subject, URIRef)
        )
    return terms


def _ontology_iris(graph: Graph) -> List[str]:
    return sorted({
        str(subject) for subject in graph.subjects(RDF.type, OWL.Ontology)
        if isinstance(subject, URIRef)
    })


def _used_namespaces(graph: Graph) -> set[str]:
    """Return namespaces of all URI references occurring in graph triples."""
    namespaces = set()
    for subject, predicate, obj in graph:
        for value in (subject, predicate, obj):
            if isinstance(value, URIRef):
                namespace = _split_namespace(str(value))
                if namespace:
                    namespaces.add(namespace)
    return namespaces


def inferred_known_prefixes(graph: Graph) -> Dict[str, str]:
    """Return conventional aliases needed by used but unbound namespaces."""
    source_bindings = {
        str(prefix): str(namespace)
        for prefix, namespace in graph.namespaces()
        if prefix
    }
    bound_namespaces = set(source_bindings.values()) | set(_ESSENTIAL_PREFIXES.values())
    used_namespaces = _used_namespaces(graph)
    inferred = {}
    for prefix, namespace in _KNOWN_PREFIXES.items():
        if (
            namespace in used_namespaces
            and namespace not in bound_namespaces
            and prefix not in source_bindings
        ):
            inferred[prefix] = namespace
    return inferred


def _namespace_matches_ontology(namespace: str, ontology_iris: List[str]) -> bool:
    stem = namespace.rstrip("/#:")
    return any(
        iri.startswith(namespace) or iri.rstrip("/#:") == stem
        for iri in ontology_iris
    )


def analyze_base_namespace(graph: Graph) -> Dict[str, Any]:
    """Describe and select the ontology's primary vocabulary namespace."""
    terms = _ontology_terms(graph)
    ontology_iris = _ontology_iris(graph)
    counts: Counter[str] = Counter()
    prefixes_by_namespace: Dict[str, List[str]] = defaultdict(list)

    for term in terms:
        namespace = _split_namespace(str(term))
        if namespace and namespace not in _STANDARD_NAMESPACES:
            counts[namespace] += 1

    for prefix, namespace in graph.namespaces():
        value = str(namespace)
        if value in _STANDARD_NAMESPACES:
            continue
        name = str(prefix or "")
        if name not in prefixes_by_namespace[value]:
            prefixes_by_namespace[value].append(name)

    candidate_namespaces = set(counts) | set(prefixes_by_namespace)
    candidate_namespaces.update(
        namespace for namespace in (_split_namespace(iri) for iri in ontology_iris)
        if namespace and namespace not in _STANDARD_NAMESPACES
    )

    candidates = []
    total_terms = len(terms)
    for namespace in candidate_namespaces:
        term_count = counts.get(namespace, 0)
        candidates.append({
            "namespace": namespace,
            "term_count": term_count,
            "coverage": round(term_count / total_terms, 4) if total_terms else 0.0,
            "prefixes": sorted(prefixes_by_namespace.get(namespace, [])),
            "ontology_hint": _namespace_matches_ontology(namespace, ontology_iris),
        })

    candidates.sort(key=lambda item: (
        -item["term_count"],
        -int(item["ontology_hint"]),
        -int("" in item["prefixes"]),
        item["namespace"],
    ))

    selected: Optional[Dict[str, Any]] = None
    detected_by = "none"
    if counts:
        selected = candidates[0]
        detected_by = "term_coverage"
    elif ontology_iris:
        hinted = [candidate for candidate in candidates if candidate["ontology_hint"]]
        if hinted:
            hinted.sort(key=lambda item: (-len(item["namespace"]), item["namespace"]))
            selected = hinted[0]
        else:
            namespace = _split_namespace(ontology_iris[0])
            selected = next(
                (candidate for candidate in candidates if candidate["namespace"] == namespace),
                None,
            )
        detected_by = "ontology_iri" if selected else "none"
    elif candidates:
        selected = candidates[0]
        detected_by = "declared_prefix"

    namespace = selected["namespace"] if selected else ""
    term_count = selected["term_count"] if selected else 0
    return {
        "namespace": namespace,
        "detected_by": detected_by,
        "term_count": term_count,
        "total_terms": total_terms,
        "coverage": round(term_count / total_terms, 4) if total_terms else 0.0,
        "confidence": round(term_count / total_terms, 4) if total_terms else (0.5 if namespace else 0.0),
        "candidates": candidates,
    }


def derive_base_namespace(graph: Graph) -> str:
    """
    Best-effort guess of the ontology's base namespace.

    Named classes and properties are the primary evidence. Ontology declarations
    and bound prefixes are deterministic fallbacks when no vocabulary terms exist.
    """
    return analyze_base_namespace(graph)["namespace"]


def _split_namespace(uri: str) -> str:
    """Return the namespace part of an IRI, including URN-style namespaces."""
    try:
        namespace, _ = split_uri(URIRef(uri))
        if namespace:
            return str(namespace)
    except ValueError:
        pass
    if "#" in uri:
        return uri.rsplit("#", 1)[0] + "#"
    if "/" in uri:
        return uri.rsplit("/", 1)[0] + "/"
    if uri.startswith("urn:") and ":" in uri[4:]:
        return uri.rsplit(":", 1)[0] + ":"
    return ""


def shapes_namespace(base_ns: str) -> str:
    """Namespace used for generated shape subjects (base + 'shapes/')."""
    if not base_ns:
        return ""
    if base_ns.endswith("#"):
        return base_ns[:-1] + "/" + _SHAPES_SEGMENT
    if base_ns.endswith("/"):
        return base_ns + _SHAPES_SEGMENT
    if base_ns.endswith(":"):
        return base_ns + "shapes:"
    return base_ns + "/" + _SHAPES_SEGMENT


def derive_shapes_namespace(graph: Graph, base_ns: str) -> Tuple[str, str]:
    """Return the generated-shape namespace and whether it was bound or derived."""
    namespaces = {str(prefix or ""): str(namespace) for prefix, namespace in graph.namespaces()}
    for prefix in ("shape", "onto-sh"):
        if namespaces.get(prefix):
            return namespaces[prefix], "declared_prefix"
    derived = shapes_namespace(base_ns)
    if derived and derived in namespaces.values():
        return derived, "declared_prefix"
    return derived, "derived" if base_ns else "none"


def derive_shape_prefix(graph: Graph, shape_ns: str) -> Tuple[str, str]:
    """Return the preferred named prefix for generated shape subjects."""
    candidates = sorted({
        str(prefix)
        for prefix, namespace in graph.namespaces()
        if prefix and str(namespace) == shape_ns
    }, key=lambda prefix: (
        prefix != "shape",
        not prefix.endswith("-sh"),
        "shape" not in prefix,
        len(prefix),
        prefix,
    ))
    if candidates:
        return candidates[0], "declared_prefix"

    occupied = {str(prefix) for prefix, _ in graph.namespaces() if prefix}
    if "shape" not in occupied:
        return "shape", "default"
    index = 2
    while f"shape{index}" in occupied:
        index += 1
    return f"shape{index}", "default"


def build_prefix_block(
    graph: Graph,
    base_ns: str,
    base_prefix: str = "onto",
    *,
    shape_ns: Optional[str] = None,
    shape_prefix: Optional[str] = None,
    include_legacy_aliases: bool = False,
) -> str:
    """
    Build a Turtle @prefix block:
      * essential SHACL/RDF/OWL/XSD prefixes
      * source prefixes declared by the ontology
      * the ontology base namespace bound to `base_prefix` only when the
        ontology does not already provide a named prefix for it
      * one preferred prefix for generated shape subjects

    ERA aliases are added only when explicitly requested by the legacy
    property-first pipeline.
    """
    lines: Dict[str, str] = {}

    for prefix, ns in _ESSENTIAL_PREFIXES.items():
        lines[prefix] = ns

    source_namespaces: Dict[str, str] = {}

    # Carry over prefixes bound in the source graph (these win over guesses).
    for prefix, ns in graph.namespaces():
        if prefix:
            value = str(ns)
            source_namespaces[str(prefix)] = value
            lines[prefix] = value

    for prefix, namespace in inferred_known_prefixes(graph).items():
        lines.setdefault(prefix, namespace)

    sh_ns = shape_ns or shapes_namespace(base_ns)
    preferred_shape_prefix = shape_prefix or derive_shape_prefix(graph, sh_ns)[0]

    # A neutral ontology alias is only useful when the source ontology has no
    # named prefix for its primary vocabulary namespace.
    has_source_base_prefix = any(
        namespace == base_ns for namespace in source_namespaces.values()
    )
    if base_ns and not has_source_base_prefix:
        lines.setdefault(base_prefix, base_ns)

    if sh_ns and preferred_shape_prefix:
        existing = lines.get(preferred_shape_prefix)
        if existing and existing != sh_ns:
            raise ValueError(
                f"Shape prefix '{preferred_shape_prefix}' is already bound to {existing}."
            )
        lines[preferred_shape_prefix] = sh_ns

    if include_legacy_aliases:
        if base_ns:
            lines.setdefault("era", base_ns)
        if sh_ns:
            lines.setdefault("era-sh", sh_ns)

    ordered = sorted(lines.items(), key=lambda kv: (kv[0] != base_prefix, kv[0]))
    return "\n".join(f"@prefix {p}: <{ns}> ." for p, ns in ordered) + "\n"


def split_prefix_block(prefix_block: str) -> List[Tuple[str, str]]:
    """Parse an edited @prefix block back into (prefix, namespace) pairs."""
    import re

    pairs: List[Tuple[str, str]] = []
    pattern = re.compile(r"@prefix\s+([^:]+):\s*<([^>]+)>\s*\.")
    for match in pattern.finditer(prefix_block or ""):
        pairs.append((match.group(1).strip(), match.group(2).strip()))
    return pairs


def ensure_legacy_era_aliases(prefix_block: str, base_ns: str, shape_ns: str = "") -> str:
    """Add aliases required by the legacy ERA-oriented property-first prompts."""
    pairs = dict(split_prefix_block(prefix_block))
    additions = []
    if base_ns and "era" not in pairs:
        additions.append(("era", base_ns))
    effective_shape_ns = shape_ns or shapes_namespace(base_ns)
    if effective_shape_ns and "era-sh" not in pairs:
        additions.append(("era-sh", effective_shape_ns))
    if not additions:
        return prefix_block
    current = (prefix_block or "").rstrip()
    suffix = "\n".join(f"@prefix {prefix}: <{namespace}> ." for prefix, namespace in additions)
    return f"{current}\n{suffix}\n" if current else f"{suffix}\n"
