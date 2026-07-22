"""Regression tests for safe Astrea baseline normalization and partitioning."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from rdflib import BNode, Graph
from rdflib import Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, SH, XSD

from shard.api.models import AstreaBaselineResponse, AstreaNormalization, BaselineInput
from shard.application.baseline_generation import generate_astrea_baseline
from shard.application.shape_validation import validate_shape_content
from shard.baselines import baseline_from_payload, normalize_astrea_graph


ONTOLOGY = """
@prefix ex: <http://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Ontology a owl:Ontology .
ex:Thing a owl:Class .
ex:otherThing a owl:Class .
ex:value a owl:DatatypeProperty ; rdfs:domain ex:Thing ; rdfs:range rdfs:Literal .
"""


REPAIRABLE_BASELINE = """
@prefix ex: <http://example.org/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:ThingShape a sh:NodeShape ;
    sh:targetClass ex:Thing ;
    sh:property [
        sh:path ex:value ;
        sh:minCount "1"^^xsd:nonNegativeInteger, "2.0"^^xsd:decimal ;
        sh:maxCount "5"^^xsd:nonNegativeInteger, "3"^^xsd:int ;
        sh:nodeKind sh:IRI, sh:BlankNode ;
        sh:pattern "alpha", "beta" ;
        sh:flags "i", "m" ;
        sh:datatype xsd:string, rdf:langString ;
        sh:in ( "alpha" "beta" ), ( "beta" "gamma" ) ;
        sh:languageIn ( "en" "es"@es ) ;
        sh:message "7"^^xsd:integer ;
        sh:uniqueLang "true"
    ] .
"""


MIXED_BASELINE = REPAIRABLE_BASELINE + """
ex:OtherShape a sh:NodeShape ;
    sh:targetClass ex:otherThing ;
    sh:property [ sh:minCount "1"^^xsd:nonNegativeInteger ] .
