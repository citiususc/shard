"""Small HTTP helpers shared by SHARD transport adapters."""

from __future__ import annotations

import json
from time import perf_counter
import uuid
from typing import Any, Dict, Mapping, Optional

from shard.api.errors import (
    ApiException,
    InternalFailure,
    MalformedJson,
    api_error_payload,
    collect_secrets,
    redact_value,
)
from shard.api.operational import allowed_cors_origin, operational_settings


def new_request_id(headers=None) -> str:
    if headers:
        incoming = headers.get("X-Request-ID") or headers.get("X-Request-Id")
        if incoming:
            return str(incoming)[:80]
    return uuid.uuid4().hex[:12]


def read_json(handler) -> Dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise MalformedJson("Content-Length must be an integer.") from exc
    if length < 0:
        raise MalformedJson("Content-Length must not be negative.")
    limit_mb = operational_settings().max_request_body_mb
    if length > limit_mb * 1024 * 1024:
        from shard.api.errors import PayloadTooLarge

        raise PayloadTooLarge(
            f"The JSON request body exceeds the configured {limit_mb} MB limit.",
            {"limit_mb": limit_mb, "resource": "request_body"},
        )
    raw = handler.rfile.read(length) or b"{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedJson("The request body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise MalformedJson("The JSON request body must be an object.")
    return payload


def send_json(
    handler,
    status: int,
    payload: Dict[str, Any],
    request_id: Optional[str] = None,
    *,
    content_type: str = "application/json",
    decorate: bool = True,
    extra_headers: Optional[Mapping[str, str]] = None,
) -> None:
    is_canonical = bool(getattr(handler, "api_is_canonical", False))
    operation = str(getattr(getattr(handler, "api_endpoint", None), "operation", ""))
    secrets = getattr(handler, "request_secrets", ())
    if is_canonical and status >= 400:
        if {"error", "code", "message"}.issubset(payload):
            error = ApiException(
                status,
                str(payload.get("error")),
                str(payload.get("code")),
                str(payload.get("message")),
                payload.get("details"),
            )
        else:
            code = str(payload.get("code") or payload.get("error_type") or "REQUEST_FAILED")
            message = str(payload.get("message") or payload.get("error") or HTTP_STATUS_MESSAGES.get(status, "Request failed."))
            error = ApiException(
                status,
                ERROR_CATEGORIES.get(status, "request_failed"),
                code.upper(),
                message,
            )
        payload = api_error_payload(error, request_id or "", secrets=secrets)
        decorate = False
    elif is_canonical and status < 400 and operation not in {"system.openapi", "system.docs", "system.redoc"}:
        from pydantic import ValidationError
        from shard.api.models import canonicalize_success, enrich_provenance, validate_response

        payload = canonicalize_success(operation, payload)
        provenance = getattr(handler, "response_provenance", None)
        operation_metadata = getattr(handler, "response_operation_metadata", None)
        if operation_metadata:
            started = getattr(handler, "request_started_at", None)
            if started is not None:
                operation_metadata = {
                    **operation_metadata,
                    "duration_ms": max(0.0, (perf_counter() - started) * 1000.0),
                }
                handler.response_operation_metadata = operation_metadata
        if provenance:
            provenance = enrich_provenance(operation, provenance, payload)
            handler.response_provenance = provenance
        if provenance and "provenance" not in payload:
            payload = {**payload, "provenance": provenance}
        if operation_metadata and "operation_metadata" not in payload:
            payload = {**payload, "operation_metadata": operation_metadata}
        if request_id:
            payload = {**payload, "request_id": request_id}
        try:
            payload = validate_response(operation, payload)
        except ValidationError:
            error = InternalFailure("The service produced a response that violates its public contract.")
            payload = api_error_payload(error, request_id or "", secrets=secrets)
            status = 500
        decorate = False
    provenance = getattr(handler, "response_provenance", None)
    if decorate and provenance and "provenance" not in payload:
        payload = {**payload, "provenance": provenance}
    if decorate and request_id:
        payload = {**payload, "request_id": request_id}
    payload = redact_value(
        payload,
        secrets,
        redact_secret_fields=operation != "system.openapi",
    )
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        send_cors_headers(handler)
        if request_id:
            handler.send_header("X-Request-ID", request_id)
        for name, value in (extra_headers or {}).items():
            handler.send_header(str(name), str(value))
        send_provenance_headers(handler)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        # The client closed the connection while a long-running operation was
        # completing. There is no receiver for either the result or an error.
        return


