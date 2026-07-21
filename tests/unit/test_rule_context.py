"""Tests for role-aware, one-generation-per-rule SHACL authoring."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake import FakeListLLM
from rdflib import Graph, RDF, SH, URIRef


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.application.batch_generation import generate_batch_shapes  # noqa: E402
from shard.application.ontology_catalog import parse_ontology  # noqa: E402
from shard.application.shape_generation import (  # noqa: E402
    _normalise_target_roles,
    _parse_semantic_review,
    _target_context_text,
    build_shape,
)
from shard.application.shape_validation import (  # noqa: E402
    ontology_grounding_catalog,
    validate_shape_grounding,
)
from shard.application.target_resolution import resolve_rule_target  # noqa: E402
from shard.domain.business_rules import BusinessRule  # noqa: E402


ONTOLOGY_PATH = ROOT / "examples" / "asset-maintenance" / "ontology.ttl"
ONTOLOGY = ONTOLOGY_PATH.read_text(encoding="utf-8")


SEMANTIC_PASS = json.dumps({
    "status": "passed",
    "summary": "Every governed clause is represented.",
    "clauses": [],
    "issues": [],
})

MISSING_CERTIFICATION_REPORT = json.dumps({
    "status": "needs_correction",
    "summary": "The certification clause is missing.",
    "clauses": [{
        "path": "ex:requiresCertification",
        "cardinality": "incorrect",
        "value_constraint": "incorrect",
        "issues": [{
            "code": "MISSING_CONSTRAINED_PATH",
            "message": (
                "Add ex:requiresCertification with sh:minCount 1 and "
                "sh:class ex:Certification."
            ),
            "path": "ex:requiresCertification",
        }],
    }],
    "issues": [],
})


class RuleContextResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terms = parse_ontology("ontology.ttl", ONTOLOGY)["entities"]

    def resolve(self, targets):
        rule = BusinessRule(
            number="BR-TEST",
            title="Test rule",
            text="A test business rule.",
            source_format="interactive",
            raw="A test business rule.",
        )
        return resolve_rule_target(
            rule,
            self.terms,
            index_map={"BR-TEST": targets},
        )

    def test_property_domain_is_derived_as_focus_node(self):
        resolution = self.resolve(["ex:serialNumber"])

        self.assertEqual(resolution.focus_nodes, ["ex:Asset"])
        self.assertEqual(resolution.constraint_paths, ["ex:serialNumber"])
        self.assertEqual(resolution.related_terms, [])
        self.assertEqual(resolution.targets, ["ex:serialNumber"])

    def test_property_range_is_related_instead_of_a_second_focus(self):
        resolution = self.resolve(["ex:Inspection", "ex:inspectsAsset", "ex:Asset"])

        self.assertEqual(resolution.focus_nodes, ["ex:Inspection"])
        self.assertEqual(resolution.constraint_paths, ["ex:inspectsAsset"])
        self.assertEqual(resolution.related_terms, ["ex:Asset"])

    def test_selected_superclass_is_related_when_specific_focus_exists(self):
        resolution = self.resolve([
            "ex:CriticalAsset",
            "ex:Asset",
            "ex:hasRiskLevel",
            "ex:requiresCertification",
        ])

        self.assertEqual(resolution.focus_nodes, ["ex:CriticalAsset"])
        self.assertEqual(
            resolution.constraint_paths,
            ["ex:hasRiskLevel", "ex:requiresCertification"],
        )
        self.assertIn("ex:Asset", resolution.related_terms)


class RuleContextBatchGenerationTests(unittest.TestCase):
    def test_batch_calls_builder_once_with_complete_rule_context(self):
        batch = """
# Data Constraints

## Rule

- Number: BR-TEST
- Title: Asset identity

### Data constraint

Every Asset must have exactly one identifier and one serial number.
"""
        calls = []
        events = []

        def shape_builder(payload):
            calls.append(payload)
            return {
                "shape": """
