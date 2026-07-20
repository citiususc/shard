"""Versioned public API contract for SHARD.

The catalog describes logical capabilities independently from their process or
port layout. Runtime dispatchers consume the same metadata, so the API exposed
by the application and the architecture documented for the demo cannot drift.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

from shard import __description__, __title__, __version__


API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"
SERVICE_LAYOUT_ENV = "SHARD_SERVICE_LAYOUT"
LEGACY_SERVICE_LAYOUT_ENV = "BR2SHACL_SERVICE_LAYOUT"
UNIFIED_LAYOUT = "unified"
SPLIT_LAYOUT = "split"
SUPPORTED_SERVICE_LAYOUTS = (UNIFIED_LAYOUT, SPLIT_LAYOUT)

PRIMARY_ROLE = "primary"
AUXILIARY_ROLE = "auxiliary"
SYSTEM_ROLE = "system"


@dataclass(frozen=True)
class LogicalService:
    """A user-visible capability boundary, independent from deployment."""

    service_id: str
    title: str
    responsibility: str


@dataclass(frozen=True)
class EndpointSpec:
    """Stable metadata for one public API operation."""

    operation: str
    method: str
    path: str
    legacy_path: Optional[str]
    service_id: Optional[str]
    role: str
    transport: str
    summary: str


LOGICAL_SERVICES: Tuple[LogicalService, ...] = (
    LogicalService(
        "ontology",
        "Ontology Catalog and Retrieval Service",
        "Parse ontologies, expose their term catalog, retrieve relevant terms and manage semantic indexes.",
    ),
    LogicalService(
        "rule-grounding",
        "Business Rule Grounding Service",
        "Ground business rules in auditable, role-grouped ontology terms without generating shapes.",
    ),
    LogicalService(
        "shape-generation",
        "Shape Generation Service",
        "Generate a grounded SHACL constraint document from one rule context.",
    ),
    LogicalService(
        "shape-assurance",
        "Shape Assurance and Baseline Integration Service",
        "Validate SHACL documents, generate ontology baselines and combine reviewed output.",
    ),
    LogicalService(
        "authoring-workflow",
        "Authoring Workflow Service",
        "Orchestrate complete Rule to Shape and Batch to Rules authoring workflows.",
    ),
)


ENDPOINTS: Tuple[EndpointSpec, ...] = (
    EndpointSpec(
        "system.root", "GET", API_PREFIX, None,
        None, SYSTEM_ROLE, "json",
        "Discover the SHARD API, documentation and principal workflows.",
    ),
    EndpointSpec(
        "system.openapi", "GET", f"{API_PREFIX}/openapi.json", None,
        None, SYSTEM_ROLE, "json",
        "Return the OpenAPI 3.1 contract for programmatic clients.",
    ),
    EndpointSpec(
        "system.docs", "GET", f"{API_PREFIX}/docs", None,
        None, SYSTEM_ROLE, "html",
        "Render interactive Swagger UI documentation for the API contract.",
    ),
    EndpointSpec(
        "workflows.rule.generate", "POST", f"{API_PREFIX}/workflows/rule-to-shape", None,
        "authoring-workflow", PRIMARY_ROLE, "json",
        "Resolve one business rule and generate a validated, ontology-grounded SHACL document.",
    ),
    EndpointSpec(
        "workflows.guide.generate", "POST", f"{API_PREFIX}/workflows/guide-to-shapes", None,
        "authoring-workflow", PRIMARY_ROLE, "json",
        "Generate and consolidate validated SHACL documents from a structured business-rule batch.",
    ),
    EndpointSpec(
        "ontology.parse", "POST", f"{API_PREFIX}/ontology/parse", "/parse-ontology",
        "ontology", PRIMARY_ROLE, "json",
        "Parse an ontology and return namespaces, prefixes and ontology terms.",
    ),
    EndpointSpec(
        "ontology.search", "POST", f"{API_PREFIX}/ontology/search", "/find-relevant-terms",
        "ontology", PRIMARY_ROLE, "json",
        "Rank ontology terms for a business rule.",
    ),
    EndpointSpec(
        "ontology.index.prepare", "POST", f"{API_PREFIX}/ontology/index",
        "/prepare-ontology-embeddings", "ontology", AUXILIARY_ROLE, "json",
        "Prepare and cache ontology-term embeddings.",
    ),
    EndpointSpec(
        "ontology.index.status", "POST", f"{API_PREFIX}/ontology/index/status",
        "/ontology-embedding-status", "ontology", AUXILIARY_ROLE, "json",
        "Inspect ontology embedding preparation.",
    ),
    EndpointSpec(
        "ontology.index.cancel", "POST", f"{API_PREFIX}/ontology/index/cancel",
        "/cancel-ontology-embeddings", "ontology", AUXILIARY_ROLE, "json",
        "Cancel matching ontology embedding jobs.",
    ),
    EndpointSpec(
        "rules.resolve-targets", "POST", f"{API_PREFIX}/rules/resolve-targets",
        "/resolve-rule-targets", "rule-grounding", PRIMARY_ROLE, "json",
        "Resolve business rules to focus nodes, constrained paths and related terms.",
    ),
    EndpointSpec(
        "shapes.build", "POST", f"{API_PREFIX}/shapes/build", "/build-shacl-shape",
        "shape-generation", PRIMARY_ROLE, "json",
        "Generate and validate one grounded SHACL rule constraint document.",
    ),
    EndpointSpec(
        "shapes.validate", "POST", f"{API_PREFIX}/shapes/validate", "/validate-shape",
        "shape-assurance", PRIMARY_ROLE, "json",
        "Validate Turtle syntax and active SHACL for SHACL profiles.",
    ),
    EndpointSpec(
        "baselines.astrea.generate", "POST", f"{API_PREFIX}/baselines/astrea",
        "/generate-astrea-baseline", "shape-assurance", PRIMARY_ROLE, "json",
        "Generate ontology-derived baseline shapes through the Astrea service.",
    ),
    EndpointSpec(
        "shapes.merge", "POST", f"{API_PREFIX}/shapes/merge", "/merge-shapes",
        "shape-assurance", PRIMARY_ROLE, "json",
        "Merge generated shapes with an ontology-derived baseline.",
    ),
    EndpointSpec(
        "guides.generate", "POST", f"{API_PREFIX}/guides/generate", "/generate-from-guide",
        "authoring-workflow", PRIMARY_ROLE, "sse",
        "Generate shapes from a business-rule batch and stream progress by rule.",
    ),
    EndpointSpec(
        "models.check", "POST", f"{API_PREFIX}/models/check", "/validate-model",
        None, AUXILIARY_ROLE, "json",
        "Check whether a configured inference model is reachable.",
    ),
    EndpointSpec(
        "models.local.status", "POST", f"{API_PREFIX}/models/local/status",
        "/local-model-status", None, AUXILIARY_ROLE, "json",
        "Check whether a local model snapshot has already been downloaded.",
    ),
    EndpointSpec(
        "models.local.download", "POST", f"{API_PREFIX}/models/local/download",
        "/download-local-model", None, AUXILIARY_ROLE, "sse",
        "Explicitly download a local model snapshot and stream progress.",
    ),
    EndpointSpec(
        "system.capabilities", "GET", f"{API_PREFIX}/capabilities", "/api/capabilities",
        None, SYSTEM_ROLE, "json",
        "Describe deployment policy and the available API contract.",
    ),
    EndpointSpec(
        "system.health", "GET", f"{API_PREFIX}/health", None,
        None, SYSTEM_ROLE, "json",
        "Report application API health.",
    ),
)


_BY_OPERATION: Dict[str, EndpointSpec] = {item.operation: item for item in ENDPOINTS}
_BY_ROUTE = {
    (item.method, route): item
    for item in ENDPOINTS
    for route in (item.path, item.legacy_path)
    if route
}


def endpoint_for_operation(operation: str) -> EndpointSpec:
    """Return one endpoint specification by stable operation id."""
    return _BY_OPERATION[operation]


def endpoint_for_route(method: str, path: str) -> Optional[EndpointSpec]:
    """Resolve a canonical or legacy route to its endpoint specification."""
    return _BY_ROUTE.get((str(method or "").upper(), str(path or "")))


def public_endpoint_map() -> Dict[str, str]:
    """Return operation ids mapped to canonical versioned paths."""
    return {item.operation: item.path for item in ENDPOINTS}


def legacy_endpoint_map() -> Dict[str, str]:
    """Return operation ids mapped to compatibility paths where available."""
    return {item.operation: item.legacy_path for item in ENDPOINTS if item.legacy_path}


def api_catalog() -> Dict[str, object]:
    """Return a JSON-serializable description of services and operations."""
    return {
        "product": {
            "name": __title__,
            "title": __description__,
            "version": __version__,
        },
        "version": API_VERSION,
        "prefix": API_PREFIX,
        "services": [asdict(service) for service in LOGICAL_SERVICES],
        "endpoints": [asdict(endpoint) for endpoint in ENDPOINTS],
    }


_FRONTEND_OPERATIONS = {
    "capabilities": "system.capabilities",
    "parse": "ontology.parse",
    "terms": "ontology.search",
    "prepareTerms": "ontology.index.prepare",
    "termStatus": "ontology.index.status",
    "cancelTerms": "ontology.index.cancel",
    "resolveRule": "rules.resolve-targets",
    "build": "shapes.build",
    "validate": "shapes.validate",
    "astrea": "baselines.astrea.generate",
    "merge": "shapes.merge",
    "validateModel": "models.check",
    "localModelStatus": "models.local.status",
    "downloadLocalModel": "models.local.download",
    "guide": "guides.generate",
}

_SPLIT_SERVICE_ORIGINS = {
    "ontology.parse": "http://127.0.0.1:9100",
    "ontology.search": "http://127.0.0.1:9101",
    "ontology.index.prepare": "http://127.0.0.1:9101",
    "ontology.index.status": "http://127.0.0.1:9101",
    "ontology.index.cancel": "http://127.0.0.1:9101",
    "rules.resolve-targets": "http://127.0.0.1:9104",
    "shapes.build": "http://127.0.0.1:9102",
    "shapes.validate": "http://127.0.0.1:9102",
    "baselines.astrea.generate": "http://127.0.0.1:9102",
    "shapes.merge": "http://127.0.0.1:9102",
    "models.check": "http://127.0.0.1:9102",
    "models.local.status": "http://127.0.0.1:9102",
    "models.local.download": "http://127.0.0.1:9102",
    "guides.generate": "http://127.0.0.1:9103",
}


def normalize_service_layout(value: object) -> str:
    """Normalize and validate a runtime service layout."""
    layout = str(value or UNIFIED_LAYOUT).strip().lower()
    if layout not in SUPPORTED_SERVICE_LAYOUTS:
        choices = ", ".join(SUPPORTED_SERVICE_LAYOUTS)
        raise ValueError(f"service layout must be one of: {choices}")
    return layout


def get_service_layout() -> str:
    """Read the service layout, accepting the former environment alias."""
    import os

    value = os.environ.get(SERVICE_LAYOUT_ENV)
    if value is None:
        value = os.environ.get(LEGACY_SERVICE_LAYOUT_ENV, UNIFIED_LAYOUT)
    return normalize_service_layout(value)


def frontend_endpoint_map(layout: str = UNIFIED_LAYOUT) -> Dict[str, str]:
    """Return the endpoint URLs consumed by the static frontend."""
    selected_layout = normalize_service_layout(layout)
    endpoints = {}
    for frontend_key, operation in _FRONTEND_OPERATIONS.items():
        endpoint = endpoint_for_operation(operation)
        if selected_layout == UNIFIED_LAYOUT or operation == "system.capabilities":
            endpoints[frontend_key] = endpoint.path
            continue
        origin = _SPLIT_SERVICE_ORIGINS[operation]
        endpoints[frontend_key] = f"{origin}{endpoint.legacy_path}"
    return endpoints
