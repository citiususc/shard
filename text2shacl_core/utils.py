from __future__ import annotations

from typing import Dict, List, Optional
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, OWL, BNode
from rdflib.namespace import RDF, SH
from rdflib.plugins.serializers.turtle import TurtleSerializer
from urllib.parse import urlparse
import re

import argparse
import sys

ERA    = "http://data.europa.eu/949/"
ERA_SH = "http://data.europa.eu/949/shapes/"

# --- AUXILIARY FUNCTIONS ---

def process_shacl(shacl_text: str, output_path: str):
    """
    Cleans a SHACL Turtle file by:
    1. Removing duplicate prefix declarations (keeping first occurrence).
    2. Fixing bare rdflib blank node labels (e.g. 'n3d89e4Shape' → '_:n3d89e4Shape').
    3. Sanitizing invalid triples (URIs with spaces, "inf" literals, invalid RDF lists).
    4. Enriching sh:SPARQLConstraint shapes that are missing era:affectedClass
       or era:affectedProperty by inferring them from the sibling sh:PropertyShape.
    Writes the cleaned and enriched content to the specified output path.
    """
    import os
    from rdflib import Graph, URIRef, Literal, BNode
    from rdflib.collection import Collection
    from rdflib.namespace import XSD
    # NOTE (br2shacl-ui): the legacy `from enrich_sparql_constraints import enrich`
    # import was removed — enrich() is defined locally further down in this module.

    # ── 1. Deduplicate prefix declarations ──────────────────────────────────
    prefix_pattern = re.compile(r'^@prefix\s+[^:]+:\s+<[^>]+>\s+\.', re.MULTILINE)

    all_prefixes = prefix_pattern.findall(shacl_text)
    seen = set()
    unique_prefixes = []
    for p in all_prefixes:
        if p not in seen:
            unique_prefixes.append(p)
            seen.add(p)

    text_without_prefixes = prefix_pattern.sub('', shacl_text).strip()
    shacl_cleaned = '\n'.join(unique_prefixes) + '\n\n' + text_without_prefixes

    # ── 2. Fix bare rdflib blank node labels ────────────────────────────────
    _BNODE_RE = re.compile(r'[Nn][0-9a-fA-F]{8,}[0-9a-zA-Z]*')

    def _fix_blank_nodes(text: str) -> str:
        result = []
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c == "#":
                end = text.find("\n", i)
                if end == -1:
                    result.append(text[i:]); break
                result.append(text[i:end + 1]); i = end + 1; continue
            if c == "<":
                end = text.find(">", i)
                if end == -1:
                    result.append(text[i:]); break
                result.append(text[i:end + 1]); i = end + 1; continue
            if text[i:i+3] in ('"""', "'''"):
                delim = text[i:i+3]
                end = text.find(delim, i + 3)
                if end == -1:
                    result.append(text[i:]); break
                result.append(text[i:end + 3]); i = end + 3; continue
            if c in ('"', "'"):
                j = i + 1
                while j < n:
                    if text[j] == "\\": j += 2; continue
                    if text[j] == c: j += 1; break
                    j += 1
                result.append(text[i:j]); i = j; continue
            if text[i:i+2] == "_:":
                j = i + 2
                while j < n and (text[j].isalnum() or text[j] in "-_."): j += 1
                result.append(text[i:j]); i = j; continue
            if c.isalpha() or c == "_":
                j = i
                while j < n and (text[j].isalnum() or text[j] in "_-."): j += 1
                token = text[i:j]
                if j < n and text[j] == ":":
                    result.append(token); i = j; continue
                if ":" not in token and _BNODE_RE.fullmatch(token):
                    result.append(f"_:{token}")
                else:
                    result.append(token)
                i = j; continue
            result.append(c); i += 1
        return "".join(result)

    shacl_cleaned = _fix_blank_nodes(shacl_cleaned)

    # ── 3 & 4. Parse → sanitize → enrich → serialize ────────────────────────

    def _is_valid_uri(uri: str) -> bool:
        import urllib.parse
        if " " in uri:
            return False
        try:
            parsed = urllib.parse.urlparse(uri)
            return bool(parsed.scheme) and parsed.scheme in (
                "http", "https", "urn", "file", "ftp"
            )
        except Exception:
            return False

    _NUMERIC_XSD = {
        str(XSD.integer), str(XSD.int), str(XSD.long), str(XSD.short),
        str(XSD.decimal), str(XSD.float), str(XSD.double),
        str(XSD.nonNegativeInteger), str(XSD.positiveInteger),
    }

    def _sanitize_graph(g: Graph) -> Graph:
        to_remove = []
        for s, p, o in g:
            # Invalid subject or predicate
            for node in (s, p):
                if isinstance(node, URIRef) and not _is_valid_uri(str(node)):
                    to_remove.append((s, p, o))
                    break
            if (s, p, o) in to_remove:
                continue
            # Invalid object URI
            if isinstance(o, URIRef) and not _is_valid_uri(str(o)):
                to_remove.append((s, p, o))
            # "inf" numeric literals
            elif isinstance(o, Literal):
                if (
                    str(o).lower() in ("inf", "-inf", "infinity", "-infinity")
                    and str(o.datatype) in _NUMERIC_XSD
                ):
                    to_remove.append((s, p, o))
            # Invalid URIs inside RDF lists (sh:in etc.)
            elif isinstance(o, BNode):
                try:
                    items = list(Collection(g, o))
                    if any(
                        isinstance(item, URIRef) and not _is_valid_uri(str(item))
                        for item in items
                    ):
                        to_remove.append((s, p, o))
                except Exception:
                    pass
        for triple in to_remove:
            g.remove(triple)
        return g

    os.makedirs(
        os.path.dirname(output_path) if os.path.dirname(output_path) else ".",
        exist_ok=True,
    )

    try:
        g = Graph()
        g.parse(data=shacl_cleaned, format="turtle")
        g = _sanitize_graph(g)   # ← step 3
        enrich(g)                 # ← step 4
        g.serialize(destination=output_path, format="turtle")
    except Exception:
        # Fallback: write cleaned text directly if rdflib parsing fails
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(shacl_cleaned)