asset-sh:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset ;
    sh:property [
        sh:path ex:assetIdentifier ;
        sh:minCount 1 ;
        sh:maxCount 1
    ] ;
    sh:property [
        sh:path ex:serialNumber ;
        sh:minCount 1 ;
        sh:maxCount 1
    ] ;
    sh:sparql [
        sh:message "Identifier and serial number must differ." ;
        sh:select "SELECT $this WHERE { $this ex:assetIdentifier ?v ; ex:serialNumber ?v . }"
    ] .
""".strip(),
                "valid": True,
                "error": None,
                "error_type": "none",
                "attempts": 1,
            }

        result = generate_batch_shapes(
            {
                "ontology_content": ONTOLOGY,
                "ontology_filename": "ontology.ttl",
                "batch_content": batch,
                "batch_filename": "rules.md",
                "index_map": {
                    "BR-TEST": ["ex:Asset", "ex:assetIdentifier", "ex:serialNumber"],
                },
                "prefixes": "\n".join([
                    "@prefix ex: <http://example.org/asset-maintenance#> .",
                    "@prefix asset-sh: <http://example.org/asset-maintenance/shapes/> .",
                    "@prefix sh: <http://www.w3.org/ns/shacl#> .",
                ]),
                "shape_namespace": "http://example.org/asset-maintenance/shapes/",
                "shape_prefix": "asset-sh",
                "wait_embeddings": False,
                "resolver_llm_fallback": False,
            },
            shape_builder=shape_builder,
            event_callback=events.append,
        )

        self.assertEqual(len(calls), 1)
        shape_events = [event for event in events if event.get("type") == "shape"]
        self.assertEqual(len(shape_events), 1)
        self.assertEqual(shape_events[0]["focus_nodes"], ["ex:Asset"])
        self.assertEqual(
            shape_events[0]["constraint_paths"],
            ["ex:assetIdentifier", "ex:serialNumber"],
        )
        roles = calls[0]["target_roles"]
        self.assertEqual([term["iri"] for term in roles["focus_nodes"]], ["ex:Asset"])
        self.assertEqual(
            [term["iri"] for term in roles["constraint_paths"]],
            ["ex:assetIdentifier", "ex:serialNumber"],
        )
        self.assertEqual(result["summary"]["generated_total"], 1)
        self.assertEqual(result["summary"]["targets_total"], 3)

        graph = Graph()
        graph.parse(data=result["shape_document"], format="turtle")
        asset_shape = URIRef("http://example.org/asset-maintenance/shapes/AssetShape")
        self.assertIn((asset_shape, RDF.type, SH.NodeShape), graph)
        self.assertEqual(len(list(graph.objects(asset_shape, SH.property))), 2)
        self.assertEqual(len(list(graph.objects(asset_shape, SH.sparql))), 1)


class RuleContextShapeBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ontology = parse_ontology("ontology.ttl", ONTOLOGY)
        cls.by_iri = {term["iri"]: term for term in cls.ontology["entities"]}

    def _critical_asset_roles(self):
        return {
            "focus_nodes": [self.by_iri["ex:CriticalAsset"]],
            "constraint_paths": [
                self.by_iri["ex:hasRiskLevel"],
                self.by_iri["ex:requiresCertification"],
            ],
            "related_terms": [
                self.by_iri["ex:RiskLevel"],
                self.by_iri["ex:Certification"],
            ],
        }

    def test_minimal_ui_roles_are_enriched_from_the_ontology_catalog(self):
        roles = _normalise_target_roles(
            {
                "target_roles": {
                    "focus_nodes": [
                        {"iri": "ex:CriticalAsset", "label": "Critical asset"},
                    ],
                    "constraint_paths": [
                        {"iri": "ex:hasRiskLevel", "label": "has risk level"},
                    ],
                    "related_terms": [
                        {"iri": "ex:RiskLevel", "label": "Risk level"},
                    ],
                },
            },
            ONTOLOGY,
            "ontology.ttl",
            None,
        )

        path = roles["constraint_paths"][0]
        self.assertEqual(path["kind"], "ObjectProperty")
        self.assertEqual(path["domain"], "ex:CriticalAsset")
        self.assertEqual(path["range"], "ex:RiskLevel")
        self.assertEqual(
            path["full_iri"],
            "http://example.org/asset-maintenance#hasRiskLevel",
        )

        context = _target_context_text(roles)
        self.assertIn("AUTHORITATIVE sh:path allowlist", context)
        self.assertIn("ex:hasRiskLevel", context)
        self.assertIn("kind=ObjectProperty", context)
        self.assertIn("domain=ex:CriticalAsset", context)
        self.assertIn("range=ex:RiskLevel", context)
        self.assertIn("value-shape evidence=sh:class ex:RiskLevel", context)
        self.assertIn("context only; not additional targets or paths", context)

    def test_generation_prompts_preserve_the_authoritative_path_contract(self):
        prompt_path = (
            ROOT
            / "src"
            / "shard"
            / "resources"
            / "prompts"
            / "rule_general.json"
        )
        prompts = json.loads(prompt_path.read_text(encoding="utf-8"))

        for prompt_name in ("generator", "generator_with_error"):
            content = "\n".join(item["content"] for item in prompts[prompt_name])
            self.assertIn("AUTHORITATIVE", content)
            self.assertIn("sh:path allowlist", content)
            self.assertIn("never substitute", content.lower())
            self.assertIn("optional", content.lower())
            self.assertIn("silently", content)

        critic = "\n".join(
            item["content"] for item in prompts["semantic_critic"]
        )
        self.assertIn("Do not rewrite the SHACL", critic)
        self.assertIn("one clause entry for every constrained property path", critic)
        self.assertIn("xsd:string", critic)
        self.assertIn("rdf:langString", critic)
        self.assertIn("rdf:PlainLiteral alone is not an equivalent", critic)
        self.assertIn("Return exactly one JSON object", critic)

        retry_critic = "\n".join(
            item["content"] for item in prompts["semantic_critic_with_error"]
        )
        self.assertIn("previous response violated", retry_critic.lower())
        self.assertIn("PREVIOUS FORMAT ERROR", retry_critic)

        corrector = "\n".join(
            item["content"] for item in prompts["semantic_corrector"]
        )
        self.assertIn("Apply every actionable issue", corrector)
        self.assertIn("do not substitute rdf:PlainLiteral", corrector)
        self.assertIn("Return only complete valid Turtle", corrector)

    def test_semantic_critic_report_is_strict_and_concise(self):
        report = _parse_semantic_review(MISSING_CERTIFICATION_REPORT)

        self.assertEqual(report["status"], "needs_correction")
        self.assertEqual(len(report["issues"]), 1)
        self.assertEqual(report["issues"][0]["code"], "MISSING_CONSTRAINED_PATH")
        self.assertEqual(report["issues"][0]["path"], "ex:requiresCertification")
        with self.assertRaisesRegex(ValueError, "issues field must be an array"):
            _parse_semantic_review('{"status":"passed","issues":"none"}')

    def test_semantic_critic_retries_an_invalid_json_contract(self):
        roles = self._critical_asset_roles()
        generated = """
