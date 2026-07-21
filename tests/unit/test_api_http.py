"""Unit tests for low-level API response transport behavior."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.http import send_json  # noqa: E402


class _DisconnectedWriter:
    def write(self, _body):
        raise BrokenPipeError("client disconnected")


class _Handler:
    api_is_canonical = False
    api_endpoint = None
    request_secrets = ()
    response_provenance = None

    def __init__(self):
        self.wfile = _DisconnectedWriter()

    def send_response(self, _status):
        pass

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


class ApiHttpTests(unittest.TestCase):
    def test_client_disconnect_during_response_write_is_not_an_api_failure(self):
        send_json(_Handler(), 200, {"status": "ok"}, request_id="req-disconnected")


if __name__ == "__main__":
    unittest.main()
