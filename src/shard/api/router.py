"""Route canonical and compatibility HTTP requests to SHARD operations."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from shard import __description__, __title__, __version__
from shard.api.contract import (
    API_PREFIX,
    api_catalog,
    endpoint_for_route,
    frontend_endpoint_map,
    get_service_layout,
)
from shard.api.http import new_request_id, read_json, send_html, send_json, send_options
from shard.api.openapi import openapi_document
from shard.api.operations import dispatch_post_operation
from shard.api.provenance import request_provenance
from shard.api.swagger_ui import SWAGGER_UI_CSP, swagger_ui_document
from shard.deployment.policy import PROJECT_REPOSITORY_URL, capabilities


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
    return any(endpoint_for_route(method, path) for method in ("GET", "POST"))


def api_root_document():
    """Return concise discovery links for human and machine API clients."""
    return {
        "name": __title__,
        "description": __description__,
        "version": __version__,
        "api_version": active_api_catalog()["version"],
        "docs": f"{API_PREFIX}/docs",
        "openapi": f"{API_PREFIX}/openapi.json",
        "capabilities": f"{API_PREFIX}/capabilities",
        "health": f"{API_PREFIX}/health",
        "workflows": {
            "rule_to_shape": f"{API_PREFIX}/workflows/rule-to-shape",
            "guide_to_shapes": f"{API_PREFIX}/workflows/guide-to-shapes",
            "guide_stream": f"{API_PREFIX}/guides/generate",
        },
        "documentation": f"{API_PREFIX}/docs",
        "api_guide": f"{PROJECT_REPOSITORY_URL}/blob/main/docs/api.md",
        "repository": PROJECT_REPOSITORY_URL,
    }


def dispatch_api_request(handler) -> bool:
    """Dispatch a known API request and report whether it was handled."""
    handler.response_provenance = None
    handler.api_endpoint = None
    path = request_path(handler)
    method = str(handler.command or "").upper()

    if method == "OPTIONS" and (_known_path(path) or path.startswith("/api/")):
        send_options(handler)
        return True

    endpoint = endpoint_for_route(method, path)
    if endpoint is None:
        if path.startswith("/api/"):
            request_id = new_request_id(handler.headers)
            send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)
            return True
        return False

    request_id = new_request_id(handler.headers)
    handler.request_id = request_id
    handler.api_endpoint = endpoint
    if path == endpoint.path:
        handler.response_provenance = request_provenance(endpoint, {}, request_id)

    if method == "GET":
        if endpoint.operation == "system.root":
            send_json(handler, 200, api_root_document(), request_id=request_id)
        elif endpoint.operation == "system.openapi":
            handler.response_provenance = None
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
                "ok": True,
                "service": "shard-api",
                "api_version": active_api_catalog()["version"],
            }, request_id=request_id)
        else:
            send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)
        return True

    try:
        payload = read_json(handler)
    except ValueError as exc:
        send_json(handler, 400, {"error": str(exc)}, request_id=request_id)
        return True

    if path == endpoint.path:
        handler.response_provenance = request_provenance(endpoint, payload, request_id)
    dispatch_post_operation(handler, endpoint.operation, payload, request_id)
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