shape:CriticalAssetShape a sh:NodeShape ;
    sh:targetClass ex:CriticalAsset ;
    sh:property [ sh:path ex:hasRiskLevel ; sh:class ex:RiskLevel ; sh:minCount 1 ; sh:maxCount 1 ] ;
    sh:property [ sh:path ex:requiresCertification ; sh:class ex:Certification ; sh:minCount 1 ] .
""".strip()

        with patch(
            "shard.inference.get_chat_llm",
            return_value=FakeListLLM(responses=[
                generated,
                "The candidate looks correct.",
                SEMANTIC_PASS,
            ]),
        ):
            result = build_shape({
                "business_rule": (
                    "Every CriticalAsset must have exactly one risk level and at "
                    "least one required Certification."
                ),
                "target": self.by_iri["ex:CriticalAsset"],
                "target_roles": roles,
                "_ontology_terms": self.ontology["entities"],
                "ontology_content": ONTOLOGY,
                "ontology_filename": "ontology.ttl",
                "prefixes": self.ontology["prefixes"],
                "base_namespace": self.ontology["base_namespace"],
                "shape_namespace": self.ontology["shape_namespace"],
                "shape_prefix": self.ontology["shape_prefix"],
                "model": "fake-rule-context-model",
                "validation_profiles": [],
            })

        self.assertTrue(result["valid"], result.get("error"))
        self.assertEqual(result["semantic_review"]["critic_calls"], 2)
        self.assertEqual(result["semantic_review"]["correction_count"], 0)
        self.assertEqual(result["review_attempts"], 2)

    def test_grounding_rejects_range_classes_used_as_has_value(self):
        roles = self._critical_asset_roles()
        shape = """
