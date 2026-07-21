"""Contract tests for manifest-driven, preloaded SHARD sessions."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

from rdflib import Graph, URIRef


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.domain.business_rules import BusinessRule, parse_business_rules_document  # noqa: E402
from shard.application.shape_generation import (  # noqa: E402
    _normalise_target_roles,
    _target_context_text,
)
from shard.application.shape_validation import (  # noqa: E402
    ontology_grounding_catalog,
    validate_shape_content,
    validate_shape_grounding,
)
from shard.application.target_resolution import resolve_rule_target  # noqa: E402


EXAMPLES = ROOT / "frontend" / "examples"
EXPECTED_BATCH_IDS = {
    "EPO-1", "EPO-5", "EPO-8", "EPO-12", "EPO-19",
    "EPO-22", "EPO-25", "EPO-32", "EPO-40", "EPO-50",
}
EXPECTED_PATHS = {
    "EPO-1": {"concernsProcedure", "refersToNotice", "specifiesProcurementCriterion"},
    "EPO-5": {"containsCandidate", "hasStartDate"},
    "EPO-8": {"concernsLot", "concernsProcedure", "isCompetitionTerminated", "isDPSTerminated", "isToBeRelaunched"},
    "EPO-12": {"foreseesSubcontractor", "hasSubcontractor", "needsToBeAWinner"},
    "EPO-19": {"hasStatus", "isSpecificToOrderResponseLine"},
    "EPO-22": {"hasAdditionalInformation", "hasAssociatedDocument", "specifiesItem"},
    "EPO-25": {"concernsReviewDecision", "concernsReviewRequest", "hasElementReference", "relatesToEFormSectionIdentifier"},
    "EPO-32": {"hasOversupplyQuantity", "hasReceivedQuantity", "hasRejectedQuantity", "hasShortQuantity", "isSubmittedForDespatchLine"},
    "EPO-40": {"hasBatchID", "hasBestBeforeDate", "hasExpiryDate", "hasManufactureDate", "isProductionOf"},
    "EPO-50": {"hasQualificationCriteriaStatedInESPDRequest", "hasQualificationCriteriaStatedInNotice", "hasQualificationCriteriaStatedInProcurementDocuments", "includesNationalCriterion"},
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def secret_keys(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if any(marker in key.lower() for marker in ("token", "secret", "password", "api_key")):
                found.append(key)
            found.extend(secret_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(secret_keys(child))
    return found


class PreloadedExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_json(EXAMPLES / "manifest.json")
        cls.sessions = {
            entry["workflow"]: load_json(EXAMPLES / entry["session"])
            for entry in cls.manifest["examples"]
        }

    def test_manifest_is_extensible_and_references_relative_session_assets(self):
        self.assertEqual(self.manifest["version"], 1)
        self.assertEqual({entry["workflow"] for entry in self.manifest["examples"]}, {"rule", "batch"})
        for entry in self.manifest["examples"]:
            with self.subTest(example=entry["id"]):
                self.assertFalse(Path(entry["session"]).is_absolute())
                self.assertTrue((EXAMPLES / entry["session"]).is_file())

    def test_sessions_use_version_three_and_contain_no_credentials(self):
        for workflow, session in self.sessions.items():
            with self.subTest(workflow=workflow):
                self.assertEqual(session["application"], "SHARD")
                self.assertEqual(session["version"], 3)
                self.assertEqual(session["workspace"]["workflow"], workflow)
                self.assertEqual(secret_keys(session), [])
                self.assertEqual(session["models"]["llmModel"], "")
                self.assertEqual(session["models"]["embeddingModel"], "")
                self.assertEqual(session["accepted"], [])
                self.assertEqual(session["shapeValidationProfiles"], [])

    def test_embedded_epo_ontology_is_parseable_and_namespace_complete(self):
        ontologies = [session["ontology"] for session in self.sessions.values()]
        self.assertEqual(ontologies[0], ontologies[1])
        ontology = ontologies[0]
        graph = Graph().parse(data=ontology["content"], format="turtle")
        self.assertGreater(len(graph), 1000)
        self.assertEqual(ontology["baseNamespace"], "http://data.europa.eu/a4g/ontology#")
        self.assertEqual(ontology["ontologyPrefix"], "epo")
        self.assertEqual(ontology["shapeNamespace"], "http://data.europa.eu/a4g/data-shape#")
        self.assertEqual(ontology["shapePrefix"], "epo-shape")
        self.assertIn("@prefix epo:", ontology["prefixes"])
        self.assertIn("@prefix epo-shape:", ontology["prefixes"])
        digest = hashlib.sha256(ontology["content"].encode("utf-8")).hexdigest()
        self.assertEqual(ontology["contentHash"], digest)
        self.assertGreaterEqual(len(ontology["entities"]), 1200)

    def test_rule_example_contains_one_representative_constraint(self):
        workspace = self.sessions["rule"]["workspace"]
        constraint = workspace["dataConstraint"]
        self.assertEqual(constraint["number"], "EPO-6")
        self.assertIn("exactly one environmental emission code", constraint["text"])
        self.assertIn("language-tagged string", constraint["text"])
        self.assertIn("has Environmental Emission Code", constraint["title"])
        self.assertTrue(workspace["domainContext"])
        self.assertTrue(workspace["generationGuidance"])

    def test_external_object_range_is_grounded_without_relaxing_unknown_iris(self):
        ontology = self.sessions["rule"]["ontology"]
        target = next(
            term for term in ontology["entities"]
            if term["iri"] == "epo:EnvironmentalEmissionInformation"
        )
        catalog = ontology_grounding_catalog(
            ontology["content"], ontology["filename"], target,
        )
        skos_concept = URIRef("http://www.w3.org/2004/02/skos/core#Concept")
        self.assertIn(skos_concept, catalog["referenced_classes"])
        self.assertIn(skos_concept, catalog["valid_classes"])

        shape = """
