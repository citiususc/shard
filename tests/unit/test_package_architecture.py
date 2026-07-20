"""Guard the source boundaries introduced by the SHARD package layout."""

import ast
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from shard import __description__, __title__  # noqa: E402


class PackageArchitectureTests(unittest.TestCase):
    def test_product_metadata_uses_shard(self):
        self.assertEqual(__title__, "SHARD")
        self.assertIn("Ontology-Grounded SHACL Authoring", __description__)

    def test_application_layer_does_not_import_http_adapters(self):
        for path in sorted((SRC / "shard" / "application").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            with self.subTest(module=path.name):
                self.assertFalse(any(name.startswith("shard.api") for name in imports))
                self.assertNotIn("http.server", imports)

    def test_removed_flat_and_property_first_modules_do_not_return(self):
        self.assertFalse((ROOT / "text2shacl_core").exists())
        self.assertFalse((ROOT / "services").exists())
        forbidden = {
            "multiagent.py",
            "multiagent_stream.py",
            "rag.py",
            "rag_inmemory.py",
        }
        present = {path.name for path in SRC.rglob("*.py")}
        self.assertTrue(forbidden.isdisjoint(present))

    def test_only_the_generic_rule_prompt_is_packaged(self):
        prompts = sorted((SRC / "shard" / "resources" / "prompts").glob("*.json"))
        self.assertEqual([path.name for path in prompts], ["rule_general.json"])


if __name__ == "__main__":
    unittest.main()
