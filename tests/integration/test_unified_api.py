"""HTTP regression tests for canonical and legacy API routes."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from run_demo import (  # noqa: E402
    ApplicationHTTPRequestHandler,
    compatibility_server_specs,
    parse_args,
)
from shard.api.operations import send_guide_event  # noqa: E402
from shard.integrations.astrea import AstreaUnavailableError  # noqa: E402
from shard.observability import logger  # noqa: E402


class UnifiedApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ApplicationHTTPRequestHandler.service_layout = "unified"
        handler = partial(ApplicationHTTPRequestHandler, directory=str(ROOT / "frontend"))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, payload=None, request_id="contract-test"):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Connection": "close", "X-Request-ID": request_id}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        result = response.status, dict(response.getheaders()), json.loads(raw or b"{}")
        connection.close()
        return result

    def request_raw(self, method, path, request_id="contract-test"):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        connection.request(method, path, headers={
            "Connection": "close",
            "X-Request-ID": request_id,
        })
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def request_sse(self, path, payload, request_id="stream-contract-test"):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        body = json.dumps(payload).encode("utf-8")
        connection.request("POST", path, body=body, headers={
            "Connection": "close",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Request-ID": request_id,
        })
        response = connection.getresponse()
        headers = dict(response.getheaders())
        events = []
        while True:
            line = response.readline().decode("utf-8")
            if not line:
                break
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event.get("type") in {"done", "error"}:
                break
        connection.close()
        return response.status, headers, events

    def assert_alias_equivalence(self, canonical, legacy, payload):
        canonical_response = self.request("POST", canonical, payload)
        legacy_response = self.request("POST", legacy, payload)
        self.assertEqual(canonical_response[0], legacy_response[0])
        canonical_payload = dict(canonical_response[2])
        provenance = canonical_payload.pop("provenance")
        self.assertEqual(canonical_payload, legacy_response[2])
        self.assertEqual(provenance["request_id"], "contract-test")
        self.assertEqual(provenance["route"], canonical)
        self.assertNotIn("provenance", legacy_response[2])
        self.assertEqual(canonical_response[1]["X-Request-ID"], "contract-test")
        self.assertEqual(legacy_response[1]["X-Request-ID"], "contract-test")
        self.assertEqual(canonical_response[1]["X-SHARD-API-Version"], "v1")

    def request_standalone(self, handler, path, payload):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_address[1], timeout=10
            )
            body = json.dumps(payload).encode("utf-8")
            connection.request("POST", path, body=body, headers={
                "Connection": "close",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Request-ID": "standalone-contract-test",
            })
            response = connection.getresponse()
            result = response.status, json.loads(response.read() or b"{}")
            connection.close()
            return result
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_parse_route_matches_legacy_alias(self):
        ontology = (ROOT / "examples" / "asset-maintenance" / "ontology.ttl").read_text(
            encoding="utf-8"
        )
        self.assert_alias_equivalence(
            "/api/v1/ontology/parse",
            "/parse-ontology",
            {"filename": "asset.ttl", "content": ontology},
        )

    def test_shape_validation_route_matches_legacy_alias(self):
        payload = {
            "prefixes": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
            "shape": "<urn:test:Shape> a sh:NodeShape .",
        }
        self.assert_alias_equivalence(
            "/api/v1/shapes/validate", "/validate-shape", payload
        )

    def test_astrea_baseline_route_matches_legacy_alias(self):
        result = {
            "available": True,
            "source": "astrea-api",
            "name": "ontology_astrea.ttl",
            "ontology_hash": "abc123",
            "shape_document": "<urn:test:Shape> a <http://www.w3.org/ns/shacl#NodeShape> .",
            "shape_count": 1,
        }
        with patch(
            "shard.api.operations.generate_astrea_baseline",
            return_value=result,
        ):
            self.assert_alias_equivalence(
                "/api/v1/baselines/astrea",
                "/generate-astrea-baseline",
                {"ontology_filename": "ontology.ttl", "ontology_content": "test"},
            )

    def test_astrea_unavailability_has_a_stable_machine_readable_error(self):
        with patch(
            "shard.api.operations.generate_astrea_baseline",
            side_effect=AstreaUnavailableError("Astrea timed out."),
        ):
            status, _, payload = self.request(
                "POST",
                "/api/v1/baselines/astrea",
                {"ontology_filename": "ontology.ttl", "ontology_content": "test"},
            )
        self.assertEqual(status, 503)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["error_type"], "astrea_unavailable")
        self.assertIn("continue without it", payload["message"])

    def test_provenance_excludes_credentials_and_request_content(self):
        secret = "secret-token-that-must-not-leak"
        rule = "Private business rule text"
        status, _, payload = self.request("POST", "/api/v1/shapes/validate", {
            "shape": "<urn:test:Shape> a <http://www.w3.org/ns/shacl#NodeShape> .",
            "business_rule": rule,
            "provider": "databricks",
            "model": "example-model",
            "inference_config": {
                "provider": "databricks",
                "databricks": {
                    "token": secret,
                    "base_url": "https://private.example/api",
                },
            },
        })
        self.assertEqual(status, 200)
        serialized = json.dumps(payload["provenance"])
        self.assertNotIn(secret, serialized)
        self.assertNotIn("private.example", serialized)
        self.assertNotIn(rule, serialized)
        self.assertEqual(payload["provenance"]["inference"]["models"]["generation"], "example-model")

    def test_public_profile_rejects_local_inference_through_both_routes(self):
        request = {
            "provider": "huggingface",
            "model": "example/local-model",
            "inference_config": {"provider": "huggingface"},
        }
        with patch.dict("os.environ", {"SHARD_DEPLOYMENT_PROFILE": "public"}):
            canonical = self.request("POST", "/api/v1/shapes/build", request)
            legacy = self.request("POST", "/build-shacl-shape", request)
        self.assertEqual(canonical[0], 403)
        self.assertEqual(legacy[0], 403)
        canonical_payload = dict(canonical[2])
        self.assertEqual(canonical_payload.pop("provenance")["deployment_profile"], "public")
        self.assertEqual(canonical_payload, legacy[2])
        self.assertEqual(canonical_payload["code"], "provider_disabled")

    def test_local_model_status_route_checks_cache_without_downloading(self):
        result = {
            "model": "example/tiny-model",
            "downloaded": False,
            "status": "not-downloaded",
            "message": "Not downloaded locally.",
        }
        with patch("shard.api.operations.local_model_status", return_value=result) as status:
            self.assert_alias_equivalence(
                "/api/v1/models/local/status",
                "/local-model-status",
                {"model": "example/tiny-model"},
            )
        self.assertEqual(status.call_count, 2)

    def test_local_model_download_streams_only_after_explicit_request(self):
        def fake_download(model_id, emit):
            emit({
                "type": "start",
                "model": model_id,
                "percent": 0,
                "message": "Starting.",
            })
            result = {
                "type": "done",
                "model": model_id,
                "downloaded": True,
                "percent": 100,
                "message": "Downloaded locally.",
            }
            emit(result)
            return result

        with patch("shard.api.operations.download_local_model", side_effect=fake_download):
            status, headers, events = self.request_sse(
                "/api/v1/models/local/download",
                {"model": "example/tiny-model"},
            )
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/event-stream"))
        self.assertEqual([event["type"] for event in events], ["start", "done"])
        self.assertEqual(events[-1]["request_id"], "stream-contract-test")

    def test_public_profile_rejects_local_cache_and_download_operations(self):
        payload = {"model": "example/tiny-model"}
        with patch.dict("os.environ", {"SHARD_DEPLOYMENT_PROFILE": "public"}):
            status_response = self.request(
                "POST", "/api/v1/models/local/status", payload
            )
            download_response = self.request(
                "POST", "/api/v1/models/local/download", payload
            )
        self.assertEqual(status_response[0], 403)
        self.assertEqual(download_response[0], 403)
        self.assertEqual(status_response[2]["code"], "provider_disabled")
        self.assertEqual(download_response[2]["code"], "provider_disabled")

    def test_guide_validation_error_matches_legacy_alias(self):
        self.assert_alias_equivalence(
            "/api/v1/guides/generate", "/generate-from-guide", {}
        )

    def test_api_root_and_openapi_document_are_discoverable(self):
        status, _, payload = self.request("GET", "/api/v1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["docs"], "/api/v1/docs")
        self.assertEqual(payload["documentation"], "/api/v1/docs")
        self.assertEqual(payload["openapi"], "/api/v1/openapi.json")
        self.assertEqual(
            payload["workflows"]["rule_to_shape"],
            "/api/v1/workflows/rule-to-shape",
        )

        status, headers, document = self.request("GET", "/api/v1/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertIn("/api/v1/workflows/guide-to-shapes", document["paths"])
        self.assertNotIn("request_id", document)
        self.assertNotIn("provenance", document)
        self.assertTrue(
            headers["Content-Type"].startswith("application/vnd.oai.openapi+json")
        )

    def test_swagger_ui_is_served_with_the_openapi_contract_and_csp(self):
        status, headers, body = self.request_raw("GET", "/api/v1/docs")
        document = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertEqual(headers["X-SHARD-Operation"], "system.docs")
        self.assertIn("SwaggerUIBundle", document)
        self.assertIn("/api/v1/openapi.json", document)
        self.assertIn("SHARD REST API", document)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_complete_rule_workflow_accepts_the_nested_contract(self):
        workflow_result = {
            "workflow": "rule-to-shape",
            "rule": {"rule_number": "BR-001", "resolution": {"resolved_by": "label"}},
            "shape": {"shape": "<urn:test:Shape> a <http://www.w3.org/ns/shacl#NodeShape> .", "valid": True},
            "unresolved": False,
            "unresolved_rules": [],
            "summary": {"rules_total": 1, "valid": 1, "invalid": 0},
            "final_shape_document": "<urn:test:Shape> a <http://www.w3.org/ns/shacl#NodeShape> .",
        }
        fake_workflow = Mock(return_value=workflow_result)
        with patch.dict(
            "shard.api.operations.WORKFLOW_OPERATIONS",
            {"workflows.rule.generate": fake_workflow},
        ):
            status, headers, payload = self.request(
                "POST",
                "/api/v1/workflows/rule-to-shape",
                {
                    "ontology": {"filename": "ontology.ttl", "content": "ontology"},
                    "rule": {"number": "BR-001", "title": "Rule", "text": "Rule text"},
                    "inference": {
                        "provider": "databricks",
                        "generation_model": "generation-model",
                    },
                },
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["workflow"], "rule-to-shape")
        self.assertEqual(payload["provenance"]["operation"], "workflows.rule.generate")
        self.assertEqual(headers["X-SHARD-Operation"], "workflows.rule.generate")
        normalized = fake_workflow.call_args.args[0]
        self.assertEqual(normalized["ontology_content"], "ontology")
        self.assertEqual(normalized["business_rule"], "Rule text")
        self.assertEqual(normalized["llm_model"], "generation-model")

    def test_complete_guide_workflow_reports_invalid_requests_as_json(self):
        status, _, payload = self.request(
            "POST",
            "/api/v1/workflows/guide-to-shapes",
            {"ontology": {"content": "ontology"}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "invalid_request")
        self.assertIn("guide.content", payload["message"])

    def test_complete_workflow_redacts_request_credentials_from_logs(self):
        secret = "workflow-secret-token"

        def fake_workflow(_):
            logger.info(f"Dependency diagnostic accidentally included {secret}")
            return {
                "workflow": "rule-to-shape",
                "rule": {},
                "shape": None,
                "unresolved": True,
                "summary": {},
                "final_shape_document": "",
            }

        with patch.dict(
            "shard.api.operations.WORKFLOW_OPERATIONS",
            {"workflows.rule.generate": fake_workflow},
        ), patch("builtins.print"):
            status, _, payload = self.request(
                "POST",
                "/api/v1/workflows/rule-to-shape",
                {
                    "ontology": {"content": "ontology"},
                    "rule": {"text": "Rule text"},
                    "inference": {
                        "provider": "databricks",
                        "databricks": {
                            "base_url": "https://example.test/mlflow/v1",
                            "token": secret,
                        },
                    },
                },
            )
        self.assertEqual(status, 200)
        serialized = json.dumps(payload)
        self.assertNotIn(secret, serialized)
        self.assertIn("[redacted]", payload["logs"])

    def test_public_profile_rejects_nested_local_inference_before_workflow(self):
        fake_workflow = Mock()
        with patch.dict(
            "shard.api.operations.WORKFLOW_OPERATIONS",
            {"workflows.rule.generate": fake_workflow},
        ), patch.dict("os.environ", {"SHARD_DEPLOYMENT_PROFILE": "public"}):
            status, _, payload = self.request(
                "POST",
                "/api/v1/workflows/rule-to-shape",
                {
                    "ontology": {"content": "ontology"},
                    "rule": {"text": "Rule text"},
                    "inference": {
                        "provider": "huggingface",
                        "generation_model": "org/local-model",
                    },
                },
            )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "provider_disabled")
        fake_workflow.assert_not_called()

    def test_capabilities_publish_the_machine_readable_contract(self):
        status, _, payload = self.request("GET", "/api/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(payload["api"]["version"], "v1")
        self.assertEqual(len(payload["api"]["services"]), 5)
        self.assertNotIn("token", json.dumps(payload).lower())

        legacy_status, legacy_headers, legacy_payload = self.request(
            "GET", "/api/capabilities"
        )
        self.assertEqual(legacy_status, 200)
        self.assertNotIn("api", legacy_payload)
        self.assertNotIn("request_id", legacy_payload)
        self.assertNotIn("X-Request-ID", legacy_headers)

        with patch.dict("os.environ", {"SHARD_SERVICE_LAYOUT": "split"}):
            split_status, _, split_payload = self.request("GET", "/api/v1/capabilities")
        self.assertEqual(split_status, 200)
        self.assertEqual(split_payload["api"]["service_layout"], "split")
        self.assertEqual(
            split_payload["api"]["runtime_endpoints"]["parse"],
            "http://127.0.0.1:9100/parse-ontology",
        )

    def test_health_and_unknown_api_routes_are_explicit(self):
        status, _, payload = self.request("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        status, _, payload = self.request("GET", "/api/v1/not-a-route")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "unknown endpoint")

    def test_static_ui_and_versioned_api_share_one_origin(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        connection.request("GET", "/rule.html", headers={"Connection": "close"})
        response = connection.getresponse()
        content = response.read().decode("utf-8")
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn("Rule", content)

    def test_runtime_retains_both_service_layouts_and_legacy_ports(self):
        with patch.dict("os.environ", {"SHARD_SERVICE_LAYOUT": ""}):
            self.assertEqual(parse_args([]).service_layout, "unified")
        self.assertEqual(parse_args(["--service-layout", "split"]).service_layout, "split")
        specs = compatibility_server_specs()
        self.assertEqual([port for _, port, _ in specs], [9100, 9101, 9102, 9103, 9104])

    def test_standalone_handlers_match_unified_legacy_aliases(self):
        ontology = """
