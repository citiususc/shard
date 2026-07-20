"""Unit coverage for helpers used by grounded shape generation."""

from pathlib import Path
import sys
import unittest

from rdflib import Graph


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "src"))

from shard.application.generation_support import (  # noqa: E402
    clean_shacl_response,
    get_info_by_name,
    get_property_domain,
)


class GenerationSupportTests(unittest.TestCase):
    def test_clean_shacl_response_extracts_fenced_turtle(self):
        response = """Here is the generated shape:
```turtle
ex:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset .
```
Additional explanation.
"""

        cleaned = clean_shacl_response(response)

        self.assertTrue(cleaned.startswith("ex:AssetShape"))
        self.assertTrue(cleaned.endswith("ex:Asset ."))
        self.assertNotIn("Additional explanation", cleaned)

    def test_get_property_domain_expands_owl_union(self):
        graph = Graph().parse(
            data="""
@prefix ex: <http://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:relatedTo a owl:ObjectProperty ;
    rdfs:domain [ owl:unionOf (ex:Asset ex:Site) ] .
""",
            format="turtle",
        )

        domains = get_property_domain(graph, "http://example.org/relatedTo")

        self.assertEqual(
            domains,
            ["http://example.org/Asset", "http://example.org/Site"],
        )

    def test_get_info_by_name_returns_entity_statements(self):
        graph = Graph().parse(
            data="""
@prefix ex: <http://example.org/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Asset rdfs:label "Asset" .
""",
            format="turtle",
        )

        info = get_info_by_name(graph, "Asset")

        self.assertIn("http://example.org/Asset", info)
        self.assertIn("Asset", info)


if __name__ == "__main__":
    unittest.main()
