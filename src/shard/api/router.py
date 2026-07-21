"""Route canonical and compatibility HTTP requests to SHARD operations."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from time import perf_counter
from urllib.parse import urlsplit

from shard import __description__, __title__, __version__
from pydantic import ValidationError

from shard.api.contract import (
    API_PREFIX,
    api_catalog,
    endpoint_for_route,
    frontend_endpoint_map,
    get_service_layout,
    match_endpoint,
)
from shard.api.errors import (
    ApiException,
    InternalFailure,
    MalformedJson,
    RequestSchemaError,
    ResourceNotFound,
    collect_secrets,
    pydantic_error_details,
)
from shard.api.http import new_request_id, read_json, send_api_error, send_html, send_json, send_options
from shard.api.models import (
    to_application_payload,
    validate_request,
    validate_request_consistency,
)
from shard.api.openapi import openapi_document
from shard.api.operations import dispatch_operation
from shard.api.operational import (
    CONCURRENCY_LIMITER,
    RATE_LIMITER,
    request_client_id,
    validate_operation_payload_size,
)
from shard.api.provenance import (
    is_authoring_operation,
    request_authoring_provenance,
    request_operation_metadata,
)
from shard.api.redoc_ui import REDOC_CSP, redoc_document
from shard.api.swagger_ui import SWAGGER_UI_CSP, swagger_ui_document
from shard.deployment.policy import PROJECT_REPOSITORY_URL, capabilities
from shard.observability import logger


def request_path(handler) -> str:
    """Return the URL path without query or fragment components."""
    return urlsplit(handler.path).path


def active_api_catalog():
    """Return contract metadata enriched with the active runtime layout."""
    layout = get_service_layout()
    return {
        **api_catalog(),
        "service_layout": layout,
        "runtime_endpoints": frontend_endpoint_map(layout),
    }


def _known_path(path: str) -> bool:
    return any(endpoint_for_route(method, path) for method in ("GET", "POST", "DELETE"))


def api_root_document():
    """Return concise discovery links for human and machine API clients."""
    return {
        "name": __title__,
        "description": __description__,
        "version": __version__,
        "api_version": active_api_catalog()["version"],
        "docs": f"{API_PREFIX}/docs",
        "redoc": f"{API_PREFIX}/redoc",
        "openapi": f"{API_PREFIX}/openapi.json",
        "capabilities": f"{API_PREFIX}/capabilities",
        "health": f"{API_PREFIX}/health",
        "workflows": {
            "rule_to_shape": f"{API_PREFIX}/workflows/rule-to-shape",
            "batch_to_shapes": f"{API_PREFIX}/workflows/batch-to-shapes",
            "batch_stream": f"{API_PREFIX}/batches/generate",
        },
        "documentation": f"{API_PREFIX}/docs",
        "api_documentation": f"{PROJECT_REPOSITORY_URL}/blob/main/docs/api.md",
        "repository": PROJECT_REPOSITORY_URL,
    }


def dispatch_api_request(handler) -> bool:
    """Dispatch a known API request and report whether it was handled."""
    handler.response_provenance = None
    handler.response_operation_metadata = None
    handler.request_started_at = perf_counter()
    handler.api_endpoint = None
    handler.api_is_canonical = False
    handler.path_params = {}
    handler.request_secrets = []
    path = request_path(handler)
    method = str(handler.command or "").upper()

    if method == "OPTIONS" and (_known_path(path) or path.startswith("/api/")):
        send_options(handler)
        return True

    endpoint, path_params = match_endpoint(method, path)
    if endpoint is None:
        if path.startswith("/api/"):
            request_id = new_request_id(handler.headers)
            handler.api_is_canonical = True
            send_api_error(
                handler,
                ResourceNotFound(f"No API endpoint exists at '{path}'."),
                request_id,
            )
            return True
        return False

    request_id = new_request_id(handler.headers)
    handler.request_id = request_id
    handler.api_endpoint = endpoint
    handler.path_params = path_params
    handler.api_is_canonical = path.startswith(API_PREFIX)
    if handler.api_is_canonical:
        handler.response_operation_metadata = request_operation_metadata(
            endpoint, request_id
        )
        if is_authoring_operation(endpoint.operation):
            handler.response_provenance = request_authoring_provenance(endpoint, {})
    try:
        RATE_LIMITER.check(request_client_id(handler), endpoint.operation)
    except ApiException as exc:
        send_api_error(handler, exc, request_id)
        return True

    if method == "GET":
        if endpoint.operation == "system.root":
            send_json(handler, 200, api_root_document(), request_id=request_id)
        elif endpoint.operation == "system.openapi":
            handler.response_provenance = None
            handler.response_operation_metadata = None
            send_json(
                handler,
                200,
                openapi_document(),
                request_id=request_id,
                content_type="application/vnd.oai.openapi+json",
                decorate=False,
            )
        elif endpoint.operation == "system.docs":
            send_html(
                handler,
                200,
                swagger_ui_document(f"{API_PREFIX}/openapi.json"),
                request_id=request_id,
                content_security_policy=SWAGGER_UI_CSP,
            )
        elif endpoint.operation == "system.redoc":
            send_html(
                handler,
                200,
                redoc_document(f"{API_PREFIX}/openapi.json"),
                request_id=request_id,
                content_security_policy=REDOC_CSP,
            )
        elif endpoint.operation in {
            "ontology.index.get", "models.local.download.get",
        }:
            with CONCURRENCY_LIMITER.slot(endpoint.operation):
                dispatch_operation(handler, endpoint.operation, {}, request_id)
        elif endpoint.operation == "system.capabilities":
            document = capabilities()
            if path == endpoint.path:
                document = {**document, "api": active_api_catalog()}
            send_json(
                handler,
                200,
                document,
                request_id=request_id if path == endpoint.path else None,
            )
        elif endpoint.operation == "system.health":
            send_json(handler, 200, {
                "status": "ok",
                "version": __version__,
                "deployment_profile": capabilities()["deployment_profile"],
            }, request_id=request_id)
        else:
            send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)
        return True

    payload = {}
    if method in {"POST", "PUT", "PATCH"}:
        try:
            payload = read_json(handler)
        except ApiException as exc:
            send_api_error(handler, exc, request_id)
            return True
    handler.request_secrets = collect_secrets(payload)
    if method in {"POST", "PUT", "PATCH"}:
        try:
            validate_operation_payload_size(endpoint.operation, payload)
        except ApiException as exc:
            send_api_error(handler, exc, request_id)
            return True
    if handler.api_is_canonical and method in {"POST", "PUT", "PATCH"}:
        try:
            canonical_payload = validate_request(endpoint.operation, payload)
            validate_request_consistency(canonical_payload)
        except ApiException as exc:
            send_api_error(handler, exc, request_id)
            return True
        except ValidationError as exc:
            send_api_error(
                handler,
                RequestSchemaError(
                    "Request body validation failed.",
                    pydantic_error_details(exc),
                ),
                request_id,
            )
            return True
        if is_authoring_operation(endpoint.operation):
            handler.response_provenance = request_authoring_provenance(
                endpoint, canonical_payload
            )
        payload = to_application_payload(endpoint.operation, canonical_payload)
    elif handler.api_is_canonical:
        if is_authoring_operation(endpoint.operation):
            handler.response_provenance = request_authoring_provenance(endpoint, payload)
    try:
        with logger.secret_context(handler.request_secrets):
            with CONCURRENCY_LIMITER.slot(endpoint.operation):
                dispatch_operation(handler, endpoint.operation, payload, request_id)
    except ApiException as exc:
        send_api_error(handler, exc, request_id)
    except Exception:
        send_api_error(handler, InternalFailure(), request_id)
    return True


class Handler(BaseHTTPRequestHandler):
    """Standalone request handler for the unified API facade."""

    def log_message(self, *args):
        pass

    def do_OPTIONS(self):
        if not dispatch_api_request(self):
            send_json(self, 404, {"error": "unknown endpoint"})

    def do_GET(self):
        if not dispatch_api_request(self):
            send_json(self, 404, {"error": "unknown endpoint"})

    def do_POST(self):
        if not dispatch_api_request(self):
            send_json(self, 404, {"error": "unknown endpoint"})

    def do_DELETE(self):
        if not dispatch_api_request(self):
            send_json(self, 404, {"error": "unknown endpoint"})
