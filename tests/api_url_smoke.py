#!/usr/bin/env python3
"""Exercise the SHARD REST API using only URLs and inline test data.

This is a manual smoke test, not part of the unit-test suite. It deliberately
uses only Python's standard library and does not read any repository fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


ONTOLOGY = """\
@prefix ex: <http://example.org/assets#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://example.org/assets> a owl:Ontology ;
    rdfs:label "Inline asset ontology" .

ex:Asset a owl:Class ;
    rdfs:label "Asset" .

ex:assetIdentifier a owl:DatatypeProperty ;
    rdfs:label "asset identifier" ;
    rdfs:domain ex:Asset ;
    rdfs:range xsd:string .
"""

RULE = "Every Asset must have exactly one asset identifier."

GUIDE = """\
# Business Rules

## Rule

- Number: BR-API-001
- Title: Asset identifier

### Business rule

Every Asset must have exactly one asset identifier.
"""

GENERATED_SHAPE = """\
@prefix ex: <http://example.org/assets#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix shape: <http://example.org/assets/shapes/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

shape:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset ;
    sh:property [
        sh:path ex:assetIdentifier ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string
    ] .
"""

BASELINE_SHAPE = """\
@prefix ex: <http://example.org/assets#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix astrea: <http://example.org/astrea/shapes/> .

astrea:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset ;
    sh:property [
        sh:path ex:assetIdentifier ;
        sh:minCount 1
    ] .
