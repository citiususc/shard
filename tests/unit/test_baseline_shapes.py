"""Tests for target-focused Astrea evidence and final SHACL merges."""

from pathlib import Path
import sys
import unittest

from rdflib import Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, SH


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "src"))

from shard.baselines import (
    baseline_context_for_target,
    focused_baseline_for_target,
    merge_shape_documents,
    parse_baseline_shapes,
)
from shard.application.shape_merge import merge_shapes


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
    def test_final_merge_only_mode_does_not_expose_baseline_as_llm_evidence(self):
        target = {"type": "property", "full_iri": f"{EX}serialNumber"}
        payload = {
            "astrea_baseline": {"name": "astrea.ttl", "content": ASTREA},
            "astrea_use_mode": "merge",
        }
        self.assertEqual(baseline_context_for_target(payload, target), "")
        payload["astrea_use_mode"] = "both"
        self.assertIn("serialNumber", baseline_context_for_target(payload, target))

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
                expected = "generated-priority" if technique == "priority-llm" else technique
                self.assertEqual(result["merge_strategy"], expected)

    def test_restrictive_combines_bounds_enumerations_and_type_constraints(self):
        astrea = PREFIXES + """
ast:CodeShape a sh:PropertyShape ;
    sh:path ex:code ; sh:minCount 1 ; sh:maxCount 5 ;
    sh:minInclusive 0 ; sh:maxInclusive 100 ;
    sh:datatype xsd:string ; sh:class ex:LegacyCode ;
    sh:in ("A" "B") ; sh:message "Astrea message" .
"""
        generated = PREFIXES + """
gen:CodeShape a sh:PropertyShape ;
    sh:path ex:code ; sh:minCount 2 ; sh:maxCount 3 ;
    sh:minInclusive 10 ; sh:maxInclusive 90 ;
    sh:datatype xsd:integer ; sh:class ex:CurrentCode ;
    sh:in ("B" "C") ; sh:message "Generated message" .
"""
        merged = merge_shape_documents(astrea, generated, "restrictive")
        graph = Graph().parse(data=merged["shape_document"], format="turtle")
        shape = next(graph.subjects(SH.path, URIRef(f"{EX}code")))

        self.assertEqual(graph.value(shape, SH.minCount), Literal(2))
        self.assertEqual(graph.value(shape, SH.maxCount), Literal(3))
        self.assertEqual(graph.value(shape, SH.minInclusive), Literal(10))
        self.assertEqual(graph.value(shape, SH.maxInclusive), Literal(90))
        self.assertEqual(graph.value(shape, SH.datatype), URIRef("http://www.w3.org/2001/XMLSchema#integer"))
        self.assertEqual(graph.value(shape, SH["class"]), URIRef(f"{EX}CurrentCode"))
        self.assertEqual(graph.value(shape, SH.message), Literal("Generated message"))
        self.assertEqual(
            set(Collection(graph, graph.value(shape, SH["in"]))),
            {Literal("B")},
        )
        self.assertTrue(any("conflicting values" in warning for warning in merged["warnings"]))

    def test_restrictive_reports_incompatible_cardinality_and_numeric_bounds(self):
        astrea = PREFIXES + """
ast:ValueShape a sh:PropertyShape ;
    sh:path ex:value ; sh:maxCount 2 ; sh:maxExclusive 5 .
"""
        generated = PREFIXES + """
gen:ValueShape a sh:PropertyShape ;
    sh:path ex:value ; sh:minCount 4 ; sh:minExclusive 10 .
"""
        merged = merge_shape_documents(astrea, generated, "restrictive")
        warnings = "\n".join(merged["warnings"])
        self.assertIn("minCount", warnings)
        self.assertIn("maxCount", warnings)
        self.assertIn("minExclusive", warnings)
        self.assertIn("maxExclusive", warnings)

    def test_restrictive_handles_logical_constraints_and_preserves_generated_metadata(self):
        astrea = PREFIXES + """
@prefix ast-extra: <http://example.org/astrea/extra/> .
ast:LogicalShape a sh:PropertyShape ;
    sh:path ex:choice ;
    sh:and (ast-extra:A ast-extra:B) ;
    sh:or (ast-extra:A ast-extra:B) ;
    sh:not ast-extra:A ;
    sh:name "Astrea name" ; sh:message "Astrea message" .
"""
        generated = PREFIXES + """
@prefix gen-extra: <http://example.org/generated/extra/> .
gen:LogicalShape a sh:PropertyShape ;
    sh:path ex:choice ;
    sh:and (gen-extra:B gen-extra:C) ;
    sh:or (gen-extra:B gen-extra:C) ;
    sh:not gen-extra:C ;
    sh:name "Generated name" ; sh:message "Generated message" .
"""
        merged = merge_shape_documents(astrea, generated, "restrictive")
        graph = Graph().parse(data=merged["shape_document"], format="turtle")
        shape = next(graph.subjects(SH.path, URIRef(f"{EX}choice")))

        and_members = set(Collection(graph, graph.value(shape, SH["and"])))
        self.assertEqual(len(and_members), 4)
        or_members = set(Collection(graph, graph.value(shape, SH["or"])))
        self.assertEqual(
            or_members,
            {
                URIRef("http://example.org/generated/extra/B"),
                URIRef("http://example.org/generated/extra/C"),
            },
        )
        self.assertEqual(
            graph.value(shape, SH["not"]),
            URIRef("http://example.org/generated/extra/C"),
        )
        self.assertEqual(graph.value(shape, SH.name), Literal("Generated name"))
        self.assertEqual(graph.value(shape, SH.message), Literal("Generated message"))
        self.assertGreaterEqual(
            sum("logical constraints" in warning for warning in merged["warnings"]),
            2,
        )
        namespaces = {prefix: str(namespace) for prefix, namespace in graph.namespaces()}
        self.assertEqual(namespaces["ast-extra"], "http://example.org/astrea/extra/")
        self.assertEqual(namespaces["gen-extra"], "http://example.org/generated/extra/")


if __name__ == "__main__":
    unittest.main()
