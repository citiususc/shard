"""
Small HTTP helpers shared by the demo services.
"""

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


def send_json(handler, status: int, payload: Dict[str, Any], request_id: Optional[str] = None) -> None:
    if request_id:
        payload = {**payload, "request_id": request_id}
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    if request_id:
        handler.send_header("X-Request-ID", request_id)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_health(handler, service_name: str, request_id: Optional[str] = None) -> None:
    send_json(handler, 200, {"ok": True, "service": service_name}, request_id=request_id)


def send_options(handler) -> None:
    handler.send_response(200)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Content-Length", "0")
    handler.end_headers()
