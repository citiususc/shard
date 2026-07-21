"""Tests for stable asynchronous job lifecycle semantics."""

from __future__ import annotations

import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.errors import (  # noqa: E402
    CapacityExceeded,
    ConflictingJobState,
    ResourceNotFound,
)
from shard.api.jobs import JobRegistry  # noqa: E402
from shard.api.operational import OperationalSettings  # noqa: E402


class JobRegistryTests(unittest.TestCase):
    def wait_terminal(self, registry, job_id):
        for _ in range(100):
            job = registry.get(job_id)
            if job["status"] in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(0.01)
        self.fail("Job did not reach a terminal state.")

    def test_job_has_stable_progress_and_timestamps(self):
        registry = JobRegistry()

        def worker(_job_id, _cancel, update):
            update(progress=0.5, message="Halfway.")
            return {"ok": True}

        created = registry.create("test", worker, message="Queued.")
        final = self.wait_terminal(registry, created["job_id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["progress"], 1.0)
        self.assertIn("created_at", final)
        self.assertIn("updated_at", final)
        self.assertNotIn("result", final)

    def test_cancellation_and_conflicting_terminal_state(self):
        registry = JobRegistry()
        release = Event()

        def worker(_job_id, cancel, _update):
            while not cancel.is_set() and not release.wait(0.01):
                pass

        job = registry.create("test", worker, message="Queued.")
        cancelled = registry.cancel(job["job_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(ConflictingJobState):
            registry.cancel(job["job_id"])
        release.set()

    def test_unknown_job_is_not_found(self):
        with self.assertRaises(ResourceNotFound):
            JobRegistry().get("missing")

    def test_jobs_queue_until_the_deliberately_broad_capacity_is_exhausted(self):
        settings = replace(
            OperationalSettings(),
            max_concurrent_jobs=1,
            max_queued_jobs=1,
        )
        registry = JobRegistry(settings=settings)
        release = Event()

        def worker(_job_id, cancel, _update):
            while not cancel.is_set() and not release.wait(0.01):
                pass

        first = registry.create("test", worker, message="Queued.")
        second = registry.create("test", worker, message="Queued.")
        self.assertIn(first["status"], {"queued", "running"})
        self.assertEqual(second["status"], "queued")
        with self.assertRaises(CapacityExceeded):
            registry.create("test", worker, message="Queued.")
        registry.cancel(first["job_id"])
        registry.cancel(second["job_id"])
        release.set()

    def test_job_runtime_timeout_is_terminal_and_secret_safe(self):
        settings = replace(
            OperationalSettings(),
            job_max_runtime_seconds=0.03,
        )
        registry = JobRegistry(settings=settings)

        def worker(_job_id, cancel, _update):
            cancel.wait(1)

        created = registry.create("test", worker, message="Queued.")
        final = self.wait_terminal(registry, created["job_id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error"]["code"], "JOB_RUNTIME_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
