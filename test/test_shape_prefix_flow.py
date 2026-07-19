"""Integration coverage for preferred generated-shape prefixes."""

from pathlib import Path
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT_DIR / "text2shacl_core"
SERVICES_DIR = ROOT_DIR / "services"
for directory in (CORE_DIR, SERVICES_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from generate_from_guide import generate_guide_shapes


class PreferredShapePrefixFlowTests(unittest.TestCase):
    def test_rule_first_propagates_preferred_prefix_to_builder_and_consolidation(self):
        ontology = (ROOT_DIR / "test" / "asset_maintenance_ontology.ttl").read_text(
            encoding="utf-8"
        )
        guide = """
# Business Rules

## Rule

- Number: BR-001
- Title: Asset identifier

### Business rule

Every Asset must have exactly one asset identifier.
"""
        seen_prefixes = []
        seen_astrea_graphs = []

        def shape_builder(payload):
            seen_prefixes.append(payload.get("shape_prefix"))
            seen_astrea_graphs.append(payload.get("_astrea_graph"))
            target = payload["target"]
            if target.get("type") == "property":
                shape = """
asset-sh:AssetIdentifierShape a sh:PropertyShape ;
    sh:targetClass ex:Asset ;
    sh:path ex:assetIdentifier ;
    sh:minCount 1 .
"""
            else:
                shape = """
asset-sh:AssetShape a sh:NodeShape ;
    sh:targetClass ex:Asset .
"""
            return {
                "shape": shape.strip(),
                "valid": True,
                "error": None,
                "error_type": "none",
                "attempts": 1,
            }

        result = generate_guide_shapes(
            {
                "iteration_mode": "rule",
                "ontology_content": ontology,
                "ontology_filename": "asset_maintenance_ontology.ttl",
                "guide_content": guide,
                "guide_filename": "rules.md",
                "base_namespace": "http://example.org/asset-maintenance#",
                "shape_namespace": "http://example.org/asset-maintenance/shapes/",
                "shape_prefix": "asset-sh",
                "prefixes": "\n".join([
                    "@prefix ex: <http://example.org/asset-maintenance#> .",
                    "@prefix asset-sh: <http://example.org/asset-maintenance/shapes/> .",
                    "@prefix sh: <http://www.w3.org/ns/shacl#> .",
                    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
                ]),
                "wait_embeddings": False,
                "resolver_llm_fallback": False,
                "astrea_baseline": {
                    "name": "astrea.ttl",
                    "content": """
@prefix ast: <http://example.org/astrea/> .
@prefix ex: <http://example.org/asset-maintenance#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .

ast:AssetIdentifierShape a sh:PropertyShape ;
    sh:targetClass ex:Asset ;
    sh:path ex:assetIdentifier ;
    sh:minCount 1 .
""",
                },
            },
            shape_builder=shape_builder,
        )

        self.assertTrue(seen_prefixes)
        self.assertEqual(set(seen_prefixes), {"asset-sh"})
        self.assertTrue(seen_astrea_graphs)
        self.assertTrue(all(graph is seen_astrea_graphs[0] for graph in seen_astrea_graphs))
        self.assertEqual(result["shape_prefix"], "asset-sh")
        self.assertIn("asset-sh:", result["shape_document"])
        self.assertNotIn("@prefix shape:", result["shape_document"])


if __name__ == "__main__":
    unittest.main()