@prefix ex: <http://example.org/test#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Asset a owl:Class .
"""
        cases = [
            ("/parse-ontology", {"filename": "ontology.ttl", "content": ontology}),
            ("/find-relevant-terms", {}),
            ("/validate-shape", {
                "shape": "<urn:test:Shape> a <http://www.w3.org/ns/shacl#NodeShape> ."
            }),
            ("/generate-from-guide", {}),
            ("/resolve-rule-targets", {}),
        ]
        for (_, _, handler), (path, payload) in zip(compatibility_server_specs(), cases):
            with self.subTest(path=path):
                direct_status, direct_payload = self.request_standalone(handler, path, payload)
                facade_status, _, facade_payload = self.request(
                    "POST", path, payload, request_id="standalone-contract-test"
                )
                self.assertEqual(direct_status, facade_status)
                self.assertEqual(direct_payload, facade_payload)

    def test_sse_transport_carries_the_same_provenance_object(self):
        class Buffer:
            def __init__(self):
                self.content = b""

            def write(self, value):
                self.content += value

            def flush(self):
                pass

        class FakeHandler:
            wfile = Buffer()
            response_provenance = {"request_id": "sse-test", "operation": "guides.generate"}

        handler = FakeHandler()
        send_guide_event(handler, {"type": "status", "stage": "parsing"}, "sse-test")
        event = json.loads(handler.wfile.content.decode("utf-8")[6:].strip())
        self.assertEqual(event["request_id"], "sse-test")
        self.assertEqual(event["provenance"], handler.response_provenance)

    def test_canonical_and_legacy_sse_preserve_the_event_sequence(self):
        ontology = """
