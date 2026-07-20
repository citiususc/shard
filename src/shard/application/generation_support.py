"""Focused helpers shared by the grounded shape generator."""

from __future__ import annotations

import re

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import OWL, RDFS


def clean_shacl_response(response: str | None) -> str:
    """Extract the Turtle/SHACL fragment from a model response."""
    if response is None:
        return ""

    text = response.strip()
    if not text:
        return ""
    if text.strip().strip('"').strip("'") == "SHACL shapes not found":
        return "SHACL shapes not found"

    fenced_blocks = re.findall(
        r"```(?:turtle|ttl)?\s*(.*?)```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_blocks:
        for block in fenced_blocks:
            block = block.strip()
            if any(
                marker in block
                for marker in (
                    "@prefix",
                    " a sh:PropertyShape",
                    " a sh:NodeShape",
                    " a sh:SPARQLConstraint",
                    " sh:path ",
                    " sh:targetClass ",
                )
            ):
                text = block
                break
        else:
            text = fenced_blocks[0].strip()
    else:
        text = re.sub(r"^```(?:turtle|ttl)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    start_candidates = []
    prefix_match = re.search(
        r"(?im)^@prefix\s+\w[\w-]*:\s*<[^>]+>\s*\.\s*$",
        text,
    )
    if prefix_match:
        start_candidates.append(prefix_match.start())
    shape_match = re.search(
        r"(?m)^[ \t]*\w[\w-]*:[\w.-]+\s+a\s+sh:(?:PropertyShape|NodeShape|SPARQLConstraint)\b",
        text,
    )
    if shape_match:
        start_candidates.append(shape_match.start())
    path_match = re.search(
        r"(?m)^[ \t]*\w[\w-]*:[\w.-]+\s+sh:path\b",
        text,
    )
    if path_match:
        start_candidates.append(path_match.start())
    if start_candidates:
        text = text[min(start_candidates):].strip()

    def looks_like_turtle_line(line: str) -> bool:
        value = line.strip()
        if not value:
            return True
        if value.startswith("@prefix") or value.startswith("@base"):
            return True
        if value.startswith(("PREFIX ", "BASE ", "#", "[", "]", "(", ")")):
            return True
        if re.match(r"^\w[\w-]*:[\w./-]+", value):
            return True
        if re.match(
            r"^(a|sh:\w+|rdf:\w+|rdfs:\w+|owl:\w+|xsd:\w+|dc:\w+|dcterms:\w+)\b",
            value,
        ):
            return True
        if re.match(r'^["\'<_]|^-?\d', value):
            return True
        if value.startswith((";", ",", ".")):
            return True
        if value.endswith((";", ",", "[", "(", ")", "]")):
            return True
        if value.endswith("."):
            return bool(
                re.search(r"\w[\w-]*:[\w./-]+", value)
                or re.search(r"<[^>]+>", value)
                or re.search(r'"[^"]*"', value)
            )
        return False

    def triple_quote_toggles(line: str) -> int:
        return line.count('"""') + line.count("'''")

    cleaned_lines: list[str] = []
    started = False
    inside_triple_quote = False
    for line in text.splitlines():
        if not started:
            if line.strip():
                started = True
            cleaned_lines.append(line)
            if triple_quote_toggles(line) % 2 == 1:
                inside_triple_quote = True
            continue
        if inside_triple_quote:
            cleaned_lines.append(line)
            if triple_quote_toggles(line) % 2 == 1:
                inside_triple_quote = False
            continue
        if triple_quote_toggles(line) % 2 == 1:
            cleaned_lines.append(line)
            inside_triple_quote = True
            continue
        if looks_like_turtle_line(line):
            cleaned_lines.append(line)
        else:
            break

    text = "\n".join(cleaned_lines).strip()
    text = re.sub(r"^```(?:turtle|ttl)?\s*", "", text, flags=re.IGNORECASE).strip()
    return re.sub(r"\s*```$", "", text).strip()


def get_property_domain(graph: Graph, property_uri: str) -> list[str]:
    """Return every explicit or union-expanded domain of an ontology property."""

    def expand_domain_node(domain_node) -> list[str]:
        if isinstance(domain_node, URIRef):
            return [str(domain_node)]
        if isinstance(domain_node, BNode):
            union_lists = list(graph.objects(domain_node, OWL.unionOf))
            if union_lists:
                return [str(item) for item in graph.items(union_lists[0])]
        return []

    domains = []
    for domain in graph.objects(URIRef(property_uri), RDFS.domain):
        domains.extend(expand_domain_node(domain))
    return domains


def get_info_by_name(graph: Graph, name: str) -> str | None:
    """Return an entity's predicate/object statements as a Markdown table."""
    subject = next(
        (candidate for candidate in graph.subjects() if str(candidate).endswith(name)),
        None,
    )
    if subject is None:
        return None

    lines = [
        "| Subject | Predicate | Object |",
        "|---------|-----------|--------|",
    ]
    lines.extend(
        f"| {subject} | {predicate} | {obj} |"
        for predicate, obj in graph.predicate_objects(subject)
    )
    return "\n".join(lines) + "\n"
