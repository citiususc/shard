"""Stable HTTP errors and secret-safe exception translation for SHARD."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional

from pydantic import ValidationError


SECRET_FIELD_NAMES = {
    "api_key",
    "authorization",
    "databricks_token",
    "hf_token",
    "huggingface_token",
    "password",
    "secret",
    "token",
}


def collect_secrets(value: Any) -> list[str]:
    """Collect request secret values without retaining their field locations."""
    result: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().lower() in SECRET_FIELD_NAMES:
                secret = str(child or "")
                if secret:
                    result.append(secret)
            else:
                result.extend(collect_secrets(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            result.extend(collect_secrets(child))
    return result


def redact_text(value: Any, secrets: Iterable[str] = ()) -> str:
    """Redact supplied secrets and common bearer-token forms from text."""
    text = str(value or "")
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+",
        r"\1[redacted]",
        text,
    )
    return text


@dataclass
class ApiException(Exception):
    """Base exception carrying a stable API status and machine code."""

    status: int
    error: str
    code: str
    message: str
    details: Optional[Dict[str, Any]] = field(default=None)
    headers: Dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class InvalidBusinessInput(ApiException):
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        *,
        code: str = "INVALID_REQUEST",
    ):
        super().__init__(400, "invalid_request", code, message, details)


class MalformedJson(ApiException):
    def __init__(self, message: str = "The request body is not valid JSON."):
        super().__init__(400, "invalid_request", "MALFORMED_JSON", message)


class CapabilityDisabled(ApiException):
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        *,
        code: str = "CAPABILITY_DISABLED",
    ):
        super().__init__(403, "capability_disabled", code, message, details)


class ResourceNotFound(ApiException):
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        *,
        code: str = "RESOURCE_NOT_FOUND",
    ):
        super().__init__(404, "resource_not_found", code, message, details)


class ConflictingJobState(ApiException):
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        *,
        code: str = "JOB_STATE_CONFLICT",
    ):
        super().__init__(409, "job_state_conflict", code, message, details)


class RequestSchemaError(ApiException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            422,
            "request_validation_failed",
            "REQUEST_SCHEMA_VALIDATION_FAILED",
            message,
            details,
        )


class RateLimited(ApiException):
    def __init__(
        self,
        message: str = "The request rate limit was exceeded.",
        *,
        retry_after_seconds: int = 1,
    ):
        super().__init__(
            429,
            "rate_limit_exceeded",
            "DEPLOYMENT_RATE_LIMIT_EXCEEDED",
            message,
            {"retry_after_seconds": retry_after_seconds},
            {"Retry-After": str(retry_after_seconds)},
        )


class PayloadTooLarge(ApiException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            413,
            "payload_too_large",
            "PAYLOAD_TOO_LARGE",
            message,
            details,
        )


class CapacityExceeded(ApiException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            503,
            "capacity_exhausted",
            "DEPLOYMENT_CAPACITY_EXHAUSTED",
            message,
            details,
        )


class InternalFailure(ApiException):
    def __init__(self, message: str = "An unexpected internal error occurred."):
        super().__init__(
            500,
            "internal_failure",
            "UNEXPECTED_INTERNAL_ERROR",
            message,
        )


class InvalidUpstreamResponse(ApiException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            502,
            "invalid_upstream_response",
            "MODEL_INVALID_RESPONSE",
            message,
            details,
        )


class ProviderUnavailable(ApiException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            503,
            "upstream_unavailable",
            "MODEL_UNAVAILABLE",
            message,
            details,
        )


class ProviderTimeout(ApiException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            504,
            "upstream_timeout",
            "MODEL_REQUEST_TIMEOUT",
            message,
            details,
        )


def pydantic_error_details(error: ValidationError) -> Dict[str, Any]:
    """Return serializable field errors without echoing request values."""
    issues = []
    for issue in error.errors(include_input=False, include_url=False):
        issues.append({
            "location": [str(part) for part in issue.get("loc", ())],
            "message": issue.get("msg", "Invalid value."),
            "type": issue.get("type", "value_error"),
        })
    return {"issues": issues}


def api_error_payload(
    error: ApiException,
    request_id: str,
    *,
    secrets: Iterable[str] = (),
) -> Dict[str, Any]:
    """Serialize one API exception using the single public error contract."""
    message = redact_text(error.message, secrets)
    details = error.details
    if details is not None:
        details = redact_value(details, secrets)
    return {
        "error": error.error,
        "code": error.code,
        "message": message,
        "request_id": request_id,
        "details": details or {},
    }


def redact_value(
    value: Any,
    secrets: Iterable[str],
    *,
    redact_secret_fields: bool = True,
) -> Any:
    """Redact runtime secret fields while allowing schema descriptions to retain their names."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[redacted]"
                if redact_secret_fields and str(key).strip().lower() in SECRET_FIELD_NAMES
                else redact_value(
                    child,
                    secrets,
                    redact_secret_fields=redact_secret_fields,
                )
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            redact_value(child, secrets, redact_secret_fields=redact_secret_fields)
            for child in value
        ]
    if isinstance(value, str):
        return redact_text(value, secrets)
    return value
