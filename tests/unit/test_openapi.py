"""Tests for the machine-readable SHARD API description."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.contract import ENDPOINTS  # noqa: E402
from shard.api.openapi import openapi_document  # noqa: E402


class OpenApiTests(unittest.TestCase):
    def test_document_covers_every_canonical_endpoint(self):
        document = openapi_document()
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(
            document["jsonSchemaDialect"],
            "https://spec.openapis.org/oas/3.1/dialect/base",
        )
        for endpoint in ENDPOINTS:
            with self.subTest(operation=endpoint.operation):
                operation = document["paths"][endpoint.path][endpoint.method.lower()]
                self.assertEqual(operation["x-shard-operation"], endpoint.operation)

    def test_complete_workflow_schemas_expose_required_inputs(self):
        schemas = openapi_document()["components"]["schemas"]
        self.assertEqual(
            set(schemas["RuleWorkflowRequest"]["required"]),
            {"ontology", "rule"},
        )
        self.assertEqual(
            set(schemas["GuideWorkflowRequest"]["required"]),
            {"ontology", "guide"},
        )
        resolver = schemas["ResolverOptions"]["properties"]
        self.assertEqual(resolver["semantic_threshold"]["default"], 0.60)
        self.assertEqual(resolver["semantic_target_margin"]["default"], 0.16)
        self.assertEqual(resolver["semantic_max_targets"]["default"], 4)
        self.assertIn("validation_profiles", schemas["GuideWorkflowRequest"]["properties"])
        self.assertIn("astrea", schemas["GuideWorkflowRequest"]["properties"])

    def test_tags_reflect_logical_service_ownership(self):
        document = openapi_document()
        expected_tags = {
            "/api/v1/workflows/rule-to-shape": "Authoring Workflow Service",
            "/api/v1/workflows/guide-to-shapes": "Authoring Workflow Service",
            "/api/v1/guides/generate": "Authoring Workflow Service",
            "/api/v1/rules/resolve-targets": "Business Rule Grounding Service",
            "/api/v1/shapes/build": "Shape Generation Service",
            "/api/v1/shapes/validate": (
                "Shape Assurance and Baseline Integration Service"
            ),
        }
        for path, expected_tag in expected_tags.items():
            with self.subTest(path=path):
                self.assertEqual(document["paths"][path]["post"]["tags"], [expected_tag])

    def test_document_contains_no_runtime_credentials(self):
        serialized = json.dumps(openapi_document()).lower()
        self.assertNotIn("dapi", serialized)
        self.assertNotIn("bearer ", serialized)

    def test_swagger_documentation_route_is_declared_as_html(self):
        response_content = (
            openapi_document()["paths"]["/api/v1/docs"]["get"]
            ["responses"]["200"]["content"]
        )
        self.assertEqual(list(response_content), ["text/html"])

    def test_every_post_operation_has_a_named_request_schema(self):
        document = openapi_document()
        for endpoint in ENDPOINTS:
            if endpoint.method != "POST":
                continue
            with self.subTest(operation=endpoint.operation):
                schema = (
                    document["paths"][endpoint.path]["post"]
                    ["requestBody"]["content"]["application/json"]["schema"]
                )
                self.assertNotEqual(
                    schema.get("$ref"),
                    "#/components/schemas/FreeFormRequest",
                )


if __name__ == "__main__":
    unittest.main()