shape:CriticalAssetShape a sh:NodeShape ;
    sh:targetClass ex:CriticalAsset ;
    sh:property [
        sh:path ex:hasRiskLevel ;
        sh:hasValue ex:RiskLevel ;
        sh:minCount 1 ;
        sh:maxCount 1
    ] .
""".strip()
        catalog = ontology_grounding_catalog(
            ONTOLOGY,
            "ontology.ttl",
            self.by_iri["ex:CriticalAsset"],
            roles,
        )

        result = validate_shape_grounding(
            shape,
            self.ontology["prefixes"],
            ONTOLOGY,
            "ontology.ttl",
            self.by_iri["ex:CriticalAsset"],
            catalog,
            roles,
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["error_type"], "grounding")
        self.assertIn("Use sh:class ex:RiskLevel", result["error"])

    def test_builder_retries_range_class_has_value_as_sh_class(self):
        roles = self._critical_asset_roles()
        invalid = """
shape:CriticalAssetShape a sh:NodeShape ;
    sh:targetClass ex:CriticalAsset ;
    sh:property [ sh:path ex:hasRiskLevel ; sh:hasValue ex:RiskLevel ; sh:minCount 1 ; sh:maxCount 1 ] .
""".strip()
        corrected = """
shape:CriticalAssetShape a sh:NodeShape ;
    sh:targetClass ex:CriticalAsset ;
    sh:property [ sh:path ex:hasRiskLevel ; sh:class ex:RiskLevel ; sh:minCount 1 ; sh:maxCount 1 ; sh:message "Exactly one risk level is required." ; sh:severity sh:Violation ] ;
    sh:property [ sh:path ex:requiresCertification ; sh:class ex:Certification ; sh:minCount 1 ; sh:message "At least one certification is required." ; sh:severity sh:Violation ] .
""".strip()

        with patch(
            "shard.inference.get_chat_llm",
            return_value=FakeListLLM(responses=[invalid, corrected, SEMANTIC_PASS]),
        ):
            result = build_shape({
                "business_rule": "Every CriticalAsset must have exactly one risk level and at least one required Certification.",
                "target": self.by_iri["ex:CriticalAsset"],
                "target_roles": roles,
                "_ontology_terms": self.ontology["entities"],
                "ontology_content": ONTOLOGY,
                "ontology_filename": "ontology.ttl",
                "prefixes": self.ontology["prefixes"],
                "base_namespace": self.ontology["base_namespace"],
                "shape_namespace": self.ontology["shape_namespace"],
                "shape_prefix": self.ontology["shape_prefix"],
                "model": "fake-rule-context-model",
                "validation_profiles": [],
            })

        self.assertTrue(result["valid"], result.get("error"))
        self.assertEqual(result["attempts"], 2)
        self.assertTrue(result["llm_review_applied"])
        self.assertEqual(result["review_attempts"], 1)
        self.assertIn("sh:class ex:RiskLevel", result["shape"])
        self.assertIn("sh:class ex:Certification", result["shape"])

    def test_critic_and_corrector_repair_a_valid_but_incomplete_shape(self):
        roles = self._critical_asset_roles()
        incomplete = """
shape:CriticalAssetShape a sh:NodeShape ;
    sh:targetClass ex:CriticalAsset ;
    sh:property [
        sh:path ex:hasRiskLevel ;
        sh:class ex:RiskLevel ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:message "Exactly one risk level is required." ;
        sh:severity sh:Violation
    ] .
""".strip()
        reviewed = """
