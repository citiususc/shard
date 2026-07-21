"""Versioned public API contract for SHARD.

The catalog describes logical capabilities independently from their process or
port layout. Runtime dispatchers consume the same metadata, so the API exposed
by the application and the architecture documented for the demo cannot drift.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
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
    description: str = ""
    success_status: int = 200


LOGICAL_SERVICES: Tuple[LogicalService, ...] = (
    LogicalService(
        "ontology",
        "Ontology Catalog and Retrieval Service",
        "Parse ontologies, expose their term catalog, retrieve relevant terms and manage semantic indexes.",
    ),
    LogicalService(
        "rule-grounding",
        "Data Constraint Grounding Service",
        "Ground data constraints in auditable, role-grouped ontology terms without generating shapes.",
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
        "Orchestrate complete Rule-to-Shape and Guide-to-Shapes authoring workflows through single-rule and batch API resources.",
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
        "system.redoc", "GET", f"{API_PREFIX}/redoc", None,
        None, SYSTEM_ROLE, "html",
        "Render reference-oriented ReDoc documentation for the API contract.",
    ),
    EndpointSpec(
        "workflows.rule.generate", "POST", f"{API_PREFIX}/workflows/rule-to-shape", None,
        "authoring-workflow", PRIMARY_ROLE, "json",
        "Resolve one data constraint and generate a validated, ontology-grounded SHACL document.",
    ),
    EndpointSpec(
        "workflows.batch.generate", "POST", f"{API_PREFIX}/workflows/batch-to-shapes", None,
        "authoring-workflow", PRIMARY_ROLE, "json",
        "Generate and consolidate validated SHACL documents from a structured data-constraint batch.",
        "Batch-to-Shapes API operation implementing the Guide-to-Shapes workflow. Returns one consolidated JSON result after every constraint has been resolved, generated and validated.",
    ),
    EndpointSpec(
        "ontology.parse", "POST", f"{API_PREFIX}/ontology/parse", "/parse-ontology",
        "ontology", PRIMARY_ROLE, "json",
        "Parse an ontology and return namespaces, prefixes and ontology terms.",
    ),
    EndpointSpec(
        "ontology.search", "POST", f"{API_PREFIX}/ontology/search", "/find-relevant-terms",
        "ontology", PRIMARY_ROLE, "json",
        "Rank ontology terms for a data constraint.",
        "Ranks catalog terms for retrieval. It does not assign focus-node or constraint-path roles; use rules/resolve-targets for grounded rule interpretation.",
    ),
    EndpointSpec(
        "ontology.index.create", "POST", f"{API_PREFIX}/ontology/indexes",
        None, "ontology", AUXILIARY_ROLE, "json",
        "Create an ontology embedding-index job.",
        "Starts asynchronous preparation and returns a stable job resource.",
        success_status=202,
    ),
    EndpointSpec(
        "ontology.index.get", "GET", f"{API_PREFIX}/ontology/indexes/{{job_id}}",
        None, "ontology", AUXILIARY_ROLE, "json",
        "Inspect an ontology embedding-index job.",
        "Returns current state and progress for a job created by POST /ontology/indexes.",
    ),
    EndpointSpec(
        "ontology.index.delete", "DELETE", f"{API_PREFIX}/ontology/indexes/{{job_id}}",
        None, "ontology", AUXILIARY_ROLE, "json",
        "Cancel an ontology embedding-index job.",
        "Requests cooperative cancellation. Completed and failed jobs produce a 409 conflict.",
    ),
    EndpointSpec(
        "rules.resolve-targets", "POST", f"{API_PREFIX}/rules/resolve-targets",
        "/resolve-rule-targets", "rule-grounding", PRIMARY_ROLE, "json",
        "Resolve data constraints to focus nodes, constrained paths and related terms.",
        "Interprets either one rule or a structured batch and assigns auditable ontology-term roles. This is semantically richer than ontology/search.",
    ),
    EndpointSpec(
        "shapes.build", "POST", f"{API_PREFIX}/shapes/build", "/build-shacl-shape",
        "shape-generation", PRIMARY_ROLE, "json",
        "Generate and validate one grounded SHACL rule constraint document.",
        "Generates a SHACL document from a previously grounded rule context and validates the resulting document. It does not resolve ontology targets.",
    ),
    EndpointSpec(
        "shapes.validate", "POST", f"{API_PREFIX}/shapes/validate", "/validate-shape",
        "shape-assurance", PRIMARY_ROLE, "json",
        "Validate Turtle syntax and active SHACL for SHACL profiles.",
        "Validates edited, imported or externally generated SHACL without invoking a generation model.",
    ),
    EndpointSpec(
        "baselines.astrea.generate", "POST", f"{API_PREFIX}/baselines/astrea",
        "/generate-astrea-baseline", "shape-assurance", PRIMARY_ROLE, "json",
        "Generate ontology-derived baseline shapes through the Astrea service.",
        "Calls Astrea using the supplied ontology. A client-supplied baseline is represented by BaselineInput in workflows and merge requests and does not call Astrea.",
    ),
    EndpointSpec(
        "shapes.merge", "POST", f"{API_PREFIX}/shapes/merge", "/merge-shapes",
        "shape-assurance", PRIMARY_ROLE, "json",
        "Merge generated shapes with an ontology-derived baseline.",
        "Combines a generated document and a supplied baseline using a deterministic merge strategy, then validates the result.",
    ),
    EndpointSpec(
        "batches.generate", "POST", f"{API_PREFIX}/batches/generate", "/generate-from-batch",
        "authoring-workflow", PRIMARY_ROLE, "sse",
        "Generate shapes from a data-constraint batch and stream progress by constraint.",
        "Batch API operation implementing the Guide-to-Shapes workflow. Returns structured JSON Server-Sent Events; POST /api/v1/workflows/batch-to-shapes returns the consolidated JSON result instead.",
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
        "models.local.download.create", "POST", f"{API_PREFIX}/models/local/downloads",
        None, None, AUXILIARY_ROLE, "json",
        "Create a local-model download job.",
        "Starts a local download in the local deployment profile and returns a stable job resource.",
        success_status=202,
    ),
    EndpointSpec(
        "models.local.download.get", "GET", f"{API_PREFIX}/models/local/downloads/{{job_id}}",
        None, None, AUXILIARY_ROLE, "json",
        "Inspect a local-model download job.",
    ),
    EndpointSpec(
        "models.local.download.delete", "DELETE", f"{API_PREFIX}/models/local/downloads/{{job_id}}",
        None, None, AUXILIARY_ROLE, "json",
        "Cancel a local-model download job.",
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
    endpoint, _ = match_endpoint(method, path)
    return endpoint


def match_endpoint(method: str, path: str) -> Tuple[Optional[EndpointSpec], Dict[str, str]]:
    """Resolve an endpoint and extract path parameters from route templates."""
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "")
    direct = _BY_ROUTE.get((normalized_method, normalized_path))
    if direct is not None:
        return direct, {}
    for endpoint in ENDPOINTS:
        if endpoint.method != normalized_method or "{" not in endpoint.path:
            continue
        names = re.findall(r"\{([^}]+)\}", endpoint.path)
        pattern = "^" + re.sub(r"\{[^}]+\}", r"([^/]+)", endpoint.path) + "$"
        match = re.match(pattern, normalized_path)
        if match:
            return endpoint, dict(zip(names, match.groups()))
    return None, {}


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
    "prepare_terms": "ontology.index.create",
    "resolve_rule": "rules.resolve-targets",
    "build": "shapes.build",
    "validate": "shapes.validate",
    "astrea": "baselines.astrea.generate",
    "merge": "shapes.merge",
    "validate_model": "models.check",
    "local_model_status": "models.local.status",
    "download_local_model": "models.local.download.create",
    "batch": "batches.generate",
}

_SPLIT_SERVICE_ORIGINS = {
    "ontology.parse": "http://127.0.0.1:9100",
    "rules.resolve-targets": "http://127.0.0.1:9104",
    "shapes.build": "http://127.0.0.1:9102",
    "shapes.validate": "http://127.0.0.1:9102",
    "baselines.astrea.generate": "http://127.0.0.1:9102",
    "shapes.merge": "http://127.0.0.1:9102",
    "models.check": "http://127.0.0.1:9102",
    "models.local.status": "http://127.0.0.1:9102",
    "batches.generate": "http://127.0.0.1:9103",
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
        if operation in {
            "ontology.index.create", "models.local.download.create"
        }:
            endpoints[frontend_key] = endpoint.path
            continue
        if selected_layout == UNIFIED_LAYOUT or operation == "system.capabilities":
            endpoints[frontend_key] = endpoint.path
            continue
        origin = _SPLIT_SERVICE_ORIGINS[operation]
        endpoints[frontend_key] = f"{origin}{endpoint.legacy_path}"
    return endpoints
