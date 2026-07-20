"""Validate SHACL syntax, SHACL for SHACL profiles and ontology grounding."""

from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SH, XSD
from rdflib.util import guess_format

from shard.domain.ontology import parse_ontology_graph

GENERIC_SHACL_PROFILE_NAME = "shacl-shacl.ttl"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
GENERIC_SHACL_PROFILE_PATH = PACKAGE_ROOT / "resources" / "validation" / GENERIC_SHACL_PROFILE_NAME

def _rdf_format(filename):
    guessed = guess_format(filename or "") if filename else None
    return guessed or "turtle"

_STANDARD_NAMESPACES = (
    str(SH),
    str(RDF),
    str(RDFS),
    str(OWL),
    str(XSD),
)

def validation_profiles_from_payload(payload):
    profiles = payload.get("validation_profiles") or payload.get("shape_validation_profiles") or []
    if not isinstance(profiles, list):
        return []
    out = []
    for idx, item in enumerate(profiles):
        if isinstance(item, str):
            content = item
            name = f"profile-{idx + 1}.ttl"
        elif isinstance(item, dict):
            content = item.get("content") or ""
            name = item.get("name") or item.get("filename") or f"profile-{idx + 1}.ttl"
        else:
            continue
        if content.strip():
            out.append({"name": name, "content": content})
    return out

def _generic_validation_profile():
    with open(GENERIC_SHACL_PROFILE_PATH, "r", encoding="utf-8") as handle:
        return {
            "name": GENERIC_SHACL_PROFILE_NAME,
            "content": handle.read(),
            "scope": "generic",
            "path": GENERIC_SHACL_PROFILE_PATH,
        }

def _validation_metadata(generic_profile, domain_profiles):
    domain_names = [profile.get("name") or f"profile-{idx + 1}.ttl" for idx, profile in enumerate(domain_profiles)]
    active_profiles = ([generic_profile] if generic_profile else []) + list(domain_profiles)
    return {
        "profile_count": len(active_profiles),
        "profile_names": [profile.get("name") or f"profile-{idx + 1}.ttl" for idx, profile in enumerate(active_profiles)],
        "generic_profile_active": bool(generic_profile),
        "generic_profile_name": generic_profile["name"] if generic_profile else None,
        "domain_profile_count": len(domain_profiles),
        "domain_profile_names": domain_names,
        "validation_level": "syntax+generic+profile" if domain_profiles else "syntax+generic",
    }

def _validation_scope_text(metadata):
    if metadata.get("domain_profile_count"):
        names = ", ".join(metadata.get("domain_profile_names") or [])
        return f"generic SHACL for SHACL + domain profile ({names})"
    return "generic SHACL for SHACL"