shape:CriticalAssetShape a sh:NodeShape ;
    sh:targetClass ex:CriticalAsset ;
    sh:property [
        sh:path ex:hasRiskLevel ;
        sh:class ex:RiskLevel ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:message "Exactly one risk level is required." ;
        sh:severity sh:Violation
    ] ;
    sh:property [
        sh:path ex:requiresCertification ;
        sh:class ex:Certification ;
        sh:minCount 1 ;
        sh:message "At least one certification is required." ;
        sh:severity sh:Violation
    ] .
""".strip()

        with patch(
            "shard.inference.get_chat_llm",
            return_value=FakeListLLM(responses=[
                incomplete,
                MISSING_CERTIFICATION_REPORT,
                "[ invalid Turtle",
                reviewed,
                SEMANTIC_PASS,
            ]),
        ):
            result = build_shape({
                "business_rule": (
                    "Every CriticalAsset must have exactly one risk level and at "
                    "least one required Certification."
                ),
                "target": self.by_iri["ex:CriticalAsset"],
                "target_roles": roles,
                "_ontology_terms": self.ontology["entities"],
                "ontology_content": ONTOLOGY,
                "ontology_filename": "ontology.ttl",
                "prefixes": self.ontology["prefixes"],
                "base_namespace": self.ontology["base_namespace"],
                "shape_namespace": self.ontology["shape_namespace"],
                "shape_prefix": self.ontology["shape_prefix"],
                "model": "fake-rule-context-model",
                "validation_profiles": [],
            })

        self.assertTrue(result["valid"], result.get("error"))
        self.assertTrue(result["llm_review_applied"])
        self.assertEqual(result["review_attempts"], 4)
        self.assertEqual(result["semantic_review"]["status"], "passed")
        self.assertEqual(result["semantic_review"]["critic_calls"], 2)
        self.assertEqual(result["semantic_review"]["correction_count"], 2)
        self.assertEqual(result["semantic_review"]["issues_found"], 2)
        self.assertIn("sh:path ex:requiresCertification", result["shape"])

    def test_builder_accepts_multiple_paths_in_one_rule_context(self):
        ontology = self.ontology
        by_iri = self.by_iri
        generated = """
shape:AssetIdentityShape a sh:NodeShape ;
    sh:targetClass ex:Asset ;
    sh:property [
        sh:path ex:assetIdentifier ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:message "Each asset needs exactly one identifier." ;
        sh:severity sh:Violation
    ] ;
    sh:property [
        sh:path ex:serialNumber ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:message "Each asset needs exactly one serial number." ;
        sh:severity sh:Violation
    ] .
""".strip()
        target_roles = {
            "focus_nodes": [by_iri["ex:Asset"]],
            "constraint_paths": [
                by_iri["ex:assetIdentifier"],
                by_iri["ex:serialNumber"],
            ],
            "related_terms": [],
        }

        with patch(
            "shard.inference.get_chat_llm",
            return_value=FakeListLLM(responses=[generated, SEMANTIC_PASS]),
        ):
            result = build_shape({
                "business_rule": (
                    "Every Asset must have exactly one identifier and one serial number."
                ),
                "target": by_iri["ex:Asset"],
                "target_roles": target_roles,
                "_ontology_terms": ontology["entities"],
                "ontology_content": ONTOLOGY,
                "ontology_filename": "ontology.ttl",
                "prefixes": ontology["prefixes"],
                "base_namespace": ontology["base_namespace"],
                "shape_namespace": ontology["shape_namespace"],
                "shape_prefix": ontology["shape_prefix"],
                "model": "fake-rule-context-model",
                "validation_profiles": [],
            })

        self.assertTrue(result["valid"], result.get("error"))
        self.assertEqual(
            [term["iri"] for term in result["target_roles"]["constraint_paths"]],
            ["ex:assetIdentifier", "ex:serialNumber"],
        )
        graph = Graph()
        graph.parse(data=f"{ontology['prefixes']}\n{result['shape']}", format="turtle")
        self.assertEqual(len(list(graph.objects(None, SH.property))), 2)


if __name__ == "__main__":
    unittest.main()
