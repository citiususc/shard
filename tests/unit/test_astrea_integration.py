"""Tests for ontology-derived Astrea baseline generation."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.application.baseline_generation import generate_astrea_baseline  # noqa: E402
from shard.integrations.astrea import (  # noqa: E402
    AstreaRateLimitError,
    AstreaResponseError,
    AstreaTimeoutError,
    AstreaUnavailableError,
    generate_astrea_shapes,
)


ONTOLOGY = """
@prefix ex: <http://example.org/test#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

ex:Ontology a owl:Ontology .
ex:Asset a owl:Class .
"""

ASTREA_SHAPES = """
@prefix sh: <http://www.w3.org/ns/shacl#> .

<urn:astrea:AssetShape> a sh:NodeShape ;
    sh:targetClass <http://example.org/test#Asset> .

<urn:astrea:IdentifierShape> a sh:PropertyShape ;
    sh:path <http://example.org/test#identifier> .
"""


class AstreaClientTests(unittest.TestCase):
    def test_document_endpoint_receives_turtle_and_returns_shape_counts(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, text=ASTREA_SHAPES, request=request)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = generate_astrea_shapes(
                ONTOLOGY,
                endpoint="https://astrea.example/api/shacl/document",
                client=client,
            )

        self.assertEqual(result["shape_count"], 2)
        self.assertEqual(result["node_shape_count"], 1)
        self.assertEqual(result["property_shape_count"], 1)
        self.assertFalse(result["partial"])
        body = json.loads(requests[0].content)
        self.assertEqual(body["serialisation"], "TURTLE")
        self.assertEqual(body["ontology"], ONTOLOGY)
        self.assertEqual(requests[0].headers["accept"], "text/turtle")

    def test_transport_failure_is_reported_as_unavailable(self):
        def fail(request):
            raise httpx.ConnectError("service down", request=request)

        with httpx.Client(transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(AstreaUnavailableError):
                generate_astrea_shapes(ONTOLOGY, client=client)

    def test_server_error_is_reported_as_unavailable(self):
        def fail(request):
            return httpx.Response(503, text="Unavailable", request=request)

        with httpx.Client(transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(AstreaUnavailableError):
                generate_astrea_shapes(ONTOLOGY, client=client)

    def test_rate_limit_and_timeout_are_reported_distinctly(self):
        cases = [
            (429, AstreaRateLimitError),
            (504, AstreaTimeoutError),
        ]
        for status, error_type in cases:
            with self.subTest(status=status):
                transport = httpx.MockTransport(
                    lambda request: httpx.Response(status, text="upstream", request=request)
                )
                with httpx.Client(transport=transport) as client:
                    with self.assertRaises(error_type):
                        generate_astrea_shapes(ONTOLOGY, client=client)

    def test_invalid_or_shapeless_response_is_rejected(self):
        responses = ["not turtle", "@prefix ex: <urn:test:> . ex:item ex:value ex:other ."]
        for content in responses:
            with self.subTest(content=content):
                transport = httpx.MockTransport(
                    lambda request: httpx.Response(200, text=content, request=request)
                )
                with httpx.Client(transport=transport) as client:
                    with self.assertRaises(AstreaResponseError):
                        generate_astrea_shapes(ONTOLOGY, client=client)


class AstreaApplicationTests(unittest.TestCase):
    def test_application_normalizes_ontology_and_validates_baseline(self):
        captured = {}

        def generate(ontology_turtle):
            captured["ontology"] = ontology_turtle
            return {
                "shape_document": ASTREA_SHAPES,
                "shape_count": 2,
                "node_shape_count": 1,
                "property_shape_count": 1,
                "triple_count": 4,
                "partial": False,
                "service_url": "https://astrea.example/api/shacl/document",
            }

        with patch(
            "shard.application.baseline_generation.generate_astrea_shapes",
            generate,
        ):
            result = generate_astrea_baseline({
                "ontology_content": ONTOLOGY,
                "ontology_filename": "asset.owl",
            })

        self.assertIn("owl:Ontology", captured["ontology"])
        self.assertEqual(result["source"], "astrea-api")
        self.assertEqual(result["name"], "asset_astrea.ttl")
        self.assertEqual(len(result["ontology_hash"]), 64)
        self.assertTrue(result["validation"]["syntax_valid"])
        self.assertTrue(result["validation"]["generic_profile_active"])


if __name__ == "__main__":
    unittest.main()
