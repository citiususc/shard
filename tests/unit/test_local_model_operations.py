"""Transport tests for local model cache and download operations."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.operations import dispatch_post_operation  # noqa: E402


class _Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}
        self.response_provenance = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


class LocalModelOperationTests(unittest.TestCase):
    def test_status_operation_returns_cache_state_as_json(self):
        handler = _Handler()
        result = {
            "model": "example/tiny-model",
            "downloaded": False,
            "status": "not-downloaded",
        }
        with patch.dict(os.environ, {"SHARD_DEPLOYMENT_PROFILE": "local"}), patch(
            "shard.api.operations.local_model_status",
            return_value=result,
        ) as status:
            dispatch_post_operation(
                handler,
                "models.local.status",
                {"model": "example/tiny-model"},
                "local-status-test",
            )

        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.status, 200)
        self.assertEqual(payload["status"], "not-downloaded")
        self.assertEqual(payload["request_id"], "local-status-test")
        status.assert_called_once_with("example/tiny-model")

    def test_public_profile_rejects_local_model_operations(self):
        for operation in ("models.local.status", "models.local.download.create"):
            handler = _Handler()
            with self.subTest(operation=operation), patch.dict(
                os.environ,
                {"SHARD_DEPLOYMENT_PROFILE": "public"},
            ):
                dispatch_post_operation(
                    handler,
                    operation,
                    {"model_id": "example/tiny-model"},
                    "public-policy-test",
                )
            payload = json.loads(handler.wfile.getvalue())
            self.assertEqual(handler.status, 403)
            self.assertEqual(payload["code"], "LOCAL_MODELS_DISABLED")


if __name__ == "__main__":
    unittest.main()
