"""Contract tests for the published SHARD service catalog."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.contract import (  # noqa: E402
    API_PREFIX,
    AUXILIARY_ROLE,
    ENDPOINTS,
    LOGICAL_SERVICES,
    PRIMARY_ROLE,
    endpoint_for_operation,
    endpoint_for_route,
    frontend_endpoint_map,
    normalize_service_layout,
)


class ApiContractTests(unittest.TestCase):
    def test_catalog_exposes_five_logical_services(self):
        self.assertEqual(len(LOGICAL_SERVICES), 5)
        self.assertEqual(
            {service.service_id for service in LOGICAL_SERVICES},
            {
                "ontology",
                "rule-grounding",
                "shape-generation",
                "shape-assurance",
                "authoring-workflow",
            },
        )

    def test_workflow_and_component_operations_have_clear_ownership(self):
        self.assertEqual(
            endpoint_for_operation("workflows.rule.generate").service_id,
            "authoring-workflow",
        )
        self.assertEqual(
            endpoint_for_operation("workflows.batch.generate").service_id,
            "authoring-workflow",
        )
        self.assertEqual(
            endpoint_for_operation("batches.generate").service_id,
            "authoring-workflow",
        )
        self.assertEqual(
            endpoint_for_operation("rules.resolve-targets").service_id,
            "rule-grounding",
        )
        self.assertEqual(
            endpoint_for_operation("shapes.build").service_id,
            "shape-generation",
        )

    def test_routes_and_operation_ids_are_unique(self):
        operations = [endpoint.operation for endpoint in ENDPOINTS]
        canonical_routes = [(endpoint.method, endpoint.path) for endpoint in ENDPOINTS]
        legacy_routes = [
            (endpoint.method, endpoint.legacy_path)
            for endpoint in ENDPOINTS
            if endpoint.legacy_path
        ]
        self.assertEqual(len(operations), len(set(operations)))
        self.assertEqual(len(canonical_routes), len(set(canonical_routes)))
        self.assertEqual(len(legacy_routes), len(set(legacy_routes)))
        self.assertTrue(all(endpoint.path.startswith(API_PREFIX) for endpoint in ENDPOINTS))

    def test_canonical_and_legacy_routes_resolve_to_the_same_operation(self):
        for endpoint in ENDPOINTS:
            with self.subTest(operation=endpoint.operation):
                self.assertIs(endpoint_for_operation(endpoint.operation), endpoint)
                self.assertIs(endpoint_for_route(endpoint.method, endpoint.path), endpoint)
                if endpoint.legacy_path:
                    self.assertIs(
                        endpoint_for_route(endpoint.method, endpoint.legacy_path), endpoint
                    )

    def test_operational_helpers_are_not_presented_as_standalone_services(self):
        auxiliary = {
            endpoint.operation
            for endpoint in ENDPOINTS
            if endpoint.role == AUXILIARY_ROLE
        }
        self.assertEqual(
            auxiliary,
            {
                "ontology.index.create",
                "ontology.index.get",
                "ontology.index.delete",
                "models.check",
                "models.local.status",
                "models.local.download.create",
                "models.local.download.get",
                "models.local.download.delete",
            },
        )
        primary_services = {
            endpoint.service_id
            for endpoint in ENDPOINTS
            if endpoint.role == PRIMARY_ROLE
        }
        self.assertEqual(primary_services, {service.service_id for service in LOGICAL_SERVICES})

    def test_frontend_endpoint_map_supports_unified_and_split_layouts(self):
        unified = frontend_endpoint_map("unified")
        split = frontend_endpoint_map("split")
        self.assertTrue(all(
            not url or url.startswith(API_PREFIX) for url in unified.values()
        ))
        self.assertNotIn("term_status", unified)
        self.assertNotIn("cancel_terms", unified)
        self.assertNotIn("terms", unified)
        self.assertNotIn("terms", split)
        self.assertEqual(unified["prepare_terms"], "/api/v1/ontology/indexes")
        self.assertEqual(
            unified["download_local_model"],
            "/api/v1/models/local/downloads",
        )
        self.assertEqual(split["capabilities"], f"{API_PREFIX}/capabilities")
        self.assertEqual(split["parse"], "http://127.0.0.1:9100/parse-ontology")
        self.assertEqual(
            split["astrea"],
            "http://127.0.0.1:9102/generate-astrea-baseline",
        )
        self.assertEqual(split["batch"], "http://127.0.0.1:9103/generate-from-batch")
        self.assertEqual(
            split["local_model_status"],
            "http://127.0.0.1:9102/local-model-status",
        )
        self.assertEqual(split["prepare_terms"], "/api/v1/ontology/indexes")
        self.assertEqual(
            split["download_local_model"],
            "/api/v1/models/local/downloads",
        )
        with self.assertRaises(ValueError):
            normalize_service_layout("unknown")


if __name__ == "__main__":
    unittest.main()
