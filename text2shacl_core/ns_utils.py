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
graph itself, while still letting the caller override both (the UI exposes an
editable namespace field and an editable prefixes panel).
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Tuple

from rdflib import Graph, RDF, RDFS, OWL


# Prefixes every SHACL document needs regardless of the source ontology.
_ESSENTIAL_PREFIXES: Dict[str, str] = {
    "sh":   "http://www.w3.org/ns/shacl#",
    "rdf":  "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl":  "http://www.w3.org/2002/07/owl#",
    "xsd":  "http://www.w3.org/2001/XMLSchema#",
}

# Metadata predicates the generator prompts emit on every PropertyShape
# (era:affectedClass, era:affectedProperty, era:scope, era:rinfIndex). They live
# in the ontology base namespace, so we bind the base prefix to it and also keep
# an `<base>shapes/` namespace for the generated shape subjects.
_SHAPES_SEGMENT = "shapes/"


def derive_base_namespace(graph: Graph) -> str:
    """
    Best-effort guess of the ontology's base namespace.

    Strategy:
      1. The namespace of the owl:Ontology subject, if declared.
      2. Otherwise the most common namespace among declared classes/properties.
      3. Otherwise the namespace bound to the empty or first non-standard prefix.
      4. Fallback to a neutral example namespace.
    """
    # 1. owl:Ontology subject
    for onto in graph.subjects(RDF.type, OWL.Ontology):
        ns = _split_namespace(str(onto))
        if ns:
            return ns

    # 2. Most common namespace among classes and properties
    counter: Counter = Counter()
    for s in set(graph.subjects(RDF.type, OWL.Class)) | set(graph.subjects(RDF.type, RDFS.Class)):
        ns = _split_namespace(str(s))
        if ns:
            counter[ns] += 1
    for ptype in (OWL.ObjectProperty, OWL.DatatypeProperty, RDF.Property):
        for s in graph.subjects(RDF.type, ptype):
            ns = _split_namespace(str(s))
            if ns:
                counter[ns] += 1
    if counter:
        return counter.most_common(1)[0][0]

    # 3. Bound prefixes (skip the well-known standard ones)
    standard = set(_ESSENTIAL_PREFIXES.values()) | {
        "http://www.w3.org/2004/02/skos/core#",
    }
    for prefix, ns in graph.namespaces():
        if str(ns) not in standard:
            return str(ns)

    # 4. Neutral fallback
    return "https://example.org/ontology/"


def _split_namespace(uri: str) -> str:
    """Return the namespace part of a URI (everything up to the last # or /)."""
    if "#" in uri:
        return uri.rsplit("#", 1)[0] + "#"
    if "/" in uri:
        return uri.rsplit("/", 1)[0] + "/"
    return ""


def shapes_namespace(base_ns: str) -> str:
    """Namespace used for generated shape subjects (base + 'shapes/')."""
    if base_ns.endswith("#"):
        return base_ns[:-1] + "/" + _SHAPES_SEGMENT
    return base_ns + _SHAPES_SEGMENT


def build_prefix_block(graph: Graph, base_ns: str, base_prefix: str = "onto") -> str:
    """
    Build a Turtle @prefix block:
      * essential SHACL/RDF/OWL/XSD prefixes
      * the ontology base namespace (bound to `base_prefix`)
      * `<base_prefix>-sh:` for generated shape subjects
      * every additional prefix already bound in the source graph

    The result is what seeds the editable prefixes panel in the UI. The generator
    prompts reference era: / era-sh:, so we also alias those to the base/shapes
    namespaces, keeping the prompts working for any ontology without edits.
    """
    lines: Dict[str, str] = {}

    for prefix, ns in _ESSENTIAL_PREFIXES.items():
        lines[prefix] = ns

    # Carry over prefixes bound in the source graph (these win over guesses).
    for prefix, ns in graph.namespaces():
        if prefix:
            lines[prefix] = str(ns)

    sh_ns = shapes_namespace(base_ns)

    # Bind the base namespace + a shapes namespace under stable prefixes.
    lines.setdefault(base_prefix, base_ns)
    lines.setdefault(f"{base_prefix}-sh", sh_ns)

    # Neutral, domain-agnostic prefix for the subjects of generated shapes.
    # Used by the generalized Rule → Shape prompt (so shapes are not tied to any
    # particular project's naming like era-sh:).
    lines.setdefault("shape", sh_ns)

    # The generator prompts are written against era: / era-sh:. Alias them to the
    # ontology base/shapes namespaces so generated metadata (affectedClass, etc.)
    # and shape subjects resolve for any ontology. If the ontology *is* ERA these
    # already match.
    lines.setdefault("era", base_ns)
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
