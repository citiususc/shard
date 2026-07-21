"""HTTP regression tests for the strict API and legacy compatibility routes."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from run_demo import ApplicationHTTPRequestHandler, compatibility_server_specs, parse_args  # noqa: E402
from shard.integrations.astrea import (  # noqa: E402
    AstreaRateLimitError,
    AstreaTimeoutError,
    AstreaUnavailableError,
)
from shard.api.operational import RATE_LIMITER  # noqa: E402
from shard.observability import logger  # noqa: E402


ONTOLOGY = """@prefix ex: <http://example.org/books#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Book a owl:Class ; rdfs:label "Book" .
ex:title a owl:DatatypeProperty ; rdfs:label "title" ;
    rdfs:domain ex:Book ; rdfs:range xsd:string .
"""

SHAPE = """@prefix ex: <http://example.org/books#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
<http://example.org/books/shapes/BookShape> a sh:NodeShape ;
    sh:targetClass ex:Book ; sh:property [ sh:path ex:title ; sh:minCount 1 ] .
"""

BATCH = """# Data Constraints

## Rule
- Number: BR-BOOK-001
- Title: Book title
### Data constraint
Every Book must have exactly one title.
"""

RULE = {
    "number": "BR-BOOK-001",
    "title": "Book title",
    "text": "Every Book must have exactly one title.",
}


def _workflow_result(workflow="rule-to-shape"):
    resolution = {
        "rule_number": RULE["number"],
        "title": RULE["title"],
        "text": RULE["text"],
        "resolution": {
            "resolved_by": "label",
            "confidence": 0.91,
            "targets": ["ex:Book", "ex:title"],
            "focus_nodes": ["ex:Book"],
            "constraint_paths": ["ex:title"],
            "related_terms": [],
        },
    }
    shape = {
        "rule_number": RULE["number"],
        "rule_title": RULE["title"],
        "targets": ["ex:Book", "ex:title"],
        "focus_nodes": ["ex:Book"],
        "constraint_paths": ["ex:title"],
        "related_terms": [],
        "shape": SHAPE,
        "valid": True,
        "attempts": 1,
        "error_type": "none",
    }
    summary = {
        "rules_total": 1, "rules_unresolved": 0, "targets_total": 2,
        "generated_total": 1, "valid": 1, "invalid": 0,
    }
    if workflow == "rule-to-shape":
        return {
            "workflow": workflow, "rule": resolution, "shape": shape,
            "unresolved": False, "unresolved_rules": [], "summary": summary,
            "namespaces": {}, "astrea": {}, "merge": None,
            "final_shape_document": SHAPE,
        }
    return {
        "workflow": "batch-to-shapes", "summary": summary,
        "generation": {
            "rules": [resolution], "shapes": [shape], "unresolved_rules": [],
            "shape_document": SHAPE,
        },
        "astrea": {}, "merge": None, "final_shape_document": SHAPE,
    }


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

    def request(self, method, path, payload=None, request_id="contract-test", headers=None):
        body = json.dumps(payload).encode() if payload is not None else None
        return self.request_bytes(
            method, path, body, request_id=request_id, extra_headers=headers
        )

    def request_bytes(
        self, method, path, body=None, request_id="contract-test", extra_headers=None
    ):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        headers = {"Connection": "close", "X-Request-ID": request_id}
        if body is not None:
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
        headers.update(extra_headers or {})
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        result = response.status, dict(response.getheaders()), json.loads(raw or b"{}")
        connection.close()
        return result

    def request_raw(self, path):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        connection.request("GET", path, headers={"Connection": "close"})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def request_sse(self, path, payload):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        body = json.dumps(payload).encode()
        connection.request("POST", path, body=body, headers={
            "Connection": "close", "Content-Type": "application/json",
            "Content-Length": str(len(body)), "X-Request-ID": "sse-contract-test",
        })
        response = connection.getresponse()
        headers = dict(response.getheaders())
        events = []
        while True:
            line = response.readline().decode()
            if not line:
                break
            if line.startswith("data: "):
                event = json.loads(line[6:])
                events.append(event)
                if event.get("event") in {"completed", "failed"} or event.get("type") in {"done", "error"}:
                    break
        connection.close()
        return response.status, headers, events

    def test_parse_and_validation_support_canonical_and_legacy_inputs(self):
        canonical = self.request("POST", "/api/v1/ontology/parse", {
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY}
        })
        legacy = self.request("POST", "/parse-ontology", {
            "filename": "books.ttl", "content": ONTOLOGY
        })
        self.assertEqual(canonical[0], 200)
        self.assertEqual(legacy[0], 200)
        self.assertEqual(len(canonical[2]["entities"]), len(legacy[2]["entities"]))
        self.assertIn("operation_metadata", canonical[2])
        self.assertNotIn("provenance", canonical[2])
        self.assertNotIn("provenance", legacy[2])

        canonical = self.request("POST", "/api/v1/shapes/validate", {
            "shape_document": SHAPE
        })
        legacy = self.request("POST", "/validate-shape", {"shape": SHAPE})
        self.assertTrue(canonical[2]["valid"])
        self.assertEqual(canonical[2]["valid"], legacy[2]["valid"])
        self.assertIn("provenance", canonical[2])

    def test_target_resolution_uses_the_discriminated_request(self):
        status, _, response = self.request("POST", "/api/v1/rules/resolve-targets", {
            "input_type": "rule",
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
            "rule": RULE,
            "resolver": {"llm_fallback": False},
        })
        self.assertEqual(status, 200)
        self.assertEqual(response["rules"][0]["resolved_by"], "label")
        self.assertEqual(response["rules"][0]["score_kind"], "lexical")
        self.assertIn("resolution_score", response["rules"][0])
        self.assertNotIn("confidence", response["rules"][0])
        self.assertEqual(response["rules"][0]["rule"], RULE)

    def test_shape_build_accepts_a_previously_grounded_rule_context(self):
        request = {
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
            "rule": RULE,
            "target_roles": {
                "focus_nodes": [{"iri": "ex:Book", "label": "Book"}],
                "constraint_paths": [{"iri": "ex:title", "label": "title"}],
                "related_terms": [{"iri": "xsd:string", "label": "string"}],
            },
        }
        result = {
            "shape": SHAPE,
            "valid": True,
            "attempts": 1,
            "hints": [],
            "fallback": False,
            "not_found": False,
            "error_type": "none",
            "message": "Generated and validated.",
        }

        with patch("shard.api.operations.build_shape", return_value=result):
            status, _, response = self.request("POST", "/api/v1/shapes/build", request)

        self.assertEqual(status, 200)
        self.assertEqual(response["shape_document"], SHAPE)
        self.assertTrue(response["valid"])
        self.assertEqual(response["provenance"]["source_rule"], RULE)
        self.assertEqual(
            response["provenance"]["target_roles"]["constraint_paths"][0]["iri"],
            "ex:title",
        )

    def test_astrea_errors_are_typed_and_timeout_is_distinct(self):
        request = {"ontology": {"filename": "books.ttl", "content": ONTOLOGY}}
        cases = [
            (AstreaUnavailableError("offline"), 503, "ASTREA_UNAVAILABLE"),
            (AstreaRateLimitError("busy"), 429, "ASTREA_RATE_LIMIT_EXCEEDED"),
            (AstreaTimeoutError("late"), 504, "ASTREA_REQUEST_TIMEOUT"),
        ]
        for error, expected_status, expected_code in cases:
            with self.subTest(code=expected_code), patch(
                "shard.api.operations.generate_astrea_baseline", side_effect=error
            ):
                status, _, payload = self.request(
                    "POST", "/api/v1/baselines/astrea", request
                )
            self.assertEqual(status, expected_status)
            self.assertEqual(payload["code"], expected_code)
            self.assertEqual(
                set(payload), {"error", "code", "message", "request_id", "details"}
            )

    def test_schema_malformed_json_and_not_found_errors_match_runtime_codes(self):
        status, _, error = self.request("POST", "/api/v1/ontology/parse", {})
        self.assertEqual(status, 422)
        self.assertEqual(error["code"], "REQUEST_SCHEMA_VALIDATION_FAILED")
        self.assertTrue(error["details"]["issues"])

        status, _, error = self.request_bytes(
            "POST", "/api/v1/ontology/parse", b"{invalid"
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["code"], "MALFORMED_JSON")

        status, _, error = self.request("GET", "/api/v1/not-a-route")
        self.assertEqual(status, 404)
        self.assertEqual(error["code"], "RESOURCE_NOT_FOUND")

    def test_workflow_provenance_and_logs_redact_credentials(self):
        secret = "workflow-secret-token"

        def fake_workflow(_):
            logger.info(f"Diagnostic accidentally included {secret}")
            return _workflow_result()

        with patch.dict(
            "shard.api.operations.WORKFLOW_OPERATIONS",
            {"workflows.rule.generate": fake_workflow},
        ), patch("builtins.print"):
            status, headers, payload = self.request(
                "POST", "/api/v1/workflows/rule-to-shape", {
                    "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
                    "rule": RULE,
                    "inference": {
                        "provider": "databricks", "generation_model": "chat-model",
                        "databricks": {"base_url": "https://private.example/api", "token": secret},
                    },
                },
            )
        self.assertEqual(status, 200)
        serialized = json.dumps(payload)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("private.example", serialized)
        self.assertIn("[redacted]", payload["logs"])
        self.assertEqual(payload["provenance"]["source_rule"], RULE)
        self.assertEqual(headers["X-SHARD-Operation"], "workflows.rule.generate")

    def test_batch_workflow_uses_only_the_canonical_versioned_route(self):
        request = {
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
            "batch": {"filename": "rules.md", "content": BATCH, "format": "md"},
        }
        fake = Mock(return_value=_workflow_result("batch-to-shapes"))
        with patch.dict(
            "shard.api.operations.WORKFLOW_OPERATIONS",
            {"workflows.batch.generate": fake},
        ):
            canonical = self.request("POST", "/api/v1/workflows/batch-to-shapes", request)
            removed = self.request("POST", "/api/v1/workflows/batch-to-rules", request)
        self.assertEqual(canonical[0], 200)
        self.assertEqual(canonical[2]["workflow"], "batch-to-shapes")
        self.assertEqual(removed[0], 404)

    def test_removed_job_compatibility_routes_return_not_found(self):
        for path in (
            "/api/v1/ontology/index/status",
            "/api/v1/ontology/index/cancel",
            "/api/v1/models/local/download",
        ):
            with self.subTest(path=path):
                status, _, payload = self.request("POST", path, {})
                self.assertEqual(status, 404)
                self.assertEqual(payload["code"], "RESOURCE_NOT_FOUND")

    def test_api_requires_no_shard_authorization_header(self):
        status, _, response = self.request("POST", "/api/v1/ontology/parse", {
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY}
        })
        self.assertEqual(status, 200)
        self.assertIn("entities", response)

    def test_cors_is_explicit_and_does_not_advertise_authorization(self):
        allowed = self.request(
            "GET", "/api/v1/health", headers={"Origin": "http://127.0.0.1:8768"}
        )
        rejected = self.request(
            "GET", "/api/v1/health", headers={"Origin": "https://untrusted.example"}
        )
        self.assertEqual(allowed[1].get("Access-Control-Allow-Origin"), "http://127.0.0.1:8768")
        self.assertNotIn("Authorization", allowed[1]["Access-Control-Allow-Headers"])
        self.assertNotIn("Access-Control-Allow-Origin", rejected[1])

    def test_extreme_request_rate_returns_429_with_retry_after(self):
        RATE_LIMITER.reset()
        limits = {
            "RATE_LIMIT_REQUESTS_PER_MINUTE": "2",
            "RATE_LIMIT_BURST": "2",
            "RATE_LIMIT_EXPENSIVE_REQUESTS_PER_MINUTE": "2",
        }
        try:
            with patch.dict("os.environ", limits):
                for _ in range(2):
                    response = self.request("POST", "/api/v1/ontology/parse", {
                        "ontology": {"filename": "books.ttl", "content": ONTOLOGY}
                    })
                    self.assertEqual(response[0], 200)
                limited = self.request("POST", "/api/v1/ontology/parse", {
                    "ontology": {"filename": "books.ttl", "content": ONTOLOGY}
                })
            self.assertEqual(limited[0], 429)
            self.assertGreaterEqual(int(limited[1]["Retry-After"]), 1)
            self.assertEqual(limited[2]["code"], "DEPLOYMENT_RATE_LIMIT_EXCEEDED")
        finally:
            RATE_LIMITER.reset()

    def test_extreme_json_body_returns_413(self):
        with patch.dict("os.environ", {"MAX_REQUEST_BODY_MB": "1"}):
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_address[1], timeout=10
            )
            connection.putrequest("POST", "/api/v1/ontology/parse")
            connection.putheader("Connection", "close")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(1024 * 1024 + 1))
            connection.putheader("X-Request-ID", "contract-test")
            connection.endheaders()
            raw_response = connection.getresponse()
            status = raw_response.status
            payload = json.loads(raw_response.read() or b"{}")
            connection.close()
        self.assertEqual(status, 413)
        self.assertEqual(payload["code"], "PAYLOAD_TOO_LARGE")

    def test_local_model_status_preserves_the_split_layout_adapter(self):
        status_result = {
            "model": "example/tiny-model", "downloaded": False,
            "status": "not-downloaded", "message": "Not downloaded locally.",
        }
        with patch("shard.api.operations.local_model_status", return_value=status_result):
            canonical = self.request("POST", "/api/v1/models/local/status", {
                "model_id": "example/tiny-model"
            })
            legacy = self.request("POST", "/local-model-status", {
                "model": "example/tiny-model"
            })
        self.assertEqual(canonical[2]["model_id"], "example/tiny-model")
        self.assertEqual(legacy[2]["model"], "example/tiny-model")

    def test_public_profile_disables_local_inference(self):
        with patch.dict("os.environ", {"SHARD_DEPLOYMENT_PROFILE": "public"}):
            status, _, payload = self.request("POST", "/api/v1/models/local/status", {
                "model_id": "example/tiny-model"
            })
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "LOCAL_MODELS_DISABLED")
        self.assertEqual(payload["details"]["provider"], "huggingface")

    def test_model_check_maps_provider_failures_to_http_codes(self):
        cases = [
            ({"ok": False, "error_code": "provider_authentication_failed", "message": "Invalid credentials."}, 401, "PROVIDER_AUTHENTICATION_FAILED"),
            ({"ok": False, "error_code": "rate_limited", "message": "Rate limited."}, 429, "MODEL_RATE_LIMIT_EXCEEDED"),
            ({"ok": False, "error_code": "provider_timeout", "message": "Timed out."}, 504, "MODEL_REQUEST_TIMEOUT"),
            ({"ok": False, "error_code": "model_unavailable", "message": "Unavailable."}, 503, "MODEL_UNAVAILABLE"),
        ]
        request = {
            "inference_provider": "databricks", "model_id": "chat-model", "role": "chat"
        }
        for result, expected, expected_code in cases:
            with self.subTest(status=expected), patch(
                "shard.api.operations.validate_model", return_value=result
            ):
                status, _, payload = self.request(
                    "POST", "/api/v1/models/check", request
                )
            self.assertEqual(status, expected)
            self.assertEqual(payload["code"], expected_code)

    def test_model_check_uses_request_credentials_without_exposing_them(self):
        captured = {}
        secret = "dapi-browser-secret"
        base_url = "https://workspace.example/ai-gateway/v1"

        def inspect_context(_):
            from shard.inference.context import (  # noqa: PLC0415
                get_databricks_base_url,
                get_databricks_token,
            )

            captured["base_url"] = get_databricks_base_url()
            captured["token"] = get_databricks_token()
            return {"ok": True, "message": "Available."}

        request = {
            "inference_provider": "databricks",
            "model_id": "chat-model",
            "role": "chat",
            "inference": {
                "provider": "databricks",
                "generation_model": "chat-model",
                "databricks": {"base_url": base_url, "token": secret},
            },
        }
        with patch("shard.api.operations.validate_model", side_effect=inspect_context):
            status, _, response = self.request("POST", "/api/v1/models/check", request)

        self.assertEqual(status, 200)
        self.assertEqual(captured, {"base_url": base_url, "token": secret})
        serialized = json.dumps(response)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(base_url, serialized)

    def test_shape_build_provider_timeout_returns_504(self):
        request = {
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
            "rule": RULE,
            "target_roles": {
                "focus_nodes": [{"iri": "ex:Book"}],
                "constraint_paths": [{"iri": "ex:title"}],
                "related_terms": [],
            },
        }
        with patch("shard.api.operations.build_shape", return_value={
            "shape": "",
            "valid": False,
            "error": "provider timed out",
            "error_type": "timeout",
            "attempts": 0,
        }):
            status, _, response = self.request(
                "POST", "/api/v1/shapes/build", request
            )
        self.assertEqual(status, 504)
        self.assertEqual(response["code"], "MODEL_REQUEST_TIMEOUT")

    def test_embedding_index_creation_returns_a_job_resource(self):
        with patch("shard.api.operations.prepare_embeddings", return_value={
            "status": "ready", "message": "Ready.", "ontology_fingerprint": "fp"
        }):
            status, _, created = self.request("POST", "/api/v1/ontology/indexes", {
                "ontology_terms": [], "ontology_hash": "books"
            })
            self.assertEqual(status, 202)
            self.assertIn(created["status"], {"queued", "running", "completed"})
            job_id = created["job_id"]
            for _ in range(50):
                status, _, job = self.request("GET", f"/api/v1/ontology/indexes/{job_id}")
                if job["status"] == "completed":
                    break
                time.sleep(0.01)
        self.assertEqual(status, 200)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["progress"], 1.0)
        status, _, error = self.request(
            "DELETE", f"/api/v1/ontology/indexes/{job_id}"
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["code"], "JOB_ALREADY_COMPLETED")

    def test_local_model_download_job_returns_202_and_can_be_polled(self):
        with patch("shard.api.operations.download_local_model", return_value={
            "downloaded": True, "model": "example/tiny-model"
        }):
            status, _, created = self.request(
                "POST", "/api/v1/models/local/downloads",
                {"model_id": "example/tiny-model"},
            )
        self.assertEqual(status, 202)
        job_id = created["job_id"]
        for _ in range(50):
            status, _, job = self.request(
                "GET", f"/api/v1/models/local/downloads/{job_id}"
            )
            if job["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(status, 200)
        self.assertEqual(job["status"], "completed")

    def test_batch_sse_uses_named_json_events_and_terminal_completed(self):
        request = {
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
            "batch": {"filename": "rules.md", "content": BATCH, "format": "md"},
        }

        def fake_generation(_, event_callback=None, **__):
            event_callback({
                "type": "rule", "stage": "rule", "rule_number": RULE["number"],
                "title": RULE["title"], "current": 1, "total": 1,
            })
            event_callback({"type": "done", "total": 1, "valid": 1, "invalid": 0})
            return {}

        with patch("shard.application.batch_generation.generate_batch_shapes", fake_generation):
            status, headers, events = self.request_sse("/api/v1/batches/generate", request)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/event-stream"))
        self.assertEqual(events[-1]["event"], "completed")
        self.assertEqual([item["sequence"] for item in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(
            item["operation_metadata"]["operation"] == "batches.generate"
            for item in events
        ))
        self.assertTrue(all("provenance" in item for item in events))

    def test_batch_sse_failure_is_terminal_and_structured(self):
        request = {
            "ontology": {"filename": "books.ttl", "content": ONTOLOGY},
            "batch": {"filename": "rules.md", "content": BATCH, "format": "md"},
        }

        def failed_generation(*_, **__):
            raise TimeoutError("provider request timed out")

        with patch(
            "shard.application.batch_generation.generate_batch_shapes",
            side_effect=failed_generation,
        ):
            status, _, events = self.request_sse("/api/v1/batches/generate", request)

        self.assertEqual(status, 200)
        self.assertEqual(events[-1]["event"], "failed")
        self.assertEqual(events[-1]["error"]["code"], "MODEL_REQUEST_TIMEOUT")
        self.assertEqual(events[-1]["error"]["request_id"], "sse-contract-test")

    def test_discovery_swagger_redoc_capabilities_and_static_ui(self):
        status, _, root = self.request("GET", "/api/v1")
        self.assertEqual(status, 200)
        self.assertEqual(root["workflows"]["batch_to_shapes"], "/api/v1/workflows/batch-to-shapes")

        status, _, document = self.request("GET", "/api/v1/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertIn("/api/v1/workflows/batch-to-shapes", document["paths"])
        self.assertNotIn("/api/v1/workflows/batch-to-rules", document["paths"])
        self.assertNotIn("/api/v1/ontology/index", document["paths"])
        self.assertNotIn("securitySchemes", document["components"])
        credential = document["components"]["schemas"]["DatabricksCredentials"]
        token_schema = credential["properties"]["token"]
        self.assertEqual(token_schema["type"], "string")
        self.assertEqual(token_schema["format"], "password")
        self.assertTrue(token_schema["writeOnly"])

        for path, marker in (("/api/v1/docs", "SwaggerUIBundle"), ("/api/v1/redoc", "Redoc.init")):
            status, headers, body = self.request_raw(path)
            self.assertEqual(status, 200)
            self.assertIn(marker, body.decode())
            self.assertIn("Content-Security-Policy", headers)

        status, _, capabilities = self.request("GET", "/api/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(len(capabilities["api"]["services"]), 5)
        self.assertNotIn("token", json.dumps(capabilities).lower())

        status, _, body = self.request_raw("/rule.html")
        self.assertEqual(status, 200)
        self.assertIn("Rule", body.decode())

    def test_runtime_retains_split_layout_and_legacy_ports(self):
        with patch.dict("os.environ", {"SHARD_SERVICE_LAYOUT": ""}):
            self.assertEqual(parse_args([]).service_layout, "unified")
        self.assertEqual(parse_args(["--service-layout", "split"]).service_layout, "split")
        self.assertEqual(
            [port for _, port, _ in compatibility_server_specs()],
            [9100, 9101, 9102, 9103, 9104],
        )


if __name__ == "__main__":
    unittest.main()
