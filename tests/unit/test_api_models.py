"""Tests for strict public API models and compatibility aliases."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.contract import endpoint_for_operation  # noqa: E402
from shard.api.models import (  # noqa: E402
    AstreaOptions,
    GenerationOptions,
    InferenceOptions,
    ModelCheckRequest,
    OntologyTerm,
    ResolverOptions,
    SemanticReviewResult,
    ShapeMergeRequest,
    TargetResolutionRequest,
    DatabricksCredentials,
    to_application_payload,
)
from shard.api.provenance import request_provenance  # noqa: E402


ONTOLOGY = "@prefix ex: <urn:books:> . ex:Book a <http://www.w3.org/2002/07/owl#Class> ."


class PublicApiModelTests(unittest.TestCase):
    def test_target_resolution_is_a_closed_discriminated_union(self):
        request = TargetResolutionRequest.model_validate({
            "input_type": "rule",
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
            "rule": {"number": "BR-1", "title": "Title", "text": "Every Book has a title."},
        })
        self.assertEqual(request.root.input_type, "rule")
        with self.assertRaises(ValidationError):
            TargetResolutionRequest.model_validate({
                "input_type": "unknown",
                "ontology": {"content": ONTOLOGY},
                "anything": {},
            })

    def test_public_objects_forbid_unexpected_fields(self):
        with self.assertRaises(ValidationError):
            GenerationOptions.model_validate({"generation_guidance": "Use messages.", "surprise": True})

    def test_semantic_llm_review_is_enabled_and_bounded_by_default(self):
        defaults = GenerationOptions.model_validate({})
        self.assertTrue(defaults.llm_review)
        self.assertEqual(defaults.review_max_attempts, 3)

        disabled = GenerationOptions.model_validate({
            "llm_review": False,
            "review_max_attempts": 1,
        })
        self.assertFalse(disabled.llm_review)
        with self.assertRaises(ValidationError):
            GenerationOptions.model_validate({"review_max_attempts": 6})

        review = SemanticReviewResult.model_validate({
            "status": "passed",
            "critic_calls": 2,
            "correction_count": 1,
            "issues_found": 1,
            "issues": [{
                "code": "MISSING_CLASS_CONSTRAINT",
                "message": "Add the ontology-backed sh:class constraint.",
                "path": "ex:hasValue",
            }],
        })
        self.assertEqual(review.status, "passed")
        self.assertEqual(review.critic_calls, 2)
        with self.assertRaises(ValidationError):
            SemanticReviewResult.model_validate({
                "status": "passed",
                "private_reasoning": "not part of the public contract",
            })

    def test_deprecated_input_names_normalize_to_canonical_names(self):
        self.assertEqual(
            InferenceOptions.model_validate({"llm_model": "chat-model"}).generation_model,
            "chat-model",
        )
        self.assertEqual(
            GenerationOptions.model_validate({"guidance": "Use messages."}).generation_guidance,
            "Use messages.",
        )
        self.assertFalse(
            ResolverOptions.model_validate({"resolver_llm_fallback": False}).llm_fallback
        )
        astrea = AstreaOptions.model_validate({"mode": "both", "technique": "priority-llm"})
        self.assertEqual(astrea.mode, "evidence-and-merge")
        self.assertEqual(astrea.merge_strategy, "generated-priority")
        merge = ShapeMergeRequest.model_validate({
            "generated": {"content": "generated"},
            "baseline": {"content": "baseline"},
            "merge_technique": "priority-llm",
        })
        self.assertEqual(merge.merge_strategy, "generated-priority")

    def test_ontology_note_alias_is_input_only(self):
        term = OntologyTerm.model_validate({
            "id": "class-1", "iri": "ex:Book", "full_iri": "urn:books:Book",
            "label": "Book", "type": "class", "kind": "Class",
            "ontologyNote": "A published work.",
        })
        document = term.model_dump(mode="json")
        self.assertEqual(document["ontology_note"], "A published work.")
        self.assertNotIn("ontologyNote", document)

    def test_provenance_never_contains_credentials_or_provider_urls(self):
        secret = "dapi-secret-contract-test"
        payload = {
            "rule": {"text": "Every Book has one title."},
            "inference": {
                "provider": "databricks",
                "generation_model": "chat-model",
                "databricks": {"base_url": "https://private.example/api", "token": secret},
            },
        }
        provenance = request_provenance(
            endpoint_for_operation("workflows.rule.generate"), payload, "request-1"
        )
        serialized = json.dumps(provenance)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("private.example", serialized)
        self.assertEqual(provenance["generation_model"], "chat-model")

    def test_model_check_uses_request_credentials_without_replacing_checked_model(self):
        request = ModelCheckRequest.model_validate({
            "inference_provider": "databricks",
            "model_id": "embedding-to-check",
            "role": "embedding",
            "inference": {
                "provider": "databricks",
                "generation_model": "different-chat-model",
                "databricks": {
                    "base_url": "https://workspace.example/api",
                    "token": "dapi-secret",
                },
            },
        }).model_dump(mode="python", exclude_none=True)
        application = to_application_payload("models.check", request)

        self.assertEqual(application["model"], "embedding-to-check")
        self.assertEqual(
            application["inference_config"]["databricks"]["token"],
            "dapi-secret",
        )

    def test_credential_repr_masks_secret_values(self):
        credentials = DatabricksCredentials.model_validate({
            "base_url": "https://workspace.example/api",
            "token": "dapi-repr-secret",
        })
        self.assertNotIn("dapi-repr-secret", repr(credentials))
        self.assertIn("**********", repr(credentials))


if __name__ == "__main__":
    unittest.main()