"""


class AstreaNormalizationTests(unittest.TestCase):
    def test_repairs_supported_shacl_for_shacl_violations(self) -> None:
        graph = Graph().parse(data=REPAIRABLE_BASELINE, format="turtle")

        statistics = normalize_astrea_graph(graph)
        validation = validate_shape_content(graph.serialize(format="turtle"), "", [])

        self.assertTrue(validation["valid"], validation.get("report_text"))
        self.assertEqual(statistics["normalized_shapes"], 1)
        self.assertEqual(statistics["integer_literals_normalized"], 4)
        self.assertEqual(statistics["numeric_parameters_collapsed"], 2)
        self.assertEqual(statistics["list_parameters_merged"], 1)
        self.assertEqual(statistics["datatype_parameters_conjoined"], 1)
        self.assertEqual(statistics["pattern_parameters_conjoined"], 1)
        self.assertEqual(
            set(graph.objects(None, SH.nodeKind)),
            {SH.BlankNodeOrIRI},
        )
        self.assertEqual(
            {value.datatype for value in graph.objects(None, SH.minCount)},
            {XSD.integer},
        )

    def test_qualified_value_shapes_are_conjoined_within_one_parameter(self) -> None:
        graph = Graph().parse(
            data="""
                @prefix ex: <http://example.org/> .
                @prefix sh: <http://www.w3.org/ns/shacl#> .
                ex:S a sh:NodeShape ; sh:targetClass ex:C ; sh:property [
                    sh:path ex:p ;
                    sh:qualifiedValueShape [ sh:class ex:A ], [ sh:class ex:B ] ;
                    sh:qualifiedMinCount 1
                ] .
            """,
            format="turtle",
        )

        statistics = normalize_astrea_graph(graph)
        validation = validate_shape_content(graph.serialize(format="turtle"), "", [])

        self.assertTrue(validation["valid"], validation.get("report_text"))
        self.assertEqual(statistics["qualified_shapes_conjoined"], 1)
        property_shape = next(graph.objects(None, SH.property))
        qualified_shapes = list(graph.objects(property_shape, SH.qualifiedValueShape))
        self.assertEqual(len(qualified_shapes), 1)
        self.assertIsNotNone(graph.value(qualified_shapes[0], SH["and"]))

    def test_collapses_boolean_list_severity_and_shape_type_parameters(self) -> None:
        graph = Graph().parse(
            data="""
                @prefix ex: <http://example.org/> .
                @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
                @prefix sh: <http://www.w3.org/ns/shacl#> .
                ex:S a sh:NodeShape, sh:PropertyShape ;
                    sh:path ex:p ;
                    sh:closed true, false ;
                    sh:deactivated true, false ;
                    sh:severity sh:Info, sh:Violation ;
                    sh:ignoredProperties ( rdf:type ), ( ex:q ) ;
                    sh:languageIn ( "en" "fr" ), ( "fr" "de" ) .
            """,
            format="turtle",
        )

        statistics = normalize_astrea_graph(graph)
        validation = validate_shape_content(graph.serialize(format="turtle"), "", [])
        shape = URIRef("http://example.org/S")

        self.assertTrue(validation["valid"], validation.get("report_text"))
        self.assertEqual(set(graph.objects(shape, RDF.type)), {SH.PropertyShape})
        self.assertEqual(graph.value(shape, SH.closed), Literal(True, datatype=XSD.boolean))
        self.assertEqual(
            graph.value(shape, SH.deactivated),
            Literal(False, datatype=XSD.boolean),
        )
        self.assertEqual(graph.value(shape, SH.severity), SH.Violation)
        ignored = set(Collection(graph, graph.value(shape, SH.ignoredProperties)))
        self.assertEqual(ignored, {RDF.type, URIRef("http://example.org/q")})
        languages = list(Collection(graph, graph.value(shape, SH.languageIn)))
        self.assertEqual(languages, [Literal("fr", datatype=XSD.string)])
        self.assertEqual(statistics["boolean_parameters_collapsed"], 2)
        self.assertEqual(statistics["list_parameters_merged"], 2)
        self.assertEqual(statistics["severity_parameters_collapsed"], 1)
        self.assertEqual(statistics["shape_types_repaired"], 1)

    def test_removes_a_vacuous_union_of_all_standard_node_kinds(self) -> None:
        graph = Graph().parse(
            data="""
                @prefix ex: <http://example.org/> .
                @prefix sh: <http://www.w3.org/ns/shacl#> .
                ex:S a sh:PropertyShape ; sh:path ex:p ;
                    sh:nodeKind sh:BlankNode, sh:IRI, sh:Literal,
                        sh:BlankNodeOrIRI, sh:BlankNodeOrLiteral, sh:IRIOrLiteral .
            """,
            format="turtle",
        )

        statistics = normalize_astrea_graph(graph)
        validation = validate_shape_content(graph.serialize(format="turtle"), "", [])

        self.assertTrue(validation["valid"], validation.get("report_text"))
        self.assertEqual(list(graph.objects(None, SH.nodeKind)), [])
        self.assertEqual(statistics["normalized_shapes"], 1)
        self.assertEqual(statistics["unrestricted_shapes"], 1)

    def test_linearizes_malformed_lists_without_losing_members(self) -> None:
        graph = Graph().parse(
            data="""
                @prefix ex: <http://example.org/> .
                @prefix sh: <http://www.w3.org/ns/shacl#> .
                ex:S a sh:NodeShape ; sh:targetClass ex:C .
            """,
            format="turtle",
        )
        shape = URIRef("http://example.org/S")
        head = BNode()
        alternatives = []
        for class_name in ("A", "B", "C"):
            alternative = BNode()
            graph.add(
                (alternative, SH["class"], URIRef(f"http://example.org/{class_name}"))
            )
            graph.add((head, RDF.first, alternative))
            alternatives.append(alternative)
        graph.add((head, RDF.rest, RDF.nil))
        graph.add((shape, SH["or"], head))

        statistics = normalize_astrea_graph(graph)
        validation = validate_shape_content(
            graph.serialize(format="turtle"), "", [], inference="none"
        )

        self.assertTrue(validation["valid"], validation.get("report_text"))
        repaired_members = list(Collection(graph, head))
        self.assertEqual(len(repaired_members), len(alternatives))
        self.assertEqual(set(repaired_members), set(alternatives))
        self.assertEqual(statistics["malformed_list_nodes_repaired"], 1)
        self.assertEqual(statistics["malformed_list_members_preserved"], 3)

    @patch("shard.application.baseline_generation.generate_astrea_shapes")
    def test_generation_preserves_evidence_and_quarantines_only_rejects(
        self,
        generate_astrea_shapes_mock,
    ) -> None:
        generate_astrea_shapes_mock.return_value = {
            "shape_document": MIXED_BASELINE,
            "shape_count": 2,
            "node_shape_count": 2,
            "property_shape_count": 2,
            "partial": False,
        }

        result = generate_astrea_baseline(
            {"ontology_content": ONTOLOGY, "ontology_filename": "ontology.ttl"}
        )

        self.assertTrue(result["available"])
        self.assertFalse(result["validation"]["valid"])
        self.assertTrue(result["merge_safe"])
        self.assertTrue(result["merge_validation"]["valid"])
        self.assertTrue(result["quarantined_shape_document"].strip())
        self.assertGreaterEqual(result["normalization"]["quarantined_shapes"], 1)
        self.assertIn("otherThing", result["shape_document"])
        self.assertIn("ThingShape", result["merge_shape_document"])

    def test_payload_selects_merge_document_without_breaking_legacy_inputs(self) -> None:
        payload = {
            "astrea_baseline": {
                "name": "astrea.ttl",
                "content": "full evidence",
                "merge_content": "safe subset",
            }
        }
        self.assertEqual(baseline_from_payload(payload), ("full evidence", "astrea.ttl"))
        self.assertEqual(
            baseline_from_payload(payload, purpose="merge"),
            ("safe subset", "astrea.ttl"),
        )
        legacy = {"astrea_baseline": {"name": "old.ttl", "content": "legacy"}}
        self.assertEqual(
            baseline_from_payload(legacy, purpose="merge"),
            ("legacy", "old.ttl"),
        )
        unavailable = {
            "astrea_baseline": {
                "name": "guarded.ttl",
                "content": "evidence only",
                "merge_content": "",
            }
        }
        self.assertEqual(
            baseline_from_payload(unavailable, purpose="merge"),
            ("", "guarded.ttl"),
        )

    def test_public_models_expose_the_normalization_contract(self) -> None:
        baseline_schema = BaselineInput.model_json_schema()
        response_schema = AstreaBaselineResponse.model_json_schema()
        normalization_schema = AstreaNormalization.model_json_schema()

        self.assertIn("merge_content", baseline_schema["properties"])
        self.assertIn("merge_shape_document", response_schema["properties"])
        self.assertIn("quarantined_shape_document", response_schema["properties"])
        self.assertIn("merge_validation", response_schema["properties"])
        self.assertIn("quarantined_shapes", normalization_schema["properties"])
        self.assertIn("integer_literals_normalized", normalization_schema["properties"])
        self.assertIn(
            "malformed_list_nodes_repaired", normalization_schema["properties"]
        )


if __name__ == "__main__":
    unittest.main()
