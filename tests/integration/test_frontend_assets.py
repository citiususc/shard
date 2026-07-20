"""Regression tests for the static SHARD frontend asset graph."""

from html.parser import HTMLParser
from pathlib import Path
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


class _AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "img"} and values.get("src"):
            self.references.append(values["src"])
        if tag in {"link", "a"} and values.get("href"):
            self.references.append(values["href"])


class FrontendAssetTests(unittest.TestCase):
    def test_local_html_references_exist(self):
        for html_path in sorted(FRONTEND.glob("*.html")):
            parser = _AssetParser()
            parser.feed(html_path.read_text(encoding="utf-8"))
            for reference in parser.references:
                parsed = urlsplit(reference)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                with self.subTest(page=html_path.name, reference=reference):
                    self.assertTrue((html_path.parent / parsed.path).is_file())

    def test_every_page_exposes_shard_branding(self):
        for html_path in sorted(FRONTEND.glob("*.html")):
            content = html_path.read_text(encoding="utf-8")
            with self.subTest(page=html_path.name):
                self.assertIn("SHARD", content)
                self.assertNotIn("text2shacl", content.lower())

    def test_shared_javascript_load_order_is_stable(self):
        expected = [
            "js/core.js",
            "js/logs.js",
            "js/turtle.js",
            "js/models.js",
            "js/ontology.js",
            "js/shapes.js",
            "js/export.js",
        ]
        for html_path in sorted(FRONTEND.glob("*.html")):
            content = html_path.read_text(encoding="utf-8")
            positions = [content.index(f'src="{path}?v=1"') for path in expected]
            with self.subTest(page=html_path.name):
                self.assertEqual(positions, sorted(positions))

    def test_rule_and_guide_use_api_generated_astrea_controls(self):
        for page in ("rule.html", "guide.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn('id="astrea-use-mode"', content)
                self.assertIn('<option value="baseline">Baseline only</option>', content)
                self.assertIn('<option value="merge">Final merge only</option>', content)
                self.assertIn('<option value="both">Both</option>', content)
                self.assertIn('id="astrea-merge-technique"', content)
                self.assertNotIn('id="astrea-baseline-file"', content)

    def test_rule_page_exposes_role_aware_optional_term_review(self):
        html = (FRONTEND / "rule.html").read_text(encoding="utf-8")
        javascript = (FRONTEND / "js" / "rule.js").read_text(encoding="utf-8")

        self.assertIn('id="focus-node-list"', html)
        self.assertIn('id="constraint-path-list"', html)
        self.assertIn('id="related-term-list"', html)
        self.assertIn("Resolve and generate SHACL shape", html)
        self.assertNotIn("Selected target", html)
        self.assertIn("SERVICES.resolveRule", javascript)
        self.assertIn("target_roles: targetRoles", javascript)

    def test_model_backend_labels_are_provider_neutral(self):
        for page in ("rule.html", "guide.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn("<span>Remote inference</span>", content)
                self.assertIn("<span>Local inference</span>", content)
                self.assertNotIn("Databricks", content)
                self.assertNotIn("Hugging Face", content)
                self.assertNotIn('id="databricks-base-url"', content)
                self.assertNotIn('id="databricks-token"', content)

    def test_model_selection_is_explicit_and_local_download_requires_consent(self):
        javascript = (FRONTEND / "js" / "models.js").read_text(encoding="utf-8")
        for page in ("rule.html", "guide.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn('id="llm-model-local-state"', content)
                self.assertIn('id="llm-model-download"', content)
                self.assertIn('id="embedding-model-local-state"', content)
                self.assertIn('id="embedding-model-download"', content)
                self.assertIn("data-custom-model-setting", content)
        self.assertIn('llmModel: ""', javascript)
        self.assertIn('embeddingModel: ""', javascript)
        self.assertIn('placeholder = "Select a model"', javascript)
        self.assertIn("window.confirm", javascript)
        self.assertIn("SERVICES.localModelStatus", javascript)
        self.assertIn("SERVICES.downloadLocalModel", javascript)
        self.assertIn('deployment_profile !== "public"', javascript)

    def test_batch_to_rules_is_the_visible_second_workflow(self):
        for page in ("index.html", "rule.html", "guide.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn("<span>Batch</span>", content)
                self.assertIn("<span>Rules</span>", content)
                self.assertNotIn("<span>Guide</span>", content)
        guide_javascript = (FRONTEND / "js" / "guide.js").read_text(encoding="utf-8")
        self.assertNotIn('source: "Guide to Shapes"', guide_javascript)


if __name__ == "__main__":
    unittest.main()
