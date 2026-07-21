"""Tests for named JSON Server-Sent Events."""

from __future__ import annotations

import io
import json
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.sse import SseWriter  # noqa: E402


class _Handler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.response_provenance = None
        self.response_operation_metadata = {
            "request_id": "sse-request",
            "operation": "batches.generate",
            "service": "authoring-workflow",
            "api_version": "v1",
            "deployment_profile": "local",
            "created_at": "2026-01-01T00:00:00Z",
            "duration_ms": 0.0,
            "warnings": [],
        }


def _frames(raw):
    frames = []
    for block in raw.decode("utf-8").strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


class SseContractTests(unittest.TestCase):
    def test_events_are_named_json_sequenced_and_terminal(self):
        handler = _Handler()
        writer = SseWriter(handler, "sse-request", heartbeat_seconds=0)
        self.assertTrue(writer.send("started", {
            "message": "Started.", "total_items": 2,
        }))
        self.assertTrue(writer.send("progress", {
            "message": "Halfway.", "completed_items": 1,
            "total_items": 2, "progress": 0.5,
        }))
        self.assertTrue(writer.send("completed", {
            "message": "Done.", "completed_items": 2, "total_items": 2,
        }))
        self.assertFalse(writer.send("progress", {
            "message": "Late.", "completed_items": 2,
            "total_items": 2, "progress": 1.0,
        }))

        frames = _frames(handler.wfile.getvalue())
        self.assertEqual([name for name, _ in frames], ["started", "progress", "completed"])
        self.assertEqual([data["sequence"] for _, data in frames], [1, 2, 3])
        self.assertTrue(all(data["request_id"] == "sse-request" for _, data in frames))
        self.assertEqual(frames[1][1]["completed_items"], 1)
        self.assertEqual(frames[1][1]["total_items"], 2)

    def test_shape_event_exposes_semantic_review_status(self):
        handler = _Handler()
        writer = SseWriter(handler, "sse-request", heartbeat_seconds=0)
        self.assertTrue(writer.send("shape_generated", {
            "rule": {
                "number": "BR-001",
                "title": "Book title",
                "text": "Every Book must have exactly one title.",
            },
            "target": {"iri": "ex:Book"},
            "target_index": 1,
            "target_total": 1,
            "shape_document": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
            "valid": True,
            "attempts": 1,
            "llm_review_applied": True,
            "review_attempts": 2,
            "semantic_review": {
                "status": "passed",
                "critic_calls": 2,
                "correction_count": 1,
                "issues_found": 1,
                "issues": [{
                    "code": "MISSING_CLASS_CONSTRAINT",
                    "message": "The object range class was restored.",
                    "path": "ex:title",
                }],
            },
        }))

        frames = _frames(handler.wfile.getvalue())
        self.assertEqual(frames[0][0], "shape_generated")
        self.assertTrue(frames[0][1]["llm_review_applied"])
        self.assertEqual(frames[0][1]["review_attempts"], 2)
        self.assertEqual(frames[0][1]["semantic_review"]["status"], "passed")

    def test_heartbeat_is_emitted_while_stream_is_idle(self):
        handler = _Handler()
        writer = SseWriter(handler, "sse-request", heartbeat_seconds=0.01)
        writer.start()
        time.sleep(0.035)
        writer.close()
        frames = _frames(handler.wfile.getvalue())
        self.assertGreaterEqual(len(frames), 1)
        self.assertTrue(all(name == "heartbeat" for name, _ in frames))

    def test_idle_timeout_emits_a_terminal_failed_event(self):
        handler = _Handler()
        writer = SseWriter(
            handler,
            "sse-request",
            heartbeat_seconds=0.01,
            idle_timeout_seconds=0.025,
        )
        writer.start()
        time.sleep(0.05)
        frames = _frames(handler.wfile.getvalue())
        self.assertEqual(frames[-1][0], "failed")
        self.assertEqual(frames[-1][1]["error"]["code"], "SSE_IDLE_TIMEOUT")
        self.assertTrue(writer.closed.is_set())


if __name__ == "__main__":
    unittest.main()