def validate_shape_content(shape, prefixes="", profiles=None):
    """Validate generated SHACL as Turtle and active SHACL for SHACL profiles."""
    domain_profiles = profiles or []
    try:
        generic_profile = _generic_validation_profile()
    except Exception as exc:
        metadata = _validation_metadata(None, domain_profiles)
        return {
            "valid": False,
            "syntax_valid": None,
            "profile_valid": False,
            **metadata,
            "error": f"Generic SHACL for SHACL profile '{GENERIC_SHACL_PROFILE_NAME}' could not be loaded: {exc}",
            "error_type": "profile",
            "message": "The generic SHACL for SHACL profile could not be loaded.",
        }
    metadata = _validation_metadata(generic_profile, domain_profiles)
    active_profiles = [generic_profile] + list(domain_profiles)
    full_shape = f"{prefixes or ''}\n{shape or ''}"

    try:
        data_graph = Graph(bind_namespaces="none")
        data_graph.parse(data=full_shape, format="turtle")
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": False,
            "profile_valid": None,
            **metadata,
            "error": str(exc),
            "error_type": "parse",
            "message": "Generated shape is not valid Turtle.",
        }

    try:
        from pyshacl import validate as pyshacl_validate
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": True,
            "profile_valid": False,
            **metadata,
            "error": f"pyshacl is required for shape validation profiles: {exc}",
            "error_type": "profile",
            "message": "Shape validation profile could not run because pyshacl is not installed.",
        }

    shapes_graph = Graph(bind_namespaces="none")
    try:
        for profile in active_profiles:
            shapes_graph.parse(
                data=profile["content"],
                format=_rdf_format(profile["name"]),
                publicID=profile["name"],
            )
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": True,
            "profile_valid": False,
            **metadata,
            "error": str(exc),
            "error_type": "profile",
            "message": "One of the shape validation profile files could not be parsed.",
        }

    try:
        conforms, _report_graph, report_text = pyshacl_validate(
            data_graph=data_graph,
            shacl_graph=shapes_graph,
            inference="rdfs",
            abort_on_first=False,
            allow_infos=True,
            allow_warnings=True,
            meta_shacl=False,
            advanced=True,
        )
    except Exception as exc:
        return {
            "valid": False,
            "syntax_valid": True,
            "profile_valid": False,
            **metadata,
            "error": str(exc),
            "error_type": "profile",
            "message": "Shape validation profile execution failed.",
        }

    report_text = str(report_text or "").strip()
    if conforms:
        return {
            "valid": True,
            "syntax_valid": True,
            "profile_valid": True,
            **metadata,
            "error": None,
            "error_type": "none",
            "report_text": report_text,
            "message": f"Valid Turtle / SHACL. {_validation_scope_text(metadata)} OK.",
        }

    return {
        "valid": False,
        "syntax_valid": True,
        "profile_valid": False,
        **metadata,
        "error": report_text[:5000] if report_text else "Shape validation profile reported non-conformance.",
        "report_text": report_text[:5000],
        "error_type": "profile",
        "message": f"Generated shape does not conform to active SHACL for SHACL validation ({_validation_scope_text(metadata)}).",
    }

def ontology_grounding_catalog(
    ontology_content,
    ontology_filename,
    target,
    target_roles=None,
):
    """Return ontology term IRIs and rule-scoped property hints."""
    from shard.application.ontology_catalog import parse_ontology

    parsed = parse_ontology(ontology_filename or "ontology.ttl", ontology_content or "")
    terms = parsed.get("entities") or []
    valid_classes = set()
    valid_properties = set()
    term_by_ref = {}
    graph = parse_ontology_graph(ontology_content, ontology_filename or "ontology.ttl")

    for term in terms:
        full_iri = term.get("full_iri")
        if not full_iri:
            continue
        ref = URIRef(str(full_iri))
        term_by_ref[str(ref)] = term
        if term.get("type") == "class":
            valid_classes.add(ref)
        elif term.get("type") == "property":
            valid_properties.add(ref)

    role_terms = [
        term
        for values in (target_roles or {}).values()
        for term in (values or [])
        if isinstance(term, dict)
    ]
    context_terms = [target or {}, *role_terms]
    target_refs = {
        str(value)
        for context_term in context_terms
        for value in (
            context_term.get("iri"),
            context_term.get("full_iri"),
            context_term.get("label"),
            context_term.get("id"),
            context_term.get("domain"),
        )
        if value
    }
    target_fulls = {
        str(context_term.get("full_iri"))
        for context_term in context_terms
        if context_term.get("full_iri")
    }
    target_domains = {
        str(context_term.get("domain"))
        for context_term in context_terms
        if context_term.get("domain")
    }
    scoped_properties = []
    for ref in sorted(valid_properties, key=str):
        term = term_by_ref.get(str(ref), {})
        same_target = str(term.get("full_iri") or "") in target_fulls
        same_domain = bool(str(term.get("domain") or "") in target_domains)
        domain_is_target = bool(str(term.get("domain") or "") in target_refs)
        if same_target or same_domain or domain_is_target:
            scoped_properties.append(ref)

    return {
        "graph": graph,
        "valid_classes": valid_classes,
        "valid_properties": valid_properties,
        "valid_terms": valid_classes | valid_properties,
        "scoped_properties": scoped_properties,
    }

def _display_iri(graph, value):
    try:
        return graph.namespace_manager.normalizeUri(value)
    except Exception:
        return f"<{value}>"

def _display_iri_list(graph, values, limit=40):
    labels = [_display_iri(graph, value) for value in sorted(values, key=str)]
    if len(labels) > limit:
        labels = labels[:limit] + [f"... ({len(values) - limit} more)"]
    return ", ".join(labels) if labels else "(none)"

