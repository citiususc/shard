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
    def test_no_shard_client_authentication_implementation_remains(self):
        roots = [ROOT / "src", ROOT / "frontend", ROOT / "docs", ROOT / "examples"]
        forbidden = (
            "HTTPBearer",
            "OAuth2PasswordBearer",
            "APIKeyHeader",
            "SHARD_API_TOKEN",
            "SHARD_AUTH_ENABLED",
        )
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".js", ".html", ".md"}:
                    continue
                content = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    with self.subTest(path=path, marker=marker):
                        self.assertNotIn(marker, content)

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
            set(schemas["BatchWorkflowRequest"]["required"]),
            {"ontology", "batch"},
        )
        resolver = schemas["ResolverOptions"]["properties"]
        self.assertEqual(resolver["semantic_threshold"]["default"], 0.60)
        self.assertEqual(resolver["semantic_target_margin"]["default"], 0.16)
        self.assertEqual(resolver["semantic_max_targets"]["default"], 4)
        validation = schemas["BatchWorkflowRequest"]["properties"]["validation"]
        self.assertEqual(validation["$ref"], "#/components/schemas/ValidationOptions")
        self.assertIn("astrea", schemas["BatchWorkflowRequest"]["properties"])

    def test_tags_reflect_logical_service_ownership(self):
        document = openapi_document()
        expected_tags = {
            "/api/v1/workflows/rule-to-shape": "Authoring Workflow Service",
            "/api/v1/workflows/batch-to-shapes": "Authoring Workflow Service",
            "/api/v1/batches/generate": "Authoring Workflow Service",
            "/api/v1/rules/resolve-targets": "Data Constraint Grounding Service",
            "/api/v1/shapes/build": "Shape Generation Service",
            "/api/v1/shapes/validate": (
                "Shape Assurance and Baseline Integration Service"
            ),
        }
        for path, expected_tag in expected_tags.items():
            with self.subTest(path=path):
                self.assertEqual(document["paths"][path]["post"]["tags"], [expected_tag])

    def test_document_contains_no_runtime_credentials(self):
        document = openapi_document()
        serialized = json.dumps(document).lower()
        self.assertNotIn("dapi", serialized)
        self.assertNotIn("securitySchemes", document["components"])
        self.assertNotIn("shard-api-token", serialized)
        self.assertFalse(any(
            "security" in operation
            for path_item in document["paths"].values()
            for operation in path_item.values()
            if isinstance(operation, dict)
        ))

    def test_removed_versioned_aliases_are_not_published(self):
        paths = openapi_document()["paths"]
        for removed_path in (
            "/api/v1/workflows/batch-to-rules",
            "/api/v1/ontology/index",
            "/api/v1/ontology/index/status",
            "/api/v1/ontology/index/cancel",
            "/api/v1/models/local/download",
        ):
            with self.subTest(path=removed_path):
                self.assertNotIn(removed_path, paths)

    def test_provider_credential_401_is_documented_only_for_model_check(self):
        document = openapi_document()
        operations_with_401 = {
            operation["x-shard-operation"]
            for path_item in document["paths"].values()
            for operation in path_item.values()
            if isinstance(operation, dict) and "401" in operation.get("responses", {})
        }
        self.assertEqual(operations_with_401, {"models.check"})

    def test_no_versioned_operations_are_deprecated(self):
        document = openapi_document()
        deprecated = {
            path
            for path, path_item in document["paths"].items()
            for operation in path_item.values()
            if isinstance(operation, dict) and operation.get("deprecated")
        }
        self.assertEqual(deprecated, set())

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