epo-shape:EnvironmentalEmissionInformationShape a sh:NodeShape ;
    sh:targetClass epo:EnvironmentalEmissionInformation ;
    sh:property [
        sh:path epo:hasEnvironmentalEmissionCode ;
        sh:class skos:Concept ;
        sh:nodeKind sh:IRI ;
        sh:minCount 1 ;
        sh:maxCount 1
    ] , [
        sh:path epo:hasMeasure ;
        sh:class epo:Quantity ;
        sh:nodeKind sh:IRI ;
        sh:minCount 1 ;
        sh:maxCount 1
    ] , [
        sh:path epo:concernsItem ;
        sh:class epo:AbstractItem ;
        sh:nodeKind sh:IRI ;
        sh:maxCount 1
    ] , [
        sh:path epo:concernsTransportMeans ;
        sh:class epo:TransportMeans ;
        sh:nodeKind sh:IRI ;
        sh:maxCount 1
    ] , [
        sh:path epo:hasCalculationMethod ;
        sh:nodeKind sh:Literal ;
        sh:maxCount 1 ;
        sh:or ( [ sh:datatype xsd:string ] [ sh:datatype rdf:langString ] )
    ] .
""".strip()
        valid = validate_shape_grounding(
            shape,
            ontology["prefixes"],
            ontology["content"],
            ontology["filename"],
            target,
            catalog,
        )
        self.assertTrue(valid["valid"], valid.get("error"))
        profile = validate_shape_content(shape, ontology["prefixes"], [])
        self.assertTrue(profile["valid"], profile.get("error"))
        self.assertTrue(profile["generic_profile_active"])

        invented = shape.replace(
            "skos:Concept", "<https://example.invalid/UnknownConcept>"
        )
        invalid = validate_shape_grounding(
            invented,
            ontology["prefixes"],
            ontology["content"],
            ontology["filename"],
            target,
            catalog,
        )
        self.assertFalse(invalid["valid"])
        self.assertIn("UnknownConcept", invalid["error"])

    def test_batch_example_contains_exactly_ten_valid_unique_constraints(self):
        workspace = self.sessions["batch"]["workspace"]
        batch = workspace["batch"]
        document = parse_business_rules_document(
            batch["content"], fmt="md", filename=batch["filename"],
        )
        self.assertEqual(batch["ruleCount"], 10)
        self.assertEqual(len(document.rules), 10)
        self.assertEqual({rule.number for rule in document.rules}, EXPECTED_BATCH_IDS)
        self.assertEqual(len({rule.text for rule in document.rules}), 10)
        joined = " ".join(rule.text for rule in document.rules)
        for phrase in ("exactly one", "at least one", "at most one", "date and time", "true/false"):
            self.assertIn(phrase, joined)

    def test_example_titles_ground_every_reference_constraint_path(self):
        ontology_terms = self.sessions["rule"]["ontology"]["entities"]
        batch = self.sessions["batch"]["workspace"]["batch"]
        rules = parse_business_rules_document(
            batch["content"], fmt="md", filename=batch["filename"],
        ).rules
        for rule in rules:
            resolution = resolve_rule_target(rule, ontology_terms)
            actual = {target.split(":", 1)[-1] for target in resolution.constraint_paths}
            with self.subTest(rule=rule.number):
                self.assertEqual(actual, EXPECTED_PATHS[rule.number])

        constraint = self.sessions["rule"]["workspace"]["dataConstraint"]
        rule = BusinessRule(
            number=constraint["number"], title=constraint["title"],
            text=constraint["text"], source_format="session", raw=constraint["text"],
        )
        resolution = resolve_rule_target(rule, ontology_terms)
        self.assertEqual(len(resolution.focus_nodes), 1)
        self.assertEqual(
            {target.split(":", 1)[-1] for target in resolution.constraint_paths},
            {"concernsItem", "concernsTransportMeans", "hasCalculationMethod", "hasEnvironmentalEmissionCode", "hasMeasure"},
        )

    def test_rule_example_passes_exact_enriched_paths_to_generation(self):
        ontology = self.sessions["rule"]["ontology"]
        path_iris = [
            "epo:hasEnvironmentalEmissionCode",
            "epo:hasMeasure",
            "epo:concernsItem",
            "epo:concernsTransportMeans",
            "epo:hasCalculationMethod",
        ]
        roles = _normalise_target_roles(
            {
                "_ontology_terms": ontology["entities"],
                "target_roles": {
                    "focus_nodes": [
                        {
                            "iri": "epo:EnvironmentalEmissionInformation",
                            "label": "Environmental Emission Information",
                        },
                    ],
                    "constraint_paths": [
                        {"iri": iri, "label": iri.split(":", 1)[1]}
                        for iri in path_iris
                    ],
                    "related_terms": [],
                },
            },
            ontology["content"],
            ontology["filename"],
            None,
        )

        actual_paths = {
            term["iri"]: term for term in roles["constraint_paths"]
        }
        self.assertEqual(set(actual_paths), set(path_iris))
        self.assertEqual(actual_paths["epo:hasMeasure"]["kind"], "ObjectProperty")
        self.assertEqual(actual_paths["epo:hasMeasure"]["range"], "epo:Quantity")

        context = _target_context_text(roles)
        self.assertIn("epo:hasMeasure", context)
        self.assertIn("range=epo:Quantity", context)
        self.assertNotIn("epo:hasQuantity", context)

    def test_file_and_preloaded_imports_share_one_payload_function(self):
        javascript = (ROOT / "frontend" / "js" / "export.js").read_text(encoding="utf-8")
        self.assertIn('const SESSION_EXAMPLES_MANIFEST = "examples/manifest.json";', javascript)
        self.assertEqual(javascript.count("importSessionPayload(payload, {"), 2)
        self.assertIn("restorePendingSessionWorkspace(options);", javascript)
        self.assertIn('location.assign(targetPage);', javascript)


if __name__ == "__main__":
    unittest.main()
