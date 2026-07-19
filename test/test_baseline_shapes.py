"""Tests for target-focused Astrea evidence and final SHACL merges."""

from pathlib import Path
import sys
import unittest

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, SH


ROOT_DIR = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT_DIR / "text2shacl_core"
SERVICES_DIR = ROOT_DIR / "services"
for directory in (CORE_DIR, SERVICES_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from baseline_shapes import (
    focused_baseline_for_target,
    merge_shape_documents,
    parse_baseline_shapes,
)
from build_shacl_shapes import merge_shapes


PREFIXES = """
@prefix ast: <http://example.org/astrea/> .
@prefix ex: <http://example.org/domain#> .
@prefix gen: <http://example.org/generated/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""

ASTREA = PREFIXES + """
ast:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset ;
    sh:property [
        sh:path ex:serialNumber ;
        sh:minCount 0 ;
        sh:maxCount 2 ;
        sh:datatype xsd:string
    ] ;
    sh:property [
        sh:path ex:manufacturer ;
        sh:minCount 1
    ] .

ast:DueDateShape a sh:PropertyShape ;
    sh:targetClass ex:Task ;
    sh:path ex:dueDate ;
    sh:minCount 0 .

ast:SiteShape a sh:NodeShape ;
    sh:targetClass ex:Site ;
    sh:closed true .
"""

GENERATED = PREFIXES + """
gen:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset ;
    sh:property [
        sh:path ex:serialNumber ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string
    ] ;
    sh:property [
        sh:path ex:dueDate ;
        sh:minCount 1
    ] .

gen:DueDateShape a sh:PropertyShape ;
    sh:targetClass ex:Task ;
    sh:path ex:dueDate ;
    sh:minCount 1 .
"""


EX = "http://example.org/domain#"


class BaselineEvidenceTests(unittest.TestCase):
    def test_property_evidence_keeps_owner_but_excludes_sibling_paths(self):
        graph = parse_baseline_shapes(ASTREA)
        focused = focused_baseline_for_target(
            graph,
            {
                "type": "property",
                "full_iri": f"{EX}serialNumber",
            },
        )
        result = Graph().parse(data=focused, format="turtle")

        self.assertIn(
            URIRef(f"{EX}serialNumber"),
            set(result.objects(None, SH.path)),
        )
        self.assertNotIn(
            URIRef(f"{EX}manufacturer"),
            set(result.objects(None, SH.path)),
        )
        self.assertIn(
            URIRef(f"{EX}Asset"),
            set(result.objects(None, SH.targetClass)),
        )

    def test_class_evidence_keeps_the_complete_matching_node_shape(self):
        graph = parse_baseline_shapes(ASTREA)
        focused = focused_baseline_for_target(
            graph,
            {"type": "class", "full_iri": f"{EX}Asset"},
        )
        result = Graph().parse(data=focused, format="turtle")

        self.assertEqual(
            set(result.objects(None, SH.path)),
            {URIRef(f"{EX}serialNumber"), URIRef(f"{EX}manufacturer")},
        )


class BaselineMergeTests(unittest.TestCase):
    def test_priority_llm_uses_astrea_only_for_uncovered_targets(self):
        merged = merge_shape_documents(ASTREA, GENERATED, "priority-llm")
        graph = Graph().parse(data=merged["shape_document"], format="turtle")

        self.assertIn(URIRef(f"{EX}Site"), set(graph.objects(None, SH.targetClass)))
        self.assertNotIn(URIRef(f"{EX}manufacturer"), set(graph.objects(None, SH.path)))
        due_shapes = list(graph.subjects(SH.path, URIRef(f"{EX}dueDate")))
        self.assertTrue(due_shapes)
        self.assertEqual(set(graph.objects(due_shapes[0], SH.minCount)), {Literal(1)})

    def test_priority_llm_treats_nested_generated_paths_as_covered(self):
        standalone_astrea = PREFIXES + """
ast:SerialShape a sh:PropertyShape ;
    sh:path ex:serialNumber ;
    sh:minCount 0 .
"""
        nested_generated = PREFIXES + """
gen:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset ;
    sh:property [
        sh:path ex:serialNumber ;
        sh:minCount 1
    ] .
"""
        merged = merge_shape_documents(
            standalone_astrea,
            nested_generated,
            "priority-llm",
        )
        graph = Graph().parse(data=merged["shape_document"], format="turtle")

        self.assertEqual(
            len(list(graph.subjects(SH.path, URIRef(f"{EX}serialNumber")))),
            1,
        )
        self.assertEqual(merged["stats"]["astrea_fallback_paths"], 0)

    def test_restrictive_merges_nested_property_constraints_by_path(self):
        merged = merge_shape_documents(ASTREA, GENERATED, "restrictive")
        graph = Graph().parse(data=merged["shape_document"], format="turtle")
        asset_shape = next(graph.subjects(SH.targetClass, URIRef(f"{EX}Asset")))
        properties = {
            graph.value(node, SH.path): node
            for node in graph.objects(asset_shape, SH.property)
        }

        self.assertEqual(
            set(properties),
            {
                URIRef(f"{EX}serialNumber"),
                URIRef(f"{EX}manufacturer"),
                URIRef(f"{EX}dueDate"),
            },
        )
        serial = properties[URIRef(f"{EX}serialNumber")]
        self.assertEqual(graph.value(serial, SH.minCount), Literal(1))
        self.assertEqual(graph.value(serial, SH.maxCount), Literal(1))
        self.assertEqual(graph.value(serial, SH.datatype), URIRef("http://www.w3.org/2001/XMLSchema#string"))
        self.assertTrue(any(graph.triples((None, RDF.type, SH.NodeShape))))

    def test_merge_service_validates_each_strategy_against_generic_meta_shapes(self):
        for technique in ("priority-llm", "restrictive"):
            with self.subTest(technique=technique):
                result = merge_shapes({
                    "generated_shapes": GENERATED,
                    "astrea_baseline": {
                        "name": "astrea.ttl",
                        "content": ASTREA,
                    },
                    "technique": technique,
                    "validation_profiles": [],
                })

                self.assertTrue(result["valid"], result.get("report_text"))
                self.assertTrue(result["syntax_valid"])
                self.assertTrue(result["generic_profile_active"])
                self.assertEqual(result["validation_level"], "syntax+generic")
                self.assertEqual(result["technique"], technique)


if __name__ == "__main__":
    unittest.main()
