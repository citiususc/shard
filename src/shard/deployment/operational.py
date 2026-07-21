"""Environment-backed operational settings shared across SHARD layers."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _integer(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


def _seconds(name: str, default: float, *, aliases: tuple[str, ...] = ()) -> float:
    raw = next(
        (os.environ[key] for key in (name, *aliases) if os.environ.get(key) not in (None, "")),
        None,
    )
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number of seconds.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


def _csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class OperationalSettings:
    """Runtime safeguards whose defaults target abuse, not ordinary demo use."""

    rate_limit_requests_per_minute: int = 1000
    rate_limit_burst: int = 200
    rate_limit_expensive_requests_per_minute: int = 120
    rate_limit_job_creations_per_minute: int = 60
    http_connect_timeout_seconds: float = 60.0
    http_read_timeout_seconds: float = 1800.0
    model_timeout_seconds: float = 1800.0
    embedding_timeout_seconds: int = 3600
    astrea_timeout_seconds: float = 1800.0
    batch_workflow_timeout_seconds: float = 7200.0
    sse_idle_timeout_seconds: float = 1800.0
    job_max_runtime_seconds: float = 7200.0
    max_request_body_mb: int = 256
    max_ontology_upload_mb: int = 200
    max_batch_upload_mb: int = 50
    max_validation_profile_mb: int = 20
    max_shape_document_mb: int = 50
    max_concurrent_jobs: int = 50
    max_concurrent_batch_workflows: int = 20
    max_concurrent_model_downloads: int = 5
    max_queued_jobs: int = 500
    cors_allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8768",
        "http://localhost:8768",
    )
    trusted_proxy_ips: tuple[str, ...] = ()


def operational_settings() -> OperationalSettings:
    """Read operational limits without caching environment overrides."""
    return OperationalSettings(
        rate_limit_requests_per_minute=_integer("RATE_LIMIT_REQUESTS_PER_MINUTE", 1000),
        rate_limit_burst=_integer("RATE_LIMIT_BURST", 200),
        rate_limit_expensive_requests_per_minute=_integer(
            "RATE_LIMIT_EXPENSIVE_REQUESTS_PER_MINUTE", 120
        ),
        rate_limit_job_creations_per_minute=_integer(
            "RATE_LIMIT_JOB_CREATIONS_PER_MINUTE", 60
        ),
        http_connect_timeout_seconds=_seconds("HTTP_CONNECT_TIMEOUT_SECONDS", 60),
        http_read_timeout_seconds=_seconds("HTTP_READ_TIMEOUT_SECONDS", 1800),
        model_timeout_seconds=_seconds("MODEL_TIMEOUT_SECONDS", 1800),
        embedding_timeout_seconds=_integer("EMBEDDING_TIMEOUT_SECONDS", 3600),
        astrea_timeout_seconds=_seconds(
            "ASTREA_TIMEOUT_SECONDS", 1800, aliases=("SHARD_ASTREA_TIMEOUT",)
        ),
        batch_workflow_timeout_seconds=_seconds("BATCH_WORKFLOW_TIMEOUT_SECONDS", 7200),
        sse_idle_timeout_seconds=_seconds("SSE_IDLE_TIMEOUT_SECONDS", 1800),
        job_max_runtime_seconds=_seconds("JOB_MAX_RUNTIME_SECONDS", 7200),
        max_request_body_mb=_integer("MAX_REQUEST_BODY_MB", 256),
        max_ontology_upload_mb=_integer("MAX_ONTOLOGY_UPLOAD_MB", 200),
        max_batch_upload_mb=_integer("MAX_BATCH_UPLOAD_MB", 50),
        max_validation_profile_mb=_integer("MAX_VALIDATION_PROFILE_MB", 20),
        max_shape_document_mb=_integer("MAX_SHAPE_DOCUMENT_MB", 50),
        max_concurrent_jobs=_integer("MAX_CONCURRENT_JOBS", 50),
        max_concurrent_batch_workflows=_integer("MAX_CONCURRENT_BATCH_WORKFLOWS", 20),
        max_concurrent_model_downloads=_integer("MAX_CONCURRENT_MODEL_DOWNLOADS", 5),
        max_queued_jobs=_integer("MAX_QUEUED_JOBS", 500),
        cors_allowed_origins=_csv(
            "SHARD_CORS_ALLOWED_ORIGINS",
            ("http://127.0.0.1:8768", "http://localhost:8768"),
        ),
        trusted_proxy_ips=_csv("SHARD_TRUSTED_PROXY_IPS"),
    )
