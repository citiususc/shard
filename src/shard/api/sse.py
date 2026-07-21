"""Structured and thread-safe Server-Sent Event transport for SHARD."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from time import monotonic, perf_counter
from threading import Event, RLock, Thread
from typing import Any, Dict, Optional

from shard.api.contract import API_VERSION
from shard.api.models import SseEvent
from shard.api.operational import operational_settings
from shard.deployment.policy import get_deployment_profile


TERMINAL_EVENTS = {"completed", "failed"}


class SseWriter:
    """Write named JSON SSE events and emit heartbeats while work is active."""

    def __init__(
        self,
        handler,
        request_id: str,
        *,
        heartbeat_seconds: float = 15.0,
        idle_timeout_seconds: Optional[float] = None,
    ):
        self.handler = handler
        self.request_id = request_id
        self.heartbeat_seconds = heartbeat_seconds
        self.idle_timeout_seconds = (
            operational_settings().sse_idle_timeout_seconds
            if idle_timeout_seconds is None
            else float(idle_timeout_seconds)
        )
        self._last_activity = monotonic()
        self.sequence = 0
        self.closed = Event()
        self.disconnected = Event()
        self._lock = RLock()
        self._heartbeat: Optional[Thread] = None

    def start(self) -> None:
        if self.heartbeat_seconds <= 0:
            return
        self._heartbeat = Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

    def close(self) -> None:
        self.closed.set()

    def send(self, event_name: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        if self.closed.is_set() or self.disconnected.is_set():
            return False
        document = dict(payload or {})
        with self._lock:
            self.sequence += 1
            document.update({
                "event": event_name,
                "request_id": self.request_id,
                "sequence": self.sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            metadata = getattr(self.handler, "response_operation_metadata", None)
            if not metadata:
                metadata = {
                    "request_id": self.request_id,
                    "operation": "stream",
                    "service": "platform",
                    "api_version": API_VERSION,
                    "deployment_profile": get_deployment_profile(),
                    "created_at": document["timestamp"],
                    "duration_ms": 0.0,
                    "warnings": [],
                }
            else:
                metadata = dict(metadata)
                started = getattr(self.handler, "request_started_at", None)
                if started is not None:
                    metadata["duration_ms"] = max(
                        0.0, (perf_counter() - started) * 1000.0
                    )
            document.setdefault("operation_metadata", metadata)
            provenance = getattr(self.handler, "response_provenance", None)
            if provenance:
                document.setdefault("provenance", provenance)
            document = SseEvent.model_validate(document).model_dump(
                mode="json", exclude_none=True
            )
            body = json.dumps(document, ensure_ascii=False)
            frame = f"id: {self.sequence}\nevent: {event_name}\ndata: {body}\n\n"
            try:
                self.handler.wfile.write(frame.encode("utf-8"))
                self.handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.disconnected.set()
                self.closed.set()
                return False
        if event_name != "heartbeat":
            self._last_activity = monotonic()
        if event_name in TERMINAL_EVENTS:
            self.close()
        return True

    def _heartbeat_loop(self) -> None:
        while not self.closed.wait(self.heartbeat_seconds):
            if (
                self.idle_timeout_seconds > 0
                and monotonic() - self._last_activity >= self.idle_timeout_seconds
            ):
                self.send("failed", {
                    "error": {
                        "error": "upstream_timeout",
                        "code": "SSE_IDLE_TIMEOUT",
                        "message": "The stream produced no work progress before its idle timeout.",
                        "request_id": self.request_id,
                        "details": {},
                    },
                })
                return
            if not self.send("heartbeat", {"message": "Connection alive."}):
                return