def clean_shacl_response(response: str | None) -> str:
    """
    Extract the Turtle/SHACL part from a model response as robustly as possible.

    Handles cases where the response:
    - is wrapped in ```turtle / ```ttl / plain ``` fences
    - contains introductory text before the Turtle
    - contains explanatory text after the Turtle
    - includes prefixes or not
    - contains multiline SPARQL literals (\"\"\"...\"\"\" or \'\'\'...\'\'\'
    - contains only the fallback string "SHACL shapes not found"

    It does NOT add prefixes. It only extracts the SHACL/Turtle fragment.
    """
    if response is None:
        return ""

    text = response.strip()
    if not text:
        return ""

    # Preserve the fallback exactly if it appears as the whole answer
    if text.strip().strip('"').strip("'") == "SHACL shapes not found":
        return "SHACL shapes not found"

    # ------------------------------------------------------------------ #
    # 1. If there are fenced code blocks, prefer the first one that       #
    #    looks like Turtle/SHACL.                                         #
    # ------------------------------------------------------------------ #
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
        # Remove stray fence markers if the model produced malformed fences
        text = re.sub(r"^```(?:turtle|ttl)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    # ------------------------------------------------------------------ #
    # 2. Find where the actual Turtle starts.                             #
    #    Prefer @prefix; otherwise the first SHACL shape declaration.    #
    # ------------------------------------------------------------------ #
    start_candidates = []

    m_prefix = re.search(r"(?im)^@prefix\s+\w[\w-]*:\s*<[^>]+>\s*\.\s*$", text)
    if m_prefix:
        start_candidates.append(m_prefix.start())

    m_shape = re.search(
        r"(?m)^[ \t]*\w[\w-]*:[\w.-]+\s+a\s+sh:(?:PropertyShape|NodeShape|SPARQLConstraint)\b",
        text,
    )
    if m_shape:
        start_candidates.append(m_shape.start())

    m_target = re.search(
        r"(?m)^[ \t]*\w[\w-]*:[\w.-]+\s+sh:path\b",
        text,
    )
    if m_target:
        start_candidates.append(m_target.start())

    if start_candidates:
        text = text[min(start_candidates):].strip()

    # ------------------------------------------------------------------ #
    # 3. Remove trailing explanatory prose, line by line.                 #
    #    Lines inside triple-quoted literals are always kept.             #
    # ------------------------------------------------------------------ #
    def _looks_like_turtle_line(line: str) -> bool:
        s = line.strip()

        if not s:
            return True

        if s.startswith("@prefix") or s.startswith("@base"):
            return True

        if s.startswith(("PREFIX ", "BASE ", "#", "[", "]", "(", ")")):
            return True

        # Prefixed name at start of line (subject or compact predicate)
        if re.match(r"^\w[\w-]*:[\w./-]+", s):
            return True

        # Turtle keyword or common predicate prefix
        if re.match(
            r"^(a|sh:\w+|rdf:\w+|rdfs:\w+|owl:\w+|xsd:\w+|dc:\w+|dcterms:\w+|era:\w+|era-sh:\w+)\b",
            s,
        ):
            return True

        # String literal, number, IRI, or blank node
        if re.match(r'^(["\'<_]|-?\d)', s):
            return True

        # Turtle punctuation continuations
        if s.startswith((";", ",", ".")):
            return True

        if s.endswith((";", ",", "[", "(", ")")):
            return True

        if s.endswith("]"):
            return True

        # A line ending in "." is only Turtle if it contains a known token
        if s.endswith("."):
            if re.search(r"\w[\w-]*:[\w./-]+", s):   # prefixed name
                return True
            if re.search(r"<[^>]+>", s):              # full IRI
                return True
            if re.search(r'"[^"]*"', s):              # quoted literal
                return True
            return False

        return False

    def _count_triple_quote_toggles(line: str) -> int:
        """
        Count how many times a triple-quote delimiter (\"\"\" or \'\'\')
        appears on this line. An odd count means the line opens or closes
        a multiline literal.
        """
        return line.count('"""') + line.count("'''")

    lines = text.splitlines()
    cleaned_lines: list[str] = []
    started = False
    inside_triple_quote = False

    for line in lines:
        stripped = line.strip()

        if not started:
            if stripped:
                started = True
            cleaned_lines.append(line)
            # Check whether this first content line opens a triple-quoted literal
            if _count_triple_quote_toggles(line) % 2 == 1:
                inside_triple_quote = True
            continue

        # Inside a multiline literal — accept unconditionally until it closes
        if inside_triple_quote:
            cleaned_lines.append(line)
            if _count_triple_quote_toggles(line) % 2 == 1:
                inside_triple_quote = False
            continue

        # Detect opening of a triple-quoted literal on this line
        if _count_triple_quote_toggles(line) % 2 == 1:
            cleaned_lines.append(line)
            inside_triple_quote = True
            continue

        if _looks_like_turtle_line(line):
            cleaned_lines.append(line)
        else:
            # Stop at the first clearly non-Turtle explanatory line
            break

    text = "\n".join(cleaned_lines).strip()

    # ------------------------------------------------------------------ #
    # 4. Final cleanup of leftover fences and whitespace                  #
    # ------------------------------------------------------------------ #
    text = re.sub(r"^```(?:turtle|ttl)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*```$", "", text).strip()

    return text


def _era(name: str) -> URIRef:
    return URIRef(ERA + name)


def _property_shape_uri(sparql_uri: str) -> Optional[str]:
    """
    Derive the corresponding PropertyShape URI from a SPARQLConstraint URI.

    era-sh:accelerationLevelCrossingApplicability
        → era-sh:accelerationLevelCrossing

    era-sh:InstructionsSwitchRadioSystemsApplicability
        → era-sh:InstructionsSwitchRadioSystems

    Returns None if the URI does not end with 'Applicability'.
    """
    local = sparql_uri.split("/")[-1].split("#")[-1]
    if not local.endswith("Applicability"):
        return None
    base_local = local[: -len("Applicability")]
    # Reconstruct the PropertyShape URI using the same namespace prefix
    prefix = sparql_uri[: sparql_uri.rfind(local)]
    return prefix + base_local


def enrich(g: Graph) -> dict:
    """
    Add era:affectedClass and era:affectedProperty to every SPARQLConstraint
    that is missing them, inferring from the sibling PropertyShape.

    Returns a stats dict.
    """
    affected_class_pred    = _era("affectedClass")
    affected_property_pred = _era("affectedProperty")

    stats = {
        "sparql_constraints_total":   0,
        "already_complete":           0,
        "enriched":                   0,
        "no_sibling_shape_found":     0,
        "details":                    [],
    }

    for sparql_shape in list(g.subjects(RDF.type, SH.SPARQLConstraint)):
        sparql_uri = str(sparql_shape)
        stats["sparql_constraints_total"] += 1

        has_class = g.value(sparql_shape, affected_class_pred)
        has_prop  = g.value(sparql_shape, affected_property_pred)

        if has_class and has_prop:
            stats["already_complete"] += 1
            continue

        # Derive sibling PropertyShape URI
        ps_uri = _property_shape_uri(sparql_uri)
        if ps_uri is None:
            stats["no_sibling_shape_found"] += 1
            stats["details"].append({
                "sparql_shape": sparql_uri,
                "result": "skipped — URI does not end with 'Applicability'",
            })
            continue

        ps_node = URIRef(ps_uri)

        # Verify the sibling actually exists as a PropertyShape
        if (ps_node, RDF.type, SH.PropertyShape) not in g:
            stats["no_sibling_shape_found"] += 1
            stats["details"].append({
                "sparql_shape":    sparql_uri,
                "expected_sibling": ps_uri,
                "result": "sibling PropertyShape not found in graph",
            })
            continue

        # Collect values from the sibling PropertyShape
        classes    = list(g.objects(ps_node, affected_class_pred))
        properties = list(g.objects(ps_node, affected_property_pred))

        added_classes    = []
        added_properties = []

        if not has_class:
            for cls in classes:
                g.add((sparql_shape, affected_class_pred, cls))
                added_classes.append(str(cls))

        if not has_prop:
            for prop in properties:
                g.add((sparql_shape, affected_property_pred, prop))
                added_properties.append(str(prop))

        stats["enriched"] += 1
        stats["details"].append({
            "sparql_shape":        sparql_uri,
            "sibling_shape":       ps_uri,
            "added_affectedClass":    added_classes,
            "added_affectedProperty": added_properties,
            "result": "enriched",
        })

    return stats


def generate_node_shapes_str(node_shapes: dict) -> str:
    """
    Generates SHACL NodeShape definitions as Turtle text
    based on a dictionary of class qualified names and their properties.
    """
    shapes_str = ""
    for class_qname, properties in node_shapes.items():
        shape_name = f"{class_qname}Shape"
        properties_str = ",\n               ".join(properties)
        
        shape_block = f"""{shape_name} a sh:NodeShape ;
               sh:property {properties_str} ;
               sh:targetClass {class_qname} .\n\n"""
        
        shapes_str += shape_block

    return shapes_str.strip()


def update_node_shapes(node_shapes: Dict[str, List[str]], affected_classes: List[str], shape_name: str):
    """
    Updates a dictionary of node shapes by adding a new shape name
    to all affected classes, creating the class key if it does not exist.
    """
    for affected_class in affected_classes:
        if affected_class not in node_shapes:
            node_shapes[affected_class] = []
        if shape_name not in node_shapes[affected_class]:
            node_shapes[affected_class].append(shape_name)
    return node_shapes


def extract_name_and_class_from_shape(shacl_str: str, shacl_prefixes: str):
    """
    Extracts the qualified name of a PropertyShape and its associated affectedClass
    values from a SHACL Turtle fragment.

    :param shacl_str: SHACL fragment (single PropertyShape) as a string
    :param shacl_prefixes: Required Turtle-style prefixes as a string
    :return: Tuple (property_shape_qname: str, affected_classes_qnames: List[str])
    """
    g = Graph()
    g.parse(data=f"{shacl_prefixes}\n{shacl_str}", format="turtle")

    ERA = Namespace("http://data.europa.eu/949/")

    for s in g.subjects(RDF.type, SH.PropertyShape):
        try:
            shape_qname = g.qname(s)
        except:
            shape_qname = str(s)

        # Collect all affectedClass values
        affected_classes = []
        for c in g.objects(s, ERA.affectedClass):
            try:
                class_qname = g.qname(c)
            except:
                class_qname = str(c)
            affected_classes.append(class_qname)

        return shape_qname, affected_classes

    return None, []


def get_entity_label_and_description(g: Graph, entity_uri: str) -> str:
    """
    Retrieves a string describing an ontology entity with its local name and
    the first available rdfs:comment.

    Example: "ContactLineSystem. A system used for supplying electrical energy..."

    :param g: RDF graph containing the ontology
    :param entity_uri: Full URI of the entity
    :return: Formatted string "LocalName. Description"
    """
    entity_ref = URIRef(entity_uri)

    # Extract the local name from the URI
    parsed = urlparse(entity_uri)
    local_name = entity_uri.rsplit('/')[-1] if '/' in parsed.path else entity_uri.rsplit('#', 1)[-1]

    # Get the first available rdfs:comment
    comments = list(g.objects(entity_ref, RDFS.comment))
    description = str(comments[0]) if comments else " "

    return f"{local_name}. {description}" if comments else f"{local_name}"


def get_property_domain(g: Graph, property_uri: str):
    """
    Returns the domain(s) of a given property in the ontology.
    If the domain is defined as an owl:unionOf, returns all members
    of the union, otherwise returns the domain directly.

    :param property_uri: Full URI of the property
    :return: List of domain URIs as strings
    """
    def expand_domain_node(g, domain_node):
        if isinstance(domain_node, URIRef):
            return [str(domain_node)]
        elif isinstance(domain_node, BNode):
            union_list = list(g.objects(domain_node, OWL.unionOf))
            if union_list:
                rdf_list_root = union_list[0]
                return [str(item) for item in g.items(rdf_list_root)]
            else:
                return []
        else:
            return []

    property_ref = URIRef(property_uri)
    raw_domains = list(g.objects(property_ref, RDFS.domain))

    expanded_domains = []
    for d in raw_domains:
        expanded_domains.extend(expand_domain_node(g, d))

    return expanded_domains


def _list2markdown(data):
    """
    Converts a list of triples (subject, predicate, object) represented
    as dictionaries into a Markdown table for better readability.
    """
    table = "| Subject | Predicate | Object |\n"
    table += "|---------|-----------|--------|\n"
    
    for entry in data:
        subject = entry['subject']
        predicate = entry['predicate']
        obj = entry['object']
        table += f"| {subject} | {predicate} | {obj} |\n"
    
    return table


def get_info_by_name(g: Graph, name: str):
    """
    Searches for an entity by its local name in the RDF graph and
    returns all associated predicates and objects in a Markdown table.

    :param g: RDF graph
    :param name: Local name of the entity
    :return: Markdown table as a string, or None if not found
    """
    subj = None
    for subject in g.subjects():
        if subject.endswith(name):
            subj = subject
            break
    
    if subj is None:
        return None
    
    result = []
    for pred, obj in g.predicate_objects(subj):
        result.append({
            "subject": str(subj),
            "predicate": str(pred),
            "object": str(obj)
        })

    markdown_info = _list2markdown(result)
    return markdown_info


def get_owl_properties_with_domain(g: Graph, namespace="http://data.europa.eu/949/"):
    """
    Returns the URIs of all OWL ObjectProperty or DatatypeProperty elements
    which have a domain defined, filtering out blank nodes that do not
    define an owl:unionOf and ignoring domains outside the given namespace.
    """
    query = """
        SELECT ?prop ?domain WHERE {
            ?prop a ?type .
            FILTER(?type IN (owl:ObjectProperty, owl:DatatypeProperty)) .
            FILTER(!isBlank(?prop)) .
            ?prop rdfs:domain ?domain .
        }
    """

    properties_with_domain = []

    for row in g.query(query, initNs={"owl": OWL, "rdfs": RDFS}):
        prop = row[0]
        domain = row[1]

        # Skip blank domains without owl:unionOf
        if isinstance(domain, BNode):
            if not any(g.objects(domain, OWL.unionOf)):
                continue
        else:
            # Ignore domains outside the target namespace
            if not str(domain).startswith(namespace):
                continue

        properties_with_domain.append(str(prop))

    return properties_with_domain