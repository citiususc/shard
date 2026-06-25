#!/usr/bin/env python3
"""
parse-ontology service  (br2shacl-ui)

Parses an uploaded ontology with rdflib and returns its classes and properties
for the UI, plus a derived base namespace and a clean SHACL-ready prefix block.

Generalised from the original demo service:
  * parses with bind_namespaces="none" so only the ontology's own prefixes show
  * derives the base namespace (ns_utils) and returns it so the UI can edit it
  * returns a prefix block aligned with the generator prompts (era:/era-sh:
    aliased to the base/shapes namespaces) for any ontology

Endpoint:  POST http://127.0.0.1:9100/parse-ontology
  request : {"filename": str, "content": str}
  response: {"entities": [...], "prefixes": str, "base_namespace": str}
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "text2shacl_core"))

from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, BNode, Literal
import ns_utils

HOST = "127.0.0.1"
PORT = 9100
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
DC = Namespace("http://purl.org/dc/elements/1.1/")
DCTERMS = Namespace("http://purl.org/dc/terms/")
SCHEMA = Namespace("http://schema.org/")


def guess_format(filename):
    suffix = Path(filename or "").suffix.lower()
    return {
        ".ttl": "turtle", ".trig": "trig", ".nt": "nt", ".nq": "nquads",
        ".rdf": "xml", ".owl": "xml", ".xml": "xml",
    }.get(suffix, "turtle")


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

    entities.append({
        "id": f"{entity_type}-{len(entities)}",
        "type": entity_type,
        "label": label_for(graph, subject),
        "iri": iri,
        "full_iri": str(subject),
        "kind": kind,
        "domain": iri if entity_type == "class" else domain,
        "range": "" if entity_type == "class" else range_value,
        "comment": comment,
        "ontologyNote": entity_note(kind, iri, domain, range_value, comment),
        "businessRule": "",
        "rules": [],
    })


def parse_ontology(filename, content):
    graph = Graph(bind_namespaces="none")
    fmt = guess_format(filename)
    try:
        graph.parse(data=content, format=fmt)
    except Exception:
        fallback = "xml" if fmt != "xml" else "turtle"
        graph.parse(data=content, format=fallback)

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

    base_ns = ns_utils.derive_base_namespace(graph)
    prefixes = ns_utils.build_prefix_block(graph, base_ns)

    entities.sort(key=lambda item: (item["type"], item["label"].lower()))
    return {"prefixes": prefixes, "entities": entities, "base_namespace": base_ns}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_POST(self):
        if self.path != "/parse-ontology":
            self._send_json(404, {"error": "unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            result = parse_ontology(payload.get("filename", ""), payload.get("content", ""))
        except Exception as exc:
            self._send_json(400, {"error": str(exc), "entities": [], "prefixes": "", "base_namespace": ""})
            return
        self._send_json(200, result)


if __name__ == "__main__":
    print(f"parse-ontology service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