@prefix ex: <http://example.org/test#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Asset a owl:Class .
ex:identifier a owl:DatatypeProperty ;
    rdfs:domain ex:Asset ;
    rdfs:range xsd:string .
"""
        guide = """
# Business Rules

## Rule

- Number: BR-001
- Title: Asset identifier

### Business rule

Every asset must have exactly one identifier.
"""
        payload = {
            "ontology_content": ontology,
            "ontology_filename": "ontology.ttl",
            "guide_content": guide,
            "guide_filename": "rules.md",
        }

        def fake_generation(_, event_callback=None, **__):
            event_callback({
                "type": "rule",
                "stage": "rule",
                "rule_number": "BR-001",
                "title": "Asset identifier",
                "current": 1,
                "total": 1,
            })
            event_callback({
                "type": "done",
                "unit": "rule",
                "total": 1,
                "valid": 0,
                "invalid": 0,
                "skipped": 0,
            })
            return {}

        with patch("shard.application.guide_generation.generate_guide_shapes", fake_generation):
            canonical = self.request_sse("/api/v1/guides/generate", payload)
            legacy = self.request_sse("/generate-from-guide", payload)

        self.assertEqual(canonical[0], 200)
        self.assertEqual(legacy[0], 200)
        canonical_events = []
        for event in canonical[2]:
            item = dict(event)
            provenance = item.pop("provenance")
            self.assertEqual(provenance["operation"], "guides.generate")
            canonical_events.append(item)
        self.assertEqual(canonical_events, legacy[2])
        self.assertEqual(
            [event.get("stage") or event.get("type") for event in canonical_events],
            ["parsing", "template", "preprocessing", "rule", "done"],
        )


if __name__ == "__main__":
    unittest.main()
