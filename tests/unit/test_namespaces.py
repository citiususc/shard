"""Regression tests for generic ontology and generated-shape namespaces."""

from pathlib import Path
import sys
import unittest

from rdflib import Graph


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "src"))

from shard.domain import namespaces as ns_utils
from shard.application.ontology_catalog import parse_ontology


def graph_from_turtle(content: str) -> Graph:
    graph = Graph(bind_namespaces="none")
    graph.parse(data=content, format="turtle")
    return graph


class NamespaceDetectionTests(unittest.TestCase):
    def test_asset_namespace_uses_term_coverage(self):
        graph = graph_from_turtle(
            (ROOT_DIR / "examples" / "asset-maintenance" / "ontology.ttl").read_text(encoding="utf-8")
        )

        analysis = ns_utils.analyze_base_namespace(graph)

        self.assertEqual(analysis["namespace"], "http://example.org/asset-maintenance#")
        self.assertEqual(analysis["detected_by"], "term_coverage")
        self.assertEqual(analysis["term_count"], analysis["total_terms"])
        self.assertEqual(analysis["coverage"], 1.0)

    def test_term_namespace_wins_when_ontology_iri_uses_no_fragment(self):
        graph = graph_from_turtle(
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix ex: <https://example.org/vocab#> .
            <https://example.org/vocab> a owl:Ontology .
            ex:Asset a owl:Class .
            ex:code a owl:DatatypeProperty .
            """
        )

        self.assertEqual(ns_utils.derive_base_namespace(graph), "https://example.org/vocab#")

    def test_terms_are_counted_once_when_they_have_multiple_types(self):
        graph = graph_from_turtle(
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
            @prefix ex: <https://example.org/vocab#> .
            ex:code a owl:DatatypeProperty, rdf:Property .
            """
        )

        analysis = ns_utils.analyze_base_namespace(graph)

        self.assertEqual(analysis["term_count"], 1)
        self.assertEqual(analysis["total_terms"], 1)

    def test_candidate_ranking_reports_multiple_vocabularies(self):
        graph = graph_from_turtle(
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix a: <https://example.org/a#> .
            @prefix b: <https://example.org/b#> .
            a:One a owl:Class .
            a:Two a owl:Class .
            b:One a owl:Class .
            """
        )

        analysis = ns_utils.analyze_base_namespace(graph)

        self.assertEqual(analysis["namespace"], "https://example.org/a#")
        self.assertEqual(analysis["term_count"], 2)
        self.assertEqual(analysis["total_terms"], 3)
        self.assertEqual(len(analysis["candidates"]), 2)

    def test_empty_graph_has_no_silent_example_namespace(self):
        graph = Graph(bind_namespaces="none")

        analysis = ns_utils.analyze_base_namespace(graph)

        self.assertEqual(analysis["namespace"], "")
        self.assertEqual(analysis["detected_by"], "none")

    def test_shape_namespace_supports_hash_slash_and_urn_namespaces(self):
        self.assertEqual(
            ns_utils.shapes_namespace("https://example.org/vocab#"),
            "https://example.org/vocab/shapes/",
        )
        self.assertEqual(
            ns_utils.shapes_namespace("https://example.org/vocab/"),
            "https://example.org/vocab/shapes/",
        )
        self.assertEqual(
            ns_utils.shapes_namespace("urn:example:vocab:"),
            "urn:example:vocab:shapes:",
        )

    def test_generic_prefixes_do_not_inject_era_aliases(self):
        graph = graph_from_turtle(
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix ex: <https://example.org/vocab#> .
            ex:Asset a owl:Class .
            """
        )
        base_namespace = ns_utils.derive_base_namespace(graph)

        prefixes = ns_utils.build_prefix_block(graph, base_namespace)

        self.assertIn("@prefix ex: <https://example.org/vocab#> .", prefixes)
        self.assertNotIn("@prefix onto:", prefixes)
        self.assertNotIn("@prefix onto-sh:", prefixes)
        self.assertIn("@prefix shape: <https://example.org/vocab/shapes/> .", prefixes)
        self.assertNotIn("@prefix era:", prefixes)
        self.assertNotIn("@prefix era-sh:", prefixes)

    def test_generic_onto_alias_is_added_without_a_named_domain_prefix(self):
        graph = graph_from_turtle(
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            <https://example.org/vocab#Asset> a owl:Class .
            """
        )
        base_namespace = ns_utils.derive_base_namespace(graph)

        prefixes = ns_utils.build_prefix_block(graph, base_namespace)

        self.assertIn("@prefix onto: <https://example.org/vocab#> .", prefixes)
        self.assertIn("@prefix shape: <https://example.org/vocab/shapes/> .", prefixes)
        self.assertNotIn("@prefix onto-sh:", prefixes)

    def test_parse_service_manages_only_the_aliases_it_adds(self):
        result = parse_ontology(
            "domain.ttl",
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix domain: <https://example.org/vocab#> .
            domain:Asset a owl:Class .
            """,
        )

        self.assertEqual(result["namespace_analysis"]["managed_prefixes"], ["shape"])
        self.assertIn("@prefix domain: <https://example.org/vocab#> .", result["prefixes"])
        self.assertNotIn("@prefix onto:", result["prefixes"])
        self.assertNotIn("@prefix onto-sh:", result["prefixes"])

    def test_known_prefix_is_added_only_when_its_namespace_is_used(self):
        graph = graph_from_turtle(
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
            @prefix ex: <https://example.org/vocab#> .
            ex:geometry a owl:ObjectProperty ;
                rdfs:range <http://www.opengis.net/ont/geosparql#Geometry> .
            """
        )

        prefixes = ns_utils.build_prefix_block(graph, ns_utils.derive_base_namespace(graph))

        self.assertIn(
            "@prefix geosparql: <http://www.opengis.net/ont/geosparql#> .",
            prefixes,
        )
        self.assertNotIn("@prefix dcat:", prefixes)
        self.assertNotIn("@prefix time:", prefixes)

    def test_declared_alias_wins_over_known_prefix_name(self):
        graph = graph_from_turtle(
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
            @prefix ex: <https://example.org/vocab#> .
            @prefix geo: <http://www.opengis.net/ont/geosparql#> .
            ex:geometry a owl:ObjectProperty ; rdfs:range geo:Geometry .
            """
        )

        prefixes = ns_utils.build_prefix_block(graph, ns_utils.derive_base_namespace(graph))

        self.assertIn("@prefix geo: <http://www.opengis.net/ont/geosparql#> .", prefixes)
        self.assertNotIn("@prefix geosparql:", prefixes)

    def test_declared_shape_prefix_is_preferred_without_generic_duplicate(self):
        result = parse_ontology(
            "era.ttl",
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix era: <http://data.europa.eu/949/> .
            @prefix era-sh: <http://data.europa.eu/949/shapes/> .
            era:RunningTrack a owl:Class .
            """,
        )

        self.assertEqual(result["shape_namespace"], "http://data.europa.eu/949/shapes/")
        self.assertEqual(result["shape_prefix"], "era-sh")
        self.assertEqual(result["namespace_analysis"]["shape_prefix_source"], "declared_prefix")
        self.assertIn("@prefix era-sh: <http://data.europa.eu/949/shapes/> .", result["prefixes"])
        self.assertNotIn("@prefix shape:", result["prefixes"])

    def test_declared_onto_and_shape_prefixes_are_not_managed_or_overwritten(self):
        result = parse_ontology(
            "collision.ttl",
            """
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix ex: <https://example.org/vocab#> .
            @prefix onto: <https://example.org/imported#> .
            @prefix shape: <https://example.org/custom-shapes#> .
            ex:Asset a owl:Class .
            """,
        )

        self.assertEqual(result["base_namespace"], "https://example.org/vocab#")
        self.assertEqual(result["shape_namespace"], "https://example.org/custom-shapes#")
        self.assertIn("@prefix onto: <https://example.org/imported#> .", result["prefixes"])
        self.assertNotIn("onto", result["namespace_analysis"]["managed_prefixes"])
        self.assertNotIn("shape", result["namespace_analysis"]["managed_prefixes"])


if __name__ == "__main__":
    unittest.main()
