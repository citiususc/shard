"""Negative contract tests that prevent weak public OpenAPI schemas."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.models import REQUEST_MODELS, RESPONSE_MODELS  # noqa: E402
from shard.api.models import ApiError, SseEvent  # noqa: E402
from shard.api.openapi import _response_example, openapi_document, request_example  # noqa: E402
from shard.domain.limits import MAX_TOP_K  # noqa: E402


class StrictOpenApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = openapi_document()

    def test_openapi_is_valid_with_the_standard_validator(self):
        try:
            from openapi_spec_validator import validate
        except ImportError as exc:  # pragma: no cover - dependency failure is actionable
            self.fail(f"openapi-spec-validator is required for development tests: {exc}")
        validate(self.document)

    def test_public_object_schemas_are_closed(self):
        for name, schema in self.document["components"]["schemas"].items():
            if schema.get("type") != "object" or name == "JsonValue":
                continue
            with self.subTest(schema=name):
                self.assertIs(schema.get("additionalProperties"), False)

    def test_no_empty_any_schema_or_additional_prop_placeholder_exists(self):
        serialized = json.dumps(self.document)
        self.assertNotIn("additionalProp1", serialized)

        def walk(value, path=()):
            if isinstance(value, dict):
                self.assertNotEqual(value, {}, f"Unconstrained schema at {'/'.join(path)}")
                for key, child in value.items():
                    walk(child, path + (str(key),))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, path + (str(index),))

        walk(self.document["components"]["schemas"])

    def test_every_operation_has_metadata_and_typed_success(self):
        operation_ids = []
        for path, path_item in self.document["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "delete", "put", "patch"}:
                    continue
                with self.subTest(path=path, method=method):
                    self.assertTrue(operation.get("summary"))
                    self.assertTrue(operation.get("description"))
                    self.assertTrue(operation.get("operationId"))
                    operation_ids.append(operation["operationId"])
                    for status, response in operation["responses"].items():
                        if not str(status).startswith("2"):
                            continue
                        media = next(iter(response.get("content", {}).values()), None)
                        self.assertIsNotNone(media, f"Missing success content for {method} {path}")
                        self.assertIn("schema", media)
                        self.assertNotEqual(media["schema"], {"type": "object"})
        self.assertEqual(len(operation_ids), len(set(operation_ids)))

    def test_target_resolution_has_only_discriminated_explicit_branches(self):
        schema = self.document["components"]["schemas"]["TargetResolutionRequest"]
        self.assertEqual(schema["discriminator"]["propertyName"], "input_type")
        branches = schema["oneOf"]
        self.assertEqual(len(branches), 2)
        self.assertTrue(all("$ref" in branch for branch in branches))

    def test_books_examples_validate_against_runtime_models(self):
        operations = {
            "ontology.parse", "ontology.search", "rules.resolve-targets",
            "shapes.build", "shapes.validate", "baselines.astrea.generate",
            "shapes.merge", "workflows.rule.generate", "workflows.batch.generate",
        }
        for operation in operations:
            with self.subTest(operation=operation):
                REQUEST_MODELS[operation].model_validate(request_example(operation))
                RESPONSE_MODELS[operation].model_validate(_response_example(operation, 200))

    def test_secret_fields_are_password_strings_and_write_only(self):
        secret_names = {
            "token", "secret", "password", "api_key", "apikey",
            "access_token", "client_secret", "authorization",
        }

        def visit(value, path=()):
            if not isinstance(value, dict):
                return
            properties = value.get("properties") or {}
            for name, schema in properties.items():
                normalized = name.lower().replace("-", "_")
                if (
                    normalized in secret_names
                    or normalized.endswith(("_token", "_secret", "_password", "_api_key"))
                ):
                    with self.subTest(path="/".join((*path, name))):
                        self.assertEqual(schema.get("type"), "string")
                        self.assertEqual(schema.get("format"), "password")
                        self.assertIs(schema.get("writeOnly"), True)
                visit(schema, (*path, name))
            for key in ("oneOf", "anyOf", "allOf"):
                for index, child in enumerate(value.get(key) or []):
                    visit(child, (*path, key, str(index)))

        for name, schema in self.document["components"]["schemas"].items():
            visit(schema, (name,))

    def test_error_examples_are_status_specific_and_model_valid(self):
        examples = {}
        for path_item in self.document["paths"].values():
            for operation in path_item.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                for status, response in operation["responses"].items():
                    if not str(status).startswith(("4", "5")):
                        continue
                    example = response["content"]["application/json"].get("example")
                    if example:
                        ApiError.model_validate(example)
                        examples.setdefault(int(status), example)
        self.assertIn("NOT_FOUND", examples[404]["code"])
        self.assertIn("UNAVAILABLE", examples[503]["code"])
        self.assertEqual(examples[500]["code"], "UNEXPECTED_INTERNAL_ERROR")
        self.assertNotEqual(examples[500]["error"], "request_validation_failed")

    def test_operational_responses_do_not_use_authoring_provenance(self):
        operational = {
            "ApiRootResponse", "HealthResponse", "CapabilitiesResponse",
            "OntologyParseResponse", "OntologySearchResponse", "ModelCheckResponse",
            "JobResponse", "EmbeddingIndexAcceptedResponse", "EmbeddingIndexStatusResponse",
            "LocalModelStatusResponse",
        }
        schemas = self.document["components"]["schemas"]
        for name in operational:
            with self.subTest(schema=name):
                self.assertIn("operation_metadata", schemas[name]["properties"])
                self.assertNotIn("provenance", schemas[name]["properties"])

        provenance = schemas["AuthoringProvenance"]["properties"]
        self.assertNotIn("request_id", provenance)
        self.assertNotIn("operation", provenance)
        self.assertNotIn("service", provenance)
        self.assertNotIn("deployment_profile", provenance)

    def test_top_k_limit_matches_runtime_limit(self):
        schemas = self.document["components"]["schemas"]
        self.assertEqual(
            schemas["ResolverOptions"]["properties"]["top_k"]["maximum"],
            MAX_TOP_K,
        )
        self.assertEqual(
            schemas["OntologySearchRequest"]["properties"]["top_k"]["maximum"],
            MAX_TOP_K,
        )

    def test_sse_is_discriminated_and_requires_event_specific_fields(self):
        schema = self.document["components"]["schemas"]["SseEvent"]
        self.assertEqual(schema["discriminator"]["propertyName"], "event")
        self.assertEqual(len(schema["oneOf"]), 9)
        with self.assertRaises(Exception):
            SseEvent.model_validate({
                "event": "progress",
                "request_id": "req-1",
                "sequence": 1,
                "timestamp": "2026-01-01T00:00:00Z",
            })

        for path_item in self.document["paths"].values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                examples = (
                    operation.get("responses", {}).get("200", {})
                    .get("content", {}).get("text/event-stream", {})
                    .get("x-sse-event-examples", {})
                )
                for example in examples.values():
                    SseEvent.model_validate(example)

    def test_every_json_example_validates_against_its_schema(self):
        from jsonschema import Draft202012Validator, RefResolver

        resolver = RefResolver.from_schema(self.document)
        for path, path_item in self.document["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                request_media = (
                    operation.get("requestBody", {}).get("content", {})
                    .get("application/json", {})
                )
                if "example" in request_media:
                    errors = list(Draft202012Validator(
                        request_media["schema"], resolver=resolver
                    ).iter_errors(request_media["example"]))
                    self.assertFalse(errors, f"Invalid request example for {method} {path}: {errors}")
                for status, response in operation["responses"].items():
                    media = response.get("content", {}).get("application/json", {})
                    if "example" not in media:
                        continue
                    errors = list(Draft202012Validator(
                        media["schema"], resolver=resolver
                    ).iter_errors(media["example"]))
                    self.assertFalse(errors, f"Invalid response example for {method} {path} {status}: {errors}")

    def test_no_deprecated_operations_are_published(self):
        deprecated = []
        for path, path_item in self.document["paths"].items():
            for method, operation in path_item.items():
                if isinstance(operation, dict) and operation.get("deprecated"):
                    deprecated.append((method, path))
        self.assertEqual(deprecated, [])

    def test_examples_use_only_canonical_property_names(self):
        serialized = json.dumps(self.document)
        self.assertNotIn('"semantic threshold"', serialized)


if __name__ == "__main__":
    unittest.main()
