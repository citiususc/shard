"""Unit tests for complete developer-facing SHARD workflows."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.application.workflows import (  # noqa: E402
    generate_batch_workflow,
    generate_rule_workflow,
    normalize_workflow_payload,
)
from shard.integrations.astrea import AstreaUnavailableError  # noqa: E402


class WorkflowPayloadTests(unittest.TestCase):
    def test_nested_contract_maps_to_existing_application_fields(self):
        payload = normalize_workflow_payload({
            "ontology": {"filename": "domain.ttl", "content": "ontology"},
            "rule": {"number": "BR-007", "title": "A title", "text": "A rule"},
            "batch": {"filename": "rules.md", "content": "batch"},
            "inference": {
                "provider": "databricks",
                "generation_model": "generation-model",
                "embedding_model": "embedding-model",
                "temperature": 0.2,
                "databricks": {"base_url": "https://example.test/mlflow/v1", "token": "secret"},
            },
            "generation": {
                "domain_context": "context",
                "guidance": "guidance",
                "shape_prefix": "shape",
            },
            "resolver": {
                "semantic_threshold": 0.61,
                "semantic_target_margin": 0.12,
                "semantic_max_targets": 3,
                "llm_fallback": False,
            },
            "validation_profiles": [{"name": "profile.ttl", "content": "profile"}],
            "astrea": {
                "mode": "both",
                "merge_technique": "restrictive",
                "failure_policy": "fail",
            },
        })

        self.assertEqual(payload["ontology_filename"], "domain.ttl")
        self.assertEqual(payload["business_rule"], "A rule")
        self.assertEqual(payload["batch_content"], "batch")
        self.assertEqual(payload["llm_model"], "generation-model")
        self.assertEqual(payload["embedding_model"], "embedding-model")
        self.assertEqual(payload["inference_config"]["databricks"]["token"], "secret")
        self.assertEqual(payload["semantic_threshold"], 0.61)
        self.assertEqual(payload["semantic_target_margin"], 0.12)
        self.assertEqual(payload["semantic_max_targets"], 3)
        self.assertFalse(payload["resolver_llm_fallback"])
        self.assertEqual(payload["astrea_use_mode"], "both")
        self.assertEqual(payload["astrea_merge_technique"], "restrictive")

    def test_nested_values_override_equivalent_flat_values(self):
        payload = normalize_workflow_payload({
            "ontology_content": "old",
            "provider": "huggingface",
            "ontology": {"content": "new"},
            "inference": {"provider": "databricks"},
        })
        self.assertEqual(payload["ontology_content"], "new")
        self.assertEqual(payload["provider"], "databricks")


class CompleteWorkflowTests(unittest.TestCase):
    def test_batch_workflow_without_astrea_returns_generated_document(self):
        def generator(payload, event_callback=None):
            self.assertEqual(payload["ontology_content"], "ontology")
            self.assertEqual(payload["batch_content"], "batch")
            self.assertEqual(payload["astrea_use_mode"], "none")
            return {
                "shape_document": "generated ttl",
                "summary": {"rules_total": 1, "valid": 1, "invalid": 0},
            }

        baseline_generator = Mock(side_effect=AssertionError("Astrea must not be called"))
        merger = Mock(side_effect=AssertionError("Merge must not be called"))
        result = generate_batch_workflow(
            {
                "ontology": {"content": "ontology"},
                "batch": {"content": "batch"},
                "astrea": {"mode": "none"},
            },
            generator=generator,
            baseline_generator=baseline_generator,
            merger=merger,
        )

        self.assertEqual(result["workflow"], "batch-to-shapes")
        self.assertEqual(result["final_shape_document"], "generated ttl")
        self.assertIsNone(result["merge"])
        baseline_generator.assert_not_called()
        merger.assert_not_called()

    def test_both_mode_generates_baseline_uses_evidence_and_merges(self):
        baseline_generator = Mock(return_value={
            "source": "astrea-api",
            "name": "domain_astrea.ttl",
            "shape_document": "baseline ttl",
            "shape_count": 2,
        })

        def generator(payload, event_callback=None):
            self.assertEqual(payload["astrea_use_mode"], "both")
            self.assertEqual(payload["astrea_baseline"]["content"], "baseline ttl")
            return {"shape_document": "generated ttl", "summary": {"valid": 1}}

        def merger(payload):
            self.assertEqual(payload["generated_shapes"], "generated ttl")
            self.assertEqual(payload["technique"], "restrictive")
            self.assertEqual(payload["astrea_baseline"]["content"], "baseline ttl")
            return {"shape_document": "merged ttl", "valid": True}

        result = generate_batch_workflow(
            {
                "ontology": {"content": "ontology"},
                "batch": {"content": "batch"},
                "astrea": {"mode": "both", "merge_technique": "restrictive"},
            },
            generator=generator,
            baseline_generator=baseline_generator,
            merger=merger,
        )

        self.assertTrue(result["astrea"]["available"])
        self.assertEqual(result["astrea"]["effective_mode"], "evidence-and-merge")
        self.assertEqual(result["final_shape_document"], "merged ttl")

    def test_astrea_unavailability_continues_without_baseline_by_default(self):
        baseline_generator = Mock(side_effect=AstreaUnavailableError("service offline"))
        merger = Mock(side_effect=AssertionError("Merge must not be called"))

        def generator(payload, event_callback=None):
            self.assertEqual(payload["astrea_use_mode"], "none")
            return {"shape_document": "generated ttl", "summary": {"valid": 1}}

        result = generate_batch_workflow(
            {
                "ontology": {"content": "ontology"},
                "batch": {"content": "batch"},
                "astrea": {"mode": "merge"},
            },
            generator=generator,
            baseline_generator=baseline_generator,
            merger=merger,
        )

        self.assertFalse(result["astrea"]["available"])
        self.assertEqual(result["astrea"]["effective_mode"], "none")
        self.assertEqual(result["astrea"]["error_type"], "astrea_unavailable")
        self.assertEqual(result["final_shape_document"], "generated ttl")

    def test_astrea_fail_policy_propagates_unavailability(self):
        with self.assertRaises(AstreaUnavailableError):
            generate_batch_workflow(
                {
                    "ontology": {"content": "ontology"},
                    "batch": {"content": "batch"},
                    "astrea": {"mode": "baseline", "failure_policy": "fail"},
                },
                generator=Mock(),
                baseline_generator=Mock(
                    side_effect=AstreaUnavailableError("service offline")
                ),
            )

    def test_single_rule_uses_the_shared_batch_pipeline_and_escapes_html(self):
        def generator(payload, event_callback=None):
            self.assertIn("&lt;Asset&gt;", payload["batch_content"])
            self.assertNotIn("<Asset>", payload["batch_content"])
            return {
                "prefixes": "@prefix ex: <urn:example:> .",
                "base_namespace": "urn:example:",
                "shape_namespace": "urn:example:shapes:",
                "shape_prefix": "shape",
                "rules": [{
                    "rule_number": "BR-001",
                    "title": "Asset rule",
                    "text": "Every <Asset> must be identified.",
                    "resolution": {"resolved_by": "label", "targets": ["ex:Asset"]},
                    "generated": [{"shape": "shape body"}],
                }],
                "shapes": [{"shape": "shape body", "valid": True}],
                "unresolved_rules": [],
                "shape_document": "shape document",
                "summary": {"rules_total": 1, "valid": 1, "invalid": 0},
            }

        result = generate_rule_workflow(
            {
                "ontology": {"content": "ontology"},
                "rule": {
                    "number": "BR-001",
                    "title": "Asset rule",
                    "text": "Every <Asset> must be identified.",
                },
            },
            generator=generator,
            baseline_generator=Mock(),
        )

        self.assertEqual(result["workflow"], "rule-to-shape")
        self.assertEqual(result["rule"]["resolution"]["resolved_by"], "label")
        self.assertNotIn("generated", result["rule"])
        self.assertFalse(result["unresolved"])
        self.assertEqual(result["final_shape_document"], "shape document")

    def test_required_workflow_inputs_have_clear_errors(self):
        with self.assertRaisesRegex(ValueError, "ontology.content"):
            generate_batch_workflow({"batch": {"content": "batch"}}, generator=Mock())
        with self.assertRaisesRegex(ValueError, "rule.text"):
            generate_rule_workflow({"ontology": {"content": "ontology"}}, generator=Mock())


if __name__ == "__main__":
    unittest.main()
