"""Generous, configurable operational safeguards for the public SHARD API."""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
import ipaddress
from threading import RLock
from time import monotonic
from typing import Deque, Dict, Iterator, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from shard.api.errors import CapacityExceeded, PayloadTooLarge, RateLimited
from shard.deployment.operational import OperationalSettings, operational_settings


EXPENSIVE_OPERATIONS = {
    "workflows.rule.generate",
    "workflows.batch.generate",
    "ontology.search",
    "ontology.index.create",
    "rules.resolve-targets",
    "shapes.build",
    "baselines.astrea.generate",
    "batches.generate",
    "models.check",
    "models.local.download.create",
}

JOB_CREATION_OPERATIONS = {
    "ontology.index.create",
    "models.local.download.create",
}

RATE_LIMITED_OPERATIONS = EXPENSIVE_OPERATIONS | {
    "ontology.parse",
    "shapes.validate",
    "shapes.merge",
    "models.local.status",
    "ontology.index.get",
    "ontology.index.delete",
    "models.local.download.get",
    "models.local.download.delete",
}


class InMemoryRateLimiter:
    """Per-process, per-client sliding windows with a one-second burst guard."""

    def __init__(self):
        self._events: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def check(
        self,
        client_id: str,
        operation: str,
        *,
        settings: Optional[OperationalSettings] = None,
        now: Optional[float] = None,
    ) -> None:
        if operation not in RATE_LIMITED_OPERATIONS:
            return
        active = settings or operational_settings()
        timestamp = monotonic() if now is None else now
        buckets = [
            ("general-minute", 60.0, active.rate_limit_requests_per_minute),
            ("burst-second", 1.0, active.rate_limit_burst),
        ]
        if operation in EXPENSIVE_OPERATIONS:
            buckets.append((
                "expensive-minute",
                60.0,
                active.rate_limit_expensive_requests_per_minute,
            ))
        if operation in JOB_CREATION_OPERATIONS:
            buckets.append((
                "jobs-minute",
                60.0,
                active.rate_limit_job_creations_per_minute,
            ))

        with self._lock:
            for name, window, maximum in buckets:
                events = self._events[(client_id, name)]
                cutoff = timestamp - window
                while events and events[0] <= cutoff:
                    events.popleft()
                if len(events) >= maximum:
                    retry_after = max(1, int(window - (timestamp - events[0]) + 0.999))
                    raise RateLimited(retry_after_seconds=retry_after)
            for name, _window, _maximum in buckets:
                self._events[(client_id, name)].append(timestamp)


class OperationConcurrencyLimiter:
    """Bound synchronous batch workflows without affecting ordinary API traffic."""

    def __init__(self):
        self._active: Dict[str, int] = defaultdict(int)
        self._lock = RLock()

    def reset(self) -> None:
        with self._lock:
            self._active.clear()

    @contextmanager
    def slot(
        self,
        operation: str,
        *,
        settings: Optional[OperationalSettings] = None,
    ) -> Iterator[None]:
        category = "batch" if operation in {
            "workflows.batch.generate", "batches.generate"
        } else ""
        if not category:
            yield
            return
        active = settings or operational_settings()
        limit = active.max_concurrent_batch_workflows
        with self._lock:
            if self._active[category] >= limit:
                raise CapacityExceeded(
                    "Batch workflow capacity is temporarily exhausted.",
                    {"limit": limit, "resource": "batch_workflows"},
                )
            self._active[category] += 1
        try:
            yield
        finally:
            with self._lock:
                self._active[category] = max(0, self._active[category] - 1)


def request_client_id(handler, settings: Optional[OperationalSettings] = None) -> str:
    """Use forwarded client IPs only when the immediate peer is explicitly trusted."""
    active = settings or operational_settings()
    peer = str((getattr(handler, "client_address", None) or ("unknown",))[0])
    forwarded = str(getattr(handler, "headers", {}).get("X-Forwarded-For", ""))
    if forwarded and peer in active.trusted_proxy_ips:
        candidate = forwarded.split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return peer


def allowed_cors_origin(handler, settings: Optional[OperationalSettings] = None) -> Optional[str]:
    """Return an allowed request origin, including an exact same-origin request."""
    origin = str(getattr(handler, "headers", {}).get("Origin", "")).strip()
    if not origin:
        return None
    active = settings or operational_settings()
    if origin in active.cors_allowed_origins:
        return origin
    host = str(getattr(handler, "headers", {}).get("Host", "")).strip().lower()
    parsed = urlsplit(origin)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host:
        return origin
    return None


RATE_LIMITER = InMemoryRateLimiter()
CONCURRENCY_LIMITER = OperationConcurrencyLimiter()


def _content_size(value: object) -> int:
    return len(str(value or "").encode("utf-8"))


def _enforce_content_limit(content: object, limit_mb: int, resource: str) -> None:
    if _content_size(content) <= limit_mb * 1024 * 1024:
        return
    raise PayloadTooLarge(
        f"The {resource} exceeds the configured {limit_mb} MB limit.",
        {"limit_mb": limit_mb, "resource": resource},
    )


def validate_operation_payload_size(
    operation: str,
    payload: Mapping[str, object],
    *,
    settings: Optional[OperationalSettings] = None,
) -> None:
    """Apply resource-specific limits after the generic HTTP body limit."""
    active = settings or operational_settings()
    ontology = payload.get("ontology")
    if isinstance(ontology, Mapping):
        _enforce_content_limit(
            ontology.get("content"), active.max_ontology_upload_mb, "ontology upload"
        )
    elif payload.get("ontology_content") is not None:
        _enforce_content_limit(
            payload.get("ontology_content"), active.max_ontology_upload_mb, "ontology upload"
        )

    batch = payload.get("batch")
    if isinstance(batch, Mapping):
        _enforce_content_limit(
            batch.get("content"), active.max_batch_upload_mb, "batch upload"
        )
    elif payload.get("batch_content") is not None:
        _enforce_content_limit(
            payload.get("batch_content"), active.max_batch_upload_mb, "batch upload"
        )

    shape_values = []
    if payload.get("shape_document") is not None:
        shape_values.append(payload.get("shape_document"))
    if payload.get("shape") is not None:
        shape_values.append(payload.get("shape"))
    for key in ("generated", "baseline"):
        document = payload.get(key)
        if isinstance(document, Mapping):
            shape_values.append(document.get("content"))
    for content in shape_values:
        _enforce_content_limit(
            content, active.max_shape_document_mb, "SHACL document"
        )

    validation = payload.get("validation")
    profiles = validation.get("profiles") if isinstance(validation, Mapping) else None
    if profiles is None:
        profiles = payload.get("validation_profiles")
    for profile in profiles if isinstance(profiles, list) else []:
        if isinstance(profile, Mapping):
            _enforce_content_limit(
                profile.get("content"),
                active.max_validation_profile_mb,
                "validation profile",
            )