HTTP_STATUS_MESSAGES = {
    400: "The request is invalid.",
    401: "An inference provider rejected its provider credential.",
    403: "The requested capability is disabled.",
    404: "The resource was not found.",
    409: "The request conflicts with current state.",
    413: "The request payload is too large.",
    422: "The request does not match the schema.",
    429: "The request rate limit was exceeded.",
    500: "An unexpected internal error occurred.",
    502: "An upstream response was invalid.",
    503: "A provider is unavailable.",
    504: "A provider timed out.",
}


ERROR_CATEGORIES = {
    400: "invalid_request",
    401: "provider_authentication_failed",
    403: "capability_disabled",
    404: "resource_not_found",
    409: "job_state_conflict",
    413: "payload_too_large",
    422: "request_validation_failed",
    429: "rate_limit_exceeded",
    500: "internal_failure",
    502: "invalid_upstream_response",
    503: "upstream_unavailable",
    504: "upstream_timeout",
}


def send_api_error(handler, error: ApiException, request_id: str) -> None:
    """Send one canonical ApiError or a compatibility error payload."""
    secrets = getattr(handler, "request_secrets", ())
    if getattr(handler, "api_is_canonical", False):
        send_json(
            handler,
            error.status,
            api_error_payload(error, request_id, secrets=secrets),
            request_id=request_id,
            decorate=False,
            extra_headers=error.headers,
        )
    else:
        send_json(
            handler,
            error.status,
            {
                "error": error.message,
                "code": error.code,
                "message": error.message,
            },
            request_id=request_id,
            extra_headers=error.headers,
        )


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
    send_cors_headers(handler)
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
    metadata = getattr(handler, "response_operation_metadata", None)
    if not metadata:
        return
    api_version = str(metadata.get("api_version", ""))
    operation = str(metadata.get("operation", ""))
    handler.send_header("X-SHARD-API-Version", api_version)
    handler.send_header("X-SHARD-Operation", operation)
    # Retain the former names during the API v1 compatibility window.
    handler.send_header("X-BR2SHACL-API-Version", api_version)
    handler.send_header("X-BR2SHACL-Operation", operation)


def send_health(handler, service_name: str, request_id: Optional[str] = None) -> None:
    send_json(handler, 200, {"ok": True, "service": service_name}, request_id=request_id)


def send_options(handler) -> None:
    handler.send_response(204)
    send_cors_headers(handler)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def send_cors_headers(handler) -> None:
    """Emit explicit CORS headers only for configured or exact same origins."""
    origin = allowed_cors_origin(handler)
    if origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")


def reject_disabled_provider(handler, payload: Dict[str, Any], request_id: Optional[str] = None) -> bool:
    """Send a policy error and return True when a provider is disabled."""
    from shard.deployment.policy import ProviderDisabledError, ensure_request_provider_enabled

    try:
        ensure_request_provider_enabled(payload)
    except ProviderDisabledError as exc:
        from shard.api.errors import CapabilityDisabled

        send_api_error(
            handler,
            CapabilityDisabled(
                str(exc),
                {"provider": exc.provider},
                code=(
                    "LOCAL_MODELS_DISABLED"
                    if exc.provider == "huggingface"
                    else "PROVIDER_DISABLED_BY_PROFILE"
                ),
            ),
            request_id or "",
        )
        return True
    return False
