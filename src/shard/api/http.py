"""Small HTTP helpers shared by SHARD transport adapters."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional


def new_request_id(headers=None) -> str:
    if headers:
        incoming = headers.get("X-Request-ID") or headers.get("X-Request-Id")
        if incoming:
            return str(incoming)[:80]
    return uuid.uuid4().hex[:12]


def read_json(handler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) or b"{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON request body: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON request body must be an object.")
    return payload


def send_json(
    handler,
    status: int,
    payload: Dict[str, Any],
    request_id: Optional[str] = None,
    *,
    content_type: str = "application/json",
    decorate: bool = True,
) -> None:
    provenance = getattr(handler, "response_provenance", None)
    if decorate and provenance and "provenance" not in payload:
        payload = {**payload, "provenance": provenance}
    if decorate and request_id:
        payload = {**payload, "request_id": request_id}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    if request_id:
        handler.send_header("X-Request-ID", request_id)
    send_provenance_headers(handler)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_html(
    handler,
    status: int,
    document: str,
    request_id: Optional[str] = None,
    *,
    content_security_policy: str = "",
) -> None:
    """Send an HTML document with the standard API response headers."""
    body = str(document or "").encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    if content_security_policy:
        handler.send_header("Content-Security-Policy", content_security_policy)
    if request_id:
        handler.send_header("X-Request-ID", request_id)
    send_provenance_headers(handler)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_provenance_headers(handler) -> None:
    """Add versioned API headers when request provenance is active."""
    provenance = getattr(handler, "response_provenance", None)
    if not provenance:
        return
    api_version = str(provenance.get("api_version", ""))
    operation = str(provenance.get("operation", ""))
    handler.send_header("X-SHARD-API-Version", api_version)
    handler.send_header("X-SHARD-Operation", operation)
    # Retain the former names during the API v1 compatibility window.
    handler.send_header("X-BR2SHACL-API-Version", api_version)
    handler.send_header("X-BR2SHACL-Operation", operation)


def send_health(handler, service_name: str, request_id: Optional[str] = None) -> None:
    send_json(handler, 200, {"ok": True, "service": service_name}, request_id=request_id)


def send_options(handler) -> None:
    handler.send_response(200)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def reject_disabled_provider(handler, payload: Dict[str, Any], request_id: Optional[str] = None) -> bool:
    """Send a policy error and return True when a provider is disabled."""
    from shard.deployment.policy import ProviderDisabledError, ensure_request_provider_enabled

    try:
        ensure_request_provider_enabled(payload)
    except ProviderDisabledError as exc:
        send_json(handler, exc.status, exc.as_payload(), request_id=request_id)
        return True
    return False
