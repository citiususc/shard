"""Tests for the broad but enforceable SHARD operational safeguards."""

from __future__ import annotations

from dataclasses import replace
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.errors import CapacityExceeded, PayloadTooLarge, RateLimited  # noqa: E402
from shard.api.operational import (  # noqa: E402
    InMemoryRateLimiter,
    OperationConcurrencyLimiter,
    OperationalSettings,
    allowed_cors_origin,
    operational_settings,
    request_client_id,
    validate_operation_payload_size,
)


class _Handler:
    def __init__(self, *, peer="127.0.0.1", headers=None):
        self.client_address = (peer, 12345)
        self.headers = headers or {}


class OperationalSafeguardTests(unittest.TestCase):
    def test_demo_defaults_are_deliberately_broad(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = operational_settings()
        self.assertEqual(settings.rate_limit_requests_per_minute, 1000)
        self.assertEqual(settings.rate_limit_burst, 200)
        self.assertEqual(settings.rate_limit_expensive_requests_per_minute, 120)
        self.assertEqual(settings.rate_limit_job_creations_per_minute, 60)
        self.assertEqual(settings.model_timeout_seconds, 1800)
        self.assertEqual(settings.embedding_timeout_seconds, 3600)
        self.assertEqual(settings.batch_workflow_timeout_seconds, 7200)
        self.assertEqual(settings.max_ontology_upload_mb, 200)
        self.assertEqual(settings.max_batch_upload_mb, 50)
        self.assertEqual(settings.max_concurrent_jobs, 50)
        self.assertEqual(settings.max_queued_jobs, 500)

    def test_limits_are_configurable_from_the_environment(self):
        with patch.dict(os.environ, {
            "RATE_LIMIT_REQUESTS_PER_MINUTE": "1234",
            "MODEL_TIMEOUT_SECONDS": "2345",
            "MAX_ONTOLOGY_UPLOAD_MB": "210",
            "MAX_CONCURRENT_JOBS": "60",
            "SHARD_CORS_ALLOWED_ORIGINS": "https://one.example,https://two.example",
            "SHARD_TRUSTED_PROXY_IPS": "10.0.0.1",
        }, clear=True):
            settings = operational_settings()
        self.assertEqual(settings.rate_limit_requests_per_minute, 1234)
        self.assertEqual(settings.model_timeout_seconds, 2345)
        self.assertEqual(settings.max_ontology_upload_mb, 210)
        self.assertEqual(settings.max_concurrent_jobs, 60)
        self.assertEqual(settings.cors_allowed_origins, (
            "https://one.example", "https://two.example"
        ))
        self.assertEqual(settings.trusted_proxy_ips, ("10.0.0.1",))

    def test_normal_use_does_not_hit_the_rate_limiter(self):
        limiter = InMemoryRateLimiter()
        settings = OperationalSettings()
        for offset in range(100):
            limiter.check(
                "client-normal",
                "ontology.parse",
                settings=settings,
                now=10.0 + offset / 1000.0,
            )

    def test_artificial_rate_excess_is_rejected(self):
        limiter = InMemoryRateLimiter()
        settings = replace(
            OperationalSettings(),
            rate_limit_requests_per_minute=2,
            rate_limit_burst=2,
        )
        limiter.check("client", "ontology.parse", settings=settings, now=10.0)
        limiter.check("client", "ontology.parse", settings=settings, now=10.1)
        with self.assertRaises(RateLimited) as raised:
            limiter.check("client", "ontology.parse", settings=settings, now=10.2)
        self.assertIn("Retry-After", raised.exception.headers)

    def test_resource_specific_payload_limits_are_enforced(self):
        settings = replace(OperationalSettings(), max_ontology_upload_mb=1)
        validate_operation_payload_size(
            "ontology.parse",
            {"ontology": {"content": "x" * (1024 * 1024)}},
            settings=settings,
        )
        with self.assertRaises(PayloadTooLarge):
            validate_operation_payload_size(
                "ontology.parse",
                {"ontology": {"content": "x" * (1024 * 1024 + 1)}},
                settings=settings,
            )

    def test_batch_concurrency_only_rejects_after_configured_saturation(self):
        limiter = OperationConcurrencyLimiter()
        settings = replace(OperationalSettings(), max_concurrent_batch_workflows=1)
        with limiter.slot("workflows.batch.generate", settings=settings):
            with self.assertRaises(CapacityExceeded):
                with limiter.slot("workflows.batch.generate", settings=settings):
                    pass
        with limiter.slot("workflows.batch.generate", settings=settings):
            pass

    def test_forwarded_ip_is_used_only_for_an_explicitly_trusted_proxy(self):
        headers = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
        trusted = replace(OperationalSettings(), trusted_proxy_ips=("10.0.0.1",))
        self.assertEqual(
            request_client_id(_Handler(peer="10.0.0.1", headers=headers), trusted),
            "203.0.113.7",
        )
        self.assertEqual(
            request_client_id(_Handler(peer="10.0.0.2", headers=headers), trusted),
            "10.0.0.2",
        )

    def test_cors_accepts_configured_and_same_origins_only(self):
        settings = replace(
            OperationalSettings(), cors_allowed_origins=("https://demo.example",)
        )
        self.assertEqual(
            allowed_cors_origin(
                _Handler(headers={"Origin": "https://demo.example"}), settings
            ),
            "https://demo.example",
        )
        self.assertEqual(
            allowed_cors_origin(
                _Handler(headers={
                    "Origin": "http://127.0.0.1:8768",
                    "Host": "127.0.0.1:8768",
                }),
                settings,
            ),
            "http://127.0.0.1:8768",
        )
        self.assertIsNone(
            allowed_cors_origin(
                _Handler(headers={"Origin": "https://untrusted.example"}), settings
            )
        )


if __name__ == "__main__":
    unittest.main()
