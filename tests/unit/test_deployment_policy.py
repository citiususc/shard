"""Tests for local/public inference-provider deployment policy."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.deployment.policy import (  # noqa: E402
    DEPLOYMENT_POLICIES,
    DEPLOYMENT_PROFILE_ENV,
    HUGGINGFACE_PUBLIC_MESSAGE,
    ProviderDisabledError,
    capabilities,
    ensure_request_provider_enabled,
    get_deployment_policy,
    requested_provider,
)
from shard.inference.context import (  # noqa: E402
    get_databricks_base_url,
    get_databricks_token,
    inference_config,
)


class DeploymentPolicyTests(unittest.TestCase):
    def test_profiles_are_declared_in_one_policy_table(self):
        self.assertEqual(set(DEPLOYMENT_POLICIES), {"local", "public"})
        self.assertEqual(get_deployment_policy("local").audience, "developer")
        self.assertEqual(get_deployment_policy("public").audience, "hosted-demo")

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

    def test_capability_documents_never_contain_credentials(self):
        for profile in DEPLOYMENT_POLICIES:
            serialized = str(capabilities(profile)).lower()
            self.assertNotIn("token", serialized)
            self.assertNotIn("base_url", serialized)

    def test_remote_credentials_fall_back_to_deployment_environment(self):
        environment = {
            "DATABRICKS_BASE_URL": "https://deployment.example/v1",
            "DATABRICKS_TOKEN": "deployment-token",
        }
        with patch.dict(os.environ, environment, clear=False):
            with inference_config({"provider": "databricks"}):
                self.assertEqual(get_databricks_base_url(), environment["DATABRICKS_BASE_URL"])
                self.assertEqual(get_databricks_token(), environment["DATABRICKS_TOKEN"])

    def test_request_credentials_override_deployment_environment(self):
        environment = {
            "DATABRICKS_BASE_URL": "https://deployment.example/v1",
            "DATABRICKS_TOKEN": "deployment-token",
        }
        request_config = {
            "provider": "databricks",
            "databricks": {
                "base_url": "https://request.example/v1",
                "token": "request-token",
            },
        }
        with patch.dict(os.environ, environment, clear=False):
            with inference_config(request_config):
                self.assertEqual(get_databricks_base_url(), "https://request.example/v1")
                self.assertEqual(get_databricks_token(), "request-token")


if __name__ == "__main__":
    unittest.main()
