"""Tests for automatic SHARD environment-file loading."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.cli import load_environment


class CliEnvironmentTests(unittest.TestCase):
    def test_environment_file_loads_without_overriding_process_values(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "SHARD_PORT=9999\nDATABRICKS_TOKEN=file-token\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"SHARD_PORT": "8768"},
                clear=False,
            ):
                os.environ.pop("DATABRICKS_TOKEN", None)
                loaded = load_environment(env_file)
                self.assertEqual(loaded, env_file.resolve())
                self.assertEqual(os.environ["SHARD_PORT"], "8768")
                self.assertEqual(os.environ["DATABRICKS_TOKEN"], "file-token")

    def test_missing_environment_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.env"
            self.assertIsNone(load_environment(missing))


if __name__ == "__main__":
    unittest.main()