def allowed_ontology_terms_text(catalog):
    graph = catalog["graph"]
    return "\n".join([
        "Allowed ontology classes:",
        _display_iri_list(graph, catalog["valid_classes"]),
        "Allowed ontology properties for this target/domain:",
        _display_iri_list(graph, catalog["scoped_properties"] or catalog["valid_properties"]),
    ])

def _has_value_range_class_errors(shape_graph, catalog):
    """Find range classes incorrectly used as concrete ``sh:hasValue`` values."""
    ontology_graph = catalog["graph"]
    errors = []
    for property_shape, value in shape_graph.subject_objects(SH.hasValue):
        if not isinstance(value, URIRef) or value not in catalog["valid_classes"]:
            continue
        for path in shape_graph.objects(property_shape, SH.path):
            if not isinstance(path, URIRef):
                continue
            ranges = set(ontology_graph.objects(path, RDFS.range))
            if value in ranges:
                errors.append({
                    "predicate": _display_iri(shape_graph, SH.hasValue),
                    "iri": _display_iri(shape_graph, value),
                    "path": _display_iri(shape_graph, path),
                    "expected": "individual or literal value",
                })
    return errors

def validate_shape_grounding(
    shape,
    prefixes="",
    ontology_content="",
    ontology_filename="",
    target=None,
    catalog=None,
    target_roles=None,
):
    """Validate that generated shape references only ontology terms for key SHACL IRIs."""
    if not ontology_content:
        return {"valid": True, "error": None, "error_type": "none", "invalid_iris": []}

    target = target or {}
    catalog = catalog or ontology_grounding_catalog(
        ontology_content,
        ontology_filename,
        target,
        target_roles,
    )
    graph = Graph(bind_namespaces="none")
    graph.parse(data=f"{prefixes or ''}\n{shape or ''}", format="turtle")

    invalid_has_values = _has_value_range_class_errors(graph, catalog)
    if invalid_has_values:
        first = invalid_has_values[0]
        error = "\n".join([
            f"Invalid sh:hasValue: {first['iri']} is the ontology range class of {first['path']}, not a concrete required value.",
            f"Use sh:class {first['iri']} to constrain values of {first['path']} to that class.",
            "Use sh:hasValue only when the business rule requires one specific individual or literal value.",
        ])
        return {
            "valid": False,
            "syntax_valid": True,
            "profile_valid": None,
            "profile_count": 0,
            "profile_names": [],
            "error": error,
            "error_type": "grounding",
            "invalid_iris": invalid_has_values,
            "message": "Generated shape uses an ontology range class as a concrete sh:hasValue value.",
        }

    checks = [
        (SH.path, catalog["valid_properties"], "property"),
        (SH.targetClass, catalog["valid_classes"], "class"),
        (SH["class"], catalog["valid_classes"], "class"),
        (SH.node, catalog["valid_terms"], "ontology term"),
    ]
    invalid = []
    for predicate, allowed, expected_kind in checks:
        for value in graph.objects(None, predicate):
            if not isinstance(value, URIRef):
                continue
            value_text = str(value)
            if value_text.startswith(_STANDARD_NAMESPACES):
                continue
            if value not in allowed:
                invalid.append({
                    "predicate": _display_iri(graph, predicate),
                    "iri": _display_iri(graph, value),
                    "expected": expected_kind,
                })

    if not invalid:
        return {"valid": True, "error": None, "error_type": "none", "invalid_iris": []}

    first = invalid[0]
    error = "\n".join([
        f"Invalid IRI: {first['iri']} does not exist in the ontology as a valid {first['expected']} for {first['predicate']}.",
        allowed_ontology_terms_text(catalog),
        "Use only IRIs from the provided ontology. Do not invent properties or classes.",
    ])
    return {
        "valid": False,
        "syntax_valid": True,
        "profile_valid": None,
        "profile_count": 0,
        "profile_names": [],
        "error": error,
        "error_type": "grounding",
        "invalid_iris": invalid,
        "message": "Generated shape references ontology IRIs that are not present in the uploaded ontology.",
    }
