"""Tests for local/public inference-provider deployment policy."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "text2shacl_core"))

from deployment_policy import (  # noqa: E402
    DEPLOYMENT_PROFILE_ENV,
    HUGGINGFACE_PUBLIC_MESSAGE,
    ProviderDisabledError,
    capabilities,
    ensure_request_provider_enabled,
    requested_provider,
)


class DeploymentPolicyTests(unittest.TestCase):
    def test_local_profile_enables_huggingface(self):
        document = capabilities("local")
        self.assertTrue(document["providers"]["huggingface"]["enabled"])

    def test_public_profile_disables_huggingface(self):
        document = capabilities("public")
        hf = document["providers"]["huggingface"]
        self.assertFalse(hf["enabled"])
        self.assertEqual(hf["message"], HUGGINGFACE_PUBLIC_MESSAGE)

    def test_nested_provider_is_rejected_in_public_profile(self):
        payload = {"inference_config": {"provider": "huggingface"}}
        with patch.dict(os.environ, {DEPLOYMENT_PROFILE_ENV: "public"}):
            with self.assertRaises(ProviderDisabledError) as raised:
                ensure_request_provider_enabled(payload)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "provider_disabled")

    def test_huggingface_model_id_is_detected_without_provider(self):
        payload = {"embedding_model": "Qwen/Qwen3-Embedding-0.6B"}
        self.assertEqual(requested_provider(payload), "huggingface")
        with patch.dict(os.environ, {DEPLOYMENT_PROFILE_ENV: "public"}):
            with self.assertRaises(ProviderDisabledError):
                ensure_request_provider_enabled(payload)

    def test_public_profile_keeps_databricks_enabled(self):
        payload = {
            "provider": "databricks",
            "model": "gemma-3-12b",
        }
        with patch.dict(os.environ, {DEPLOYMENT_PROFILE_ENV: "public"}):
            ensure_request_provider_enabled(payload)


if __name__ == "__main__":
    unittest.main()
