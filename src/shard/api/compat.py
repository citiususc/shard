"""Optional loopback listeners for pre-versioned SHARD endpoint paths.

The public deployment uses the unified versioned API. These listeners preserve
the historical development ports without duplicating application logic.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import FrozenSet
from urllib.parse import urlsplit

from shard.api.contract import endpoint_for_route
from shard.api.errors import ApiException
from shard.api.http import new_request_id, read_json, send_health, send_json, send_options
from shard.api.operations import dispatch_post_operation


@dataclass(frozen=True)
class CompatibilityService:
    """Definition of one historical listener."""

    service_id: str
    port: int
    operations: FrozenSet[str]


COMPATIBILITY_SERVICES = {
    "ontology": CompatibilityService("ontology", 9100, frozenset({"ontology.parse"})),
    "term-ranking": CompatibilityService(
        "term-ranking",
        9101,
        frozenset({
            "ontology.search",
        }),
    ),
    "shapes": CompatibilityService(
        "shapes",
        9102,
        frozenset({
            "baselines.astrea.generate",
            "shapes.build",
            "shapes.validate",
            "shapes.merge",
            "models.check",
            "models.local.status",
        }),
    ),
    "batch": CompatibilityService("batch", 9103, frozenset({"batches.generate"})),
    "target-resolution": CompatibilityService(
        "target-resolution",
        9104,
        frozenset({"rules.resolve-targets"}),
    ),
}


def make_handler(service: CompatibilityService):
    """Build a request handler restricted to one compatibility service."""

    class CompatibilityHandler(BaseHTTPRequestHandler):
        compatibility_service = service

        def log_message(self, *args):
            pass

        def _path(self):
            return urlsplit(self.path).path

        def _endpoint(self):
            endpoint = endpoint_for_route(str(self.command or "").upper(), self._path())
            if endpoint is None or endpoint.operation not in service.operations:
                return None
            if self._path() != endpoint.legacy_path:
                return None
            return endpoint

        def do_OPTIONS(self):
            send_options(self)

        def do_GET(self):
            request_id = new_request_id(self.headers)
            if self._path() == "/health":
                send_health(self, service.service_id, request_id=request_id)
                return
            send_json(self, 404, {"error": "unknown endpoint"}, request_id=request_id)

        def do_POST(self):
            request_id = new_request_id(self.headers)
            endpoint = self._endpoint()
            if endpoint is None:
                send_json(self, 404, {"error": "unknown endpoint"}, request_id=request_id)
                return
            try:
                payload = read_json(self)
            except ApiException as exc:
                send_json(self, 400, {"error": str(exc)}, request_id=request_id)
                return
            dispatch_post_operation(self, endpoint.operation, payload, request_id)

    CompatibilityHandler.__name__ = f"{service.service_id.title().replace('-', '')}Handler"
    return CompatibilityHandler


def compatibility_server_specs():
    """Return listener names, ports and handlers for embedded startup."""
    return tuple(
        (service.service_id, service.port, make_handler(service))
        for service in COMPATIBILITY_SERVICES.values()
    )


def main(argv=None):
    """Run one compatibility listener as a standalone process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=tuple(COMPATIBILITY_SERVICES))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    service = COMPATIBILITY_SERVICES[args.service]
    handler = make_handler(service)
    print(f"SHARD {service.service_id} compatibility listener: http://{args.host}:{service.port}")
    ThreadingHTTPServer((args.host, service.port), handler).serve_forever()


if __name__ == "__main__":
    main()
