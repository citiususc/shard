"""Tests for explicit local model cache inspection and downloads."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.inference.local_store import (  # noqa: E402
    LocalModelNotDownloaded,
    cached_model_path,
    download_local_model,
    local_model_status,
)


class LocalModelStoreTests(unittest.TestCase):
    def test_cache_check_never_enables_network_access(self):
        with patch(
            "huggingface_hub.snapshot_download",
            side_effect=RuntimeError("not cached"),
        ) as snapshot_download:
            status = local_model_status("example/tiny-model")

        self.assertFalse(status["downloaded"])
        self.assertEqual(status["status"], "not-downloaded")
        self.assertTrue(snapshot_download.call_args.kwargs["local_files_only"])
        self.assertNotIn("dry_run", snapshot_download.call_args.kwargs)

    def test_cached_path_is_returned_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "huggingface_hub.snapshot_download",
            return_value=directory,
        ) as snapshot_download:
            self.assertEqual(cached_model_path("example/tiny-model"), directory)
        self.assertTrue(snapshot_download.call_args.kwargs["local_files_only"])

    def test_missing_cache_has_a_specific_error(self):
        with patch(
            "huggingface_hub.snapshot_download",
            side_effect=RuntimeError("not cached"),
        ):
            with self.assertRaises(LocalModelNotDownloaded):
                cached_model_path("example/tiny-model")

    def test_explicit_download_emits_start_and_done_events(self):
        events = []
        files = [
            SimpleNamespace(will_download=True, file_size=1024),
            SimpleNamespace(will_download=False, file_size=2048),
        ]
        with tempfile.TemporaryDirectory() as directory, patch(
            "shard.inference.local_store.local_model_status",
            return_value={"model": "example/tiny-model", "downloaded": False},
        ), patch(
            "huggingface_hub.snapshot_download",
            side_effect=[files, directory],
        ) as snapshot_download:
            result = download_local_model("example/tiny-model", events.append)

        self.assertTrue(result["downloaded"])
        self.assertEqual([event["type"] for event in events], ["status", "start", "done"])
        self.assertEqual(events[1]["total"], 1)
        self.assertEqual(events[1]["total_bytes"], 1024)
        self.assertTrue(snapshot_download.call_args_list[0].kwargs["dry_run"])
        self.assertIn("tqdm_class", snapshot_download.call_args_list[1].kwargs)

    def test_repository_style_identifier_is_required(self):
        with self.assertRaises(ValueError):
            local_model_status("tiny-model")


if __name__ == "__main__":
    unittest.main()