"""


class ApiClient:
    """Small JSON/SSE client that reports useful HTTP error bodies."""

    def __init__(self, base_url: str, timeout: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        clean_path = path.lstrip("/")
        return f"{self.base_url}/{clean_path}" if clean_path else self.base_url

    def get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url(path),
            headers={"Accept": "application/json"},
        )
        return self._open_json(request)

    def get_html(self, path: str) -> str:
        request = urllib.request.Request(
            self._url(path),
            headers={"Accept": "text/html"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            self._raise_http_error(exc)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach {request.full_url}: {exc.reason}") from exc

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Request-ID": "standalone-api-smoke",
            },
            method="POST",
        )
        return self._open_json(request)

    def post_sse(self, path: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "X-Request-ID": "standalone-api-sse-smoke",
            },
            method="POST",
        )
        events: list[dict[str, Any]] = []
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    events.append(event)
                    if event.get("type") in {"done", "error"}:
                        break
        except urllib.error.HTTPError as exc:
            self._raise_http_error(exc)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach {request.full_url}: {exc.reason}") from exc
        return events

    def _open_json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            self._raise_http_error(exc)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach {request.full_url}: {exc.reason}") from exc

    @staticmethod
    def _raise_http_error(exc: urllib.error.HTTPError) -> None:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {exc.url}: {body}") from exc


def check(condition: Any, label: str) -> None:
    """Fail fast and print one compact result for a successful assertion."""
    if not condition:
        raise AssertionError(label)
    print(f"[PASS] {label}")


def inference_config() -> dict[str, Any]:
    """Build request-scoped Databricks settings without printing credentials."""
    base_url = os.environ.get("DATABRICKS_BASE_URL", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not base_url or not token:
        raise RuntimeError(
            "--with-inference requires DATABRICKS_BASE_URL and DATABRICKS_TOKEN."
        )
    return {
        "provider": "databricks",
        "generation_model": os.environ.get(
            "SHARD_LLM_MODEL",
            os.environ.get("BR2SHACL_LLM_MODEL", "gemma-3-12b"),
        ),
        "embedding_model": os.environ.get(
            "SHARD_EMBEDDING_MODEL",
            os.environ.get("BR2SHACL_EMBEDDING_MODEL", "qwen3-embedding-0-6b"),
        ),
        "temperature": 0.0,
        "databricks": {"base_url": base_url, "token": token},
    }


def basic_endpoint_checks(client: ApiClient) -> None:
    """Check endpoints that need neither inference credentials nor external APIs."""
    root = client.get_json("")
    check(root.get("api_version") == "v1", "GET /api/v1")

    health = client.get_json("health")
    check(bool(health), "GET /api/v1/health")

    capabilities = client.get_json("capabilities")
    services = ((capabilities.get("api") or {}).get("services") or [])
    check(len(services) == 5, "GET /api/v1/capabilities exposes five services")

    openapi = client.get_json("openapi.json")
    check(openapi.get("openapi") == "3.1.0", "GET /api/v1/openapi.json")

    docs = client.get_html("docs")
    check("SwaggerUIBundle" in docs, "GET /api/v1/docs")

    parsed = client.post_json(
        "ontology/parse",
        {"filename": "inline-ontology.ttl", "content": ONTOLOGY},
    )
    terms = parsed.get("entities") or []
    check(len(terms) == 2, "POST /api/v1/ontology/parse")

    ranking = client.post_json(
        "ontology/search",
        {"business_rule": RULE, "ontology_terms": terms, "top_k": 5},
    )
    check("method" in ranking, "POST /api/v1/ontology/search")

    for endpoint in ("ontology/index", "ontology/index/status", "ontology/index/cancel"):
        result = client.post_json(endpoint, {"ontology_terms": []})
        check("status" in result, f"POST /api/v1/{endpoint}")

    resolution = client.post_json(
        "rules/resolve-targets",
        {
            "ontology_filename": "inline-ontology.ttl",
            "ontology_content": ONTOLOGY,
            "business_rule": RULE,
            "rule_number": "BR-API-001",
            "rule_title": "Asset identifier",
            "resolver_llm_fallback": False,
        },
    )
    row = (resolution.get("rules") or [{}])[0]
    check(row.get("resolved_by") == "label", "POST /api/v1/rules/resolve-targets")

    validation = client.post_json("shapes/validate", {"shape": GENERATED_SHAPE})
    check(validation.get("valid") is True, "POST /api/v1/shapes/validate")

    merged = client.post_json(
        "shapes/merge",
        {
            "generated_shapes": GENERATED_SHAPE,
            "astrea_baseline": {
                "name": "inline-astrea.ttl",
                "content": BASELINE_SHAPE,
            },
            "technique": "priority-llm",
        },
    )
    check(merged.get("valid") is True, "POST /api/v1/shapes/merge")


def inference_endpoint_checks(client: ApiClient) -> None:
    """Check model-backed endpoints with request-scoped Databricks settings."""
    inference = inference_config()
    model = inference["generation_model"]

    parsed = client.post_json(
        "ontology/parse",
        {"filename": "inline-ontology.ttl", "content": ONTOLOGY},
    )
    terms = parsed.get("entities") or []
    ontology_hash = hashlib.sha256(ONTOLOGY.encode("utf-8")).hexdigest()
    index_payload = {
        "ontology_terms": terms,
        "ontology_hash": ontology_hash,
        "embedding_model": inference["embedding_model"],
        "inference_config": inference,
    }
    index = client.post_json("ontology/index", index_payload)
    deadline = time.monotonic() + client.timeout
    while index.get("status") in {"preparing", "cancelling"}:
        if time.monotonic() >= deadline:
            raise RuntimeError("Ontology embedding preparation timed out.")
        time.sleep(1)
        index = client.post_json("ontology/index/status", index_payload)
    check(index.get("status") == "ready", "semantic ontology index is ready")

    ranking = client.post_json(
        "ontology/search",
        {
            **index_payload,
            "business_rule": RULE,
            "top_k": 5,
        },
    )
    check(
        ranking.get("method") == "semantic" and bool(ranking.get("candidates")),
        "POST /api/v1/ontology/search with real embeddings",
    )

    model_result = client.post_json(
        "models/check",
        {
            "provider": "databricks",
            "model": model,
            "role": "chat",
            "inference_config": inference,
        },
    )
    check(model_result.get("ok") is True, "POST /api/v1/models/check")

    build_payload = {
        "business_rule": RULE,
        "ontology_filename": "inline-ontology.ttl",
        "ontology_content": ONTOLOGY,
        "target_roles": {
            "focus_nodes": ["http://example.org/assets#Asset"],
            "constraint_paths": ["http://example.org/assets#assetIdentifier"],
            "related_terms": [],
        },
        "model": model,
        "temperature": 0.0,
        "inference_config": inference,
        "astrea_use_mode": "none",
    }
    built = client.post_json("shapes/build", build_payload)
    check(built.get("valid") is True, "POST /api/v1/shapes/build")

    workflow_payload = {
        "ontology": {"filename": "inline-ontology.ttl", "content": ONTOLOGY},
        "rule": {
            "number": "BR-API-001",
            "title": "Asset identifier",
            "text": RULE,
        },
        "inference": inference,
        "resolver": {"llm_fallback": True, "wait_embeddings": False},
        "astrea": {"mode": "none"},
    }
    rule_workflow = client.post_json("workflows/rule-to-shape", workflow_payload)
    check(
        rule_workflow.get("workflow") == "rule-to-shape"
        and bool(rule_workflow.get("final_shape_document")),
        "POST /api/v1/workflows/rule-to-shape",
    )

    guide_payload = {
        "ontology": {"filename": "inline-ontology.ttl", "content": ONTOLOGY},
        "guide": {"filename": "inline-rules.md", "content": GUIDE},
        "inference": inference,
        "resolver": {"llm_fallback": True, "wait_embeddings": False},
        "astrea": {"mode": "none"},
    }
    guide_workflow = client.post_json("workflows/guide-to-shapes", guide_payload)
    check(
        guide_workflow.get("workflow") == "guide-to-shapes"
        and bool(guide_workflow.get("final_shape_document")),
        "POST /api/v1/workflows/guide-to-shapes",
    )

    stream_payload = {
        "ontology_filename": "inline-ontology.ttl",
        "ontology_content": ONTOLOGY,
        "guide_filename": "inline-rules.md",
        "guide_content": GUIDE,
        "llm_model": model,
        "embedding_model": inference["embedding_model"],
        "temperature": 0.0,
        "resolver_llm_fallback": True,
        "wait_embeddings": False,
        "astrea_use_mode": "none",
        "inference_config": inference,
    }
    events = client.post_sse("guides/generate", stream_payload)
    check(events and events[-1].get("type") == "done", "POST /api/v1/guides/generate (SSE)")


def astrea_endpoint_check(client: ApiClient) -> None:
    """Check the optional external Astrea integration endpoint."""
    result = client.post_json(
        "baselines/astrea",
        {"ontology_filename": "inline-ontology.ttl", "ontology_content": ONTOLOGY},
    )
    check(result.get("available") is True, "POST /api/v1/baselines/astrea")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SHARD_API_URL", "http://127.0.0.1:8768/api/v1"),
        help="Versioned API root, including /api/v1.",
    )
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--with-inference",
        action="store_true",
        help="Also call Databricks-backed generation endpoints.",
    )
    parser.add_argument(
        "--with-astrea",
        action="store_true",
        help="Also call the external Astrea baseline endpoint.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = ApiClient(args.base_url, args.timeout)
    print(f"Testing {args.base_url.rstrip('/')}", flush=True)
    try:
        basic_endpoint_checks(client)
        if args.with_inference:
            inference_endpoint_checks(client)
        if args.with_astrea:
            astrea_endpoint_check(client)
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP 404" in message and args.base_url.rstrip("/") in message:
            message += (
                "\nThe process at this URL does not expose the current versioned API. "
                "Restart run_demo.py so it loads the latest source code, or pass "
                "--base-url with the URL of a current SHARD API instance."
            )
        print(f"[FAIL] {message}", file=sys.stderr)
        return 1
    except (AssertionError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print("All requested endpoint checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
