"""Build the ontology catalog consumed by SHARD workflows.

The catalog contains classes and properties for the UI together with detected
ontology and generated-shape namespaces and a SHACL-ready prefix block.

Generalised from the original demo service:
  * parses with bind_namespaces="none" so only the ontology's own prefixes show
  * ranks namespace candidates by ontology-term coverage
  * keeps generated-shape namespaces separate from ontology-term namespaces
  * returns generic prefixes without injecting domain-specific aliases

"""

from rdflib import Namespace, RDF, RDFS, OWL, URIRef, BNode, Literal
from shard.domain.ontology import (
    ontology_namespace_analysis,
    ontology_prefix_block,
    ontology_shape_prefix,
    ontology_shapes_namespace,
    parse_ontology_graph,
)
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
DC = Namespace("http://purl.org/dc/elements/1.1/")
DCTERMS = Namespace("http://purl.org/dc/terms/")
SCHEMA = Namespace("http://schema.org/")


def qname(graph, value):
    if not value:
        return ""
    if isinstance(value, BNode):
        return "blank node"
    if isinstance(value, Literal):
        return str(value)
    try:
        return graph.namespace_manager.normalizeUri(value)
    except Exception:
        return f"<{value}>"


def label_for(graph, subject):
    for predicate in (RDFS.label, SKOS.prefLabel, DC.title, DCTERMS.title):
        for value in graph.objects(subject, predicate):
            return str(value)
    tail = str(subject).rstrip("/#").split("/")[-1].split("#")[-1]
    return tail or str(subject)


def first_object(graph, subject, predicates):
    for predicate in predicates:
        for value in graph.objects(subject, predicate):
            return value
    return None


def comment_for(graph, subject):
    for value in graph.objects(subject, RDFS.comment):
        return str(value)
    return ""


def entity_note(kind, iri, domain="", range_value="", comment=""):
    if comment:
        return comment
    if kind == "Class":
        return f"{iri} is an ontology class."
    pieces = [f"{iri} is a {kind}"]
    if domain:
        pieces.append(f"with domain {domain}")
    if range_value:
        pieces.append(f"and range {range_value}")
    return " ".join(pieces) + "."


def add_entity(entities, graph, subject, entity_type, kind):
    iri = qname(graph, subject)
    domain = range_value = ""
    if entity_type == "property":
        domain = qname(graph, first_object(graph, subject, (RDFS.domain, SCHEMA.domainIncludes)))
        range_value = qname(graph, first_object(graph, subject, (RDFS.range, SCHEMA.rangeIncludes)))
    comment = comment_for(graph, subject)
    superclasses = []
    if entity_type == "class":
        superclasses = [
            qname(graph, value)
            for value in graph.objects(subject, RDFS.subClassOf)
            if isinstance(value, URIRef)
        ]

    entities.append({
        "id": f"{entity_type}-{len(entities)}",
        "type": entity_type,
        "label": label_for(graph, subject),
        "iri": iri,
        "full_iri": str(subject),
        "kind": kind,
        "domain": iri if entity_type == "class" else domain,
        "range": "" if entity_type == "class" else range_value,
        "superclasses": superclasses,
        "comment": comment,
        "ontologyNote": entity_note(kind, iri, domain, range_value, comment),
        "businessRule": "",
        "rules": [],
    })


def parse_ontology(filename, content):
    graph = parse_ontology_graph(content, filename)

    entities = []
    seen = set()

    classes = set(graph.subjects(RDF.type, OWL.Class)) | set(graph.subjects(RDF.type, RDFS.Class))
    for subject in sorted(classes, key=str):
        if subject in seen or not isinstance(subject, URIRef):
            continue
        seen.add(subject)
        add_entity(entities, graph, subject, "class", "Class")

    property_types = [
        (OWL.ObjectProperty, "ObjectProperty"),
        (OWL.DatatypeProperty, "DatatypeProperty"),
        (OWL.AnnotationProperty, "DatatypeProperty"),
        (RDF.Property, "DatatypeProperty"),
    ]
    for rdf_type, kind in property_types:
        for subject in sorted(set(graph.subjects(RDF.type, rdf_type)), key=str):
            key = ("property", subject)
            if key in seen or not isinstance(subject, URIRef):
                continue
            seen.add(key)
            add_entity(entities, graph, subject, "property", kind)

    namespace_analysis = ontology_namespace_analysis(graph)
    base_ns = namespace_analysis["namespace"]
    shape_ns, shape_ns_source = ontology_shapes_namespace(graph, base_ns)
    shape_prefix, shape_prefix_source = ontology_shape_prefix(graph, shape_ns)
    declared_namespaces = {
        str(prefix or ""): str(namespace)
        for prefix, namespace in graph.namespaces()
    }
    declared_prefixes = set(declared_namespaces)
    has_primary_prefix = any(
        prefix and namespace == base_ns
        for prefix, namespace in declared_namespaces.items()
    )
    managed_prefixes = []
    if base_ns and not has_primary_prefix and "onto" not in declared_prefixes:
        managed_prefixes.append("onto")
    if shape_ns and shape_prefix not in declared_prefixes:
        managed_prefixes.append(shape_prefix)
    namespace_analysis = {
        **namespace_analysis,
        "shape_namespace": shape_ns,
        "shape_namespace_source": shape_ns_source,
        "shape_prefix": shape_prefix,
        "shape_prefix_source": shape_prefix_source,
        "managed_prefixes": managed_prefixes,
    }
    prefixes = ontology_prefix_block(graph, base_ns, shape_ns, shape_prefix)

    entities.sort(key=lambda item: (item["type"], item["label"].lower()))
    return {
        "prefixes": prefixes,
        "entities": entities,
        "base_namespace": base_ns,
        "shape_namespace": shape_ns,
        "shape_prefix": shape_prefix,
        "namespace_analysis": namespace_analysis,
    }
