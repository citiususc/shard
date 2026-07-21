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

    def test_rule_and_batch_use_api_generated_astrea_controls(self):
        for page in ("rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn('id="astrea-use-mode"', content)
                self.assertIn('<option value="evidence">Evidence only</option>', content)
                self.assertIn('<option value="merge">Final merge only</option>', content)
                self.assertIn('<option value="evidence-and-merge">Evidence and merge</option>', content)
                self.assertIn('<option value="generated-priority">Generated priority</option>', content)
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
        self.assertIn("target_roles: {", javascript)
        self.assertIn("focus_nodes: (targetRoles.focus_nodes || []).map(apiTermReference)", javascript)

    def test_ontology_results_support_legacy_and_canonical_references(self):
        javascript = (FRONTEND / "js" / "rule.js").read_text(encoding="utf-8")
        stylesheet = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")
        layout = (FRONTEND / "css" / "layout.css").read_text(encoding="utf-8")

        self.assertIn("function ontologyReferenceValues(value)", javascript)
        self.assertIn("...ontologyReferenceValues(e.domain)", javascript)
        self.assertIn("...ontologyReferenceValues(e.range)", javascript)
        self.assertNotIn("e.domain.map(", javascript)
        self.assertIn("grid-auto-rows: max-content;", stylesheet)
        self.assertIn("align-content: start;", stylesheet)
        self.assertIn("scrollbar-gutter: stable;", stylesheet)
        self.assertIn(".ontology-search-block .entity-filter-switch .switch-button", layout)
        self.assertIn("place-items: center;", layout)
        self.assertIn(".ontology-search-block .ontology-search-row .input", layout)
        self.assertIn("height: 28px;", layout)
        self.assertIn(".ontology-search-block #ontology-search-status", layout)
        self.assertIn(".ontology-search-block .entity-list", layout)

    def test_provider_details_are_local_only(self):
        javascript = (FRONTEND / "js" / "models.js").read_text(encoding="utf-8")
        for page in ("rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn("data-provider-title>Remote inference</span>", content)
                self.assertIn("data-provider-title>Local inference</span>", content)
                self.assertIn('id="databricks-base-url"', content)
                self.assertIn('id="databricks-token"', content)
                self.assertIn("data-provider-private-config hidden", content)
        self.assertIn('deployment_profile !== "public"', javascript)
        self.assertIn('? "Databricks" : "Hugging Face"', javascript)
        self.assertIn('config.databricks = {', javascript)

    def test_model_selection_is_explicit_and_local_download_requires_consent(self):
        javascript = (FRONTEND / "js" / "models.js").read_text(encoding="utf-8")
        for page in ("rule.html", "batch.html"):
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
        self.assertIn('downloadLocalModel: apiUrl("models/local/downloads")',
                      (FRONTEND / "js" / "core.js").read_text(encoding="utf-8"))
        self.assertNotIn("consumeLocalDownload", javascript)
        self.assertNotIn("fetchStream(SERVICES.downloadLocalModel", javascript)
        self.assertIn('deployment_profile !== "public"', javascript)

    def test_frontend_api_base_is_subpath_safe_and_filters_remote_loopback_routes(self):
        javascript = (FRONTEND / "js" / "core.js").read_text(encoding="utf-8")

        self.assertIn('window.SHARD_API_BASE || "api/v1/"', javascript)
        self.assertIn('capabilities: apiUrl("capabilities")', javascript)
        self.assertIn("function resolveRuntimeEndpoint(value)", javascript)
        self.assertIn("!isLoopbackHostname(window.location.hostname)", javascript)
        self.assertNotIn('terms: apiUrl("ontology/search")', javascript)

    def test_batch_to_shapes_is_the_visible_second_workflow(self):
        for page in ("index.html", "rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn("<span>Batch</span>", content)
                self.assertIn("<span>Shapes</span>", content)
                self.assertIn('href="batch.html"', content)
        batch_javascript = (FRONTEND / "js" / "batch.js").read_text(encoding="utf-8")
        self.assertIn('source: "Batch to Shapes"', batch_javascript)

    def test_file_upload_controls_have_icons(self):
        expected_counts = {"rule.html": 3, "batch.html": 4}
        for page, expected in expected_counts.items():
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertEqual(content.count('class="upload-file-icon"'), expected)

    def test_batch_progress_is_integrated_into_the_section_heading(self):
        content = (FRONTEND / "batch.html").read_text(encoding="utf-8")
        javascript = (FRONTEND / "js" / "batch.js").read_text(encoding="utf-8")
        self.assertIn('class="batch-progress-heading"', content)
        progress = content.index('class="batch-progress-heading"')
        cancel = content.index('id="cancel-generation"')
        self.assertLess(progress, cancel)
        self.assertNotIn('class="generation-controls"', content)
        self.assertNotIn(".generation-controls", (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8"))
        self.assertIn('>0 / 0 processed</span>', content)
        self.assertIn('compactUnit = unit === "data constraints processed"', javascript)
        self.assertNotIn('id="queue-count"', content)
        self.assertNotIn('byId("queue-count")', javascript)

    def test_batch_generated_queue_shows_three_compact_rows(self):
        stylesheet = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")

        self.assertIn("--queue-card-height: 36px;", stylesheet)
        self.assertIn("--generated-queue-gap: 4px;", stylesheet)
        self.assertIn("var(--queue-card-height) * 3", stylesheet)

    def test_rule_and_batch_share_the_same_review_geometry(self):
        stylesheet = (FRONTEND / "css" / "support-panels.css").read_text(encoding="utf-8")

        self.assertIn(
            "grid-template-rows: 110px 160px minmax(230px, 1fr) 195px;",
            stylesheet,
        )
        self.assertIn("grid-template-rows: 78px 115px minmax(205px, 1fr) 174px;", stylesheet)
        layout = (FRONTEND / "css" / "layout.css").read_text(encoding="utf-8")
        self.assertIn("flex: 0 0 92px;", layout)
        self.assertIn("max-height: 92px !important;", layout)

    def test_shape_editor_actions_share_the_heading_and_size(self):
        for page in ("rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                actions = content.index('class="section-title-actions shape-editor-actions"')
                code_wrap = content.index('class="code-wrap"', actions)
                for button_id in ("copy-shape", "validate-shape", "accept-shape"):
                    position = content.index(f'id="{button_id}"')
                    self.assertLess(actions, position)
                    self.assertLess(position, code_wrap)
                self.assertNotIn('class="editor-actions"', content)

        workspace = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn(".shape-editor-actions button", workspace)
        self.assertIn("width: 58px;", workspace)
        layout = (FRONTEND / "css" / "layout.css").read_text(encoding="utf-8")
        self.assertIn("body.app-page .validation-panel", layout)
        self.assertIn("padding: 6px 9px;", layout)

    def test_generation_enables_semantic_review_with_sufficient_timeout(self):
        rule_javascript = (FRONTEND / "js" / "rule.js").read_text(encoding="utf-8")
        batch_javascript = (FRONTEND / "js" / "batch.js").read_text(encoding="utf-8")
        shape_javascript = (FRONTEND / "js" / "shapes.js").read_text(encoding="utf-8")

        for javascript in (rule_javascript, batch_javascript):
            self.assertIn("llm_review: true", javascript)
            self.assertIn("review_max_attempts: 3", javascript)
            self.assertIn("semanticReviewSummary", javascript)
        self.assertIn("semantic_review", batch_javascript)
        self.assertIn("Semantic critique passed", shape_javascript)
        self.assertIn("Semantic review not passed", shape_javascript)
        self.assertIn(
            'label: "Generate SHACL shape", timeoutMs: 600000',
            rule_javascript,
        )

    def test_rule_session_metadata_reaches_resolution_and_generation(self):
        javascript = (FRONTEND / "js" / "rule.js").read_text(encoding="utf-8")

        self.assertIn("let activeRuleMetadata", javascript)
        self.assertIn("number: String(constraint.number", javascript)
        self.assertIn("title: String(constraint.title", javascript)
        self.assertEqual(javascript.count("rule: activeBusinessRule(rule)"), 2)
        self.assertNotIn("rule: apiBusinessRule(rule)", javascript)

    def test_prefixes_and_rule_resolution_have_explicit_visible_capacity(self):
        rule_html = (FRONTEND / "rule.html").read_text(encoding="utf-8")
        rule_js = (FRONTEND / "js" / "rule.js").read_text(encoding="utf-8")
        workspace = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")
        support = (FRONTEND / "css" / "support-panels.css").read_text(encoding="utf-8")

        combined_help = "Search by text, rank and resolve ontology terms against the data constraint, or add them manually."
        self.assertIn(combined_help, rule_html)
        self.assertIn(combined_help, rule_js)
        self.assertNotIn('id="resolution-status"', rule_html)
        self.assertIn("height: 64px;", workspace)
        self.assertIn("grid-template-rows: 120px 184px 230px 190px;", support)
        self.assertIn("grid-template-rows: 120px 145px 230px 190px;", support)
        self.assertIn("overflow-y: auto;", support)

    def test_accepted_shapes_support_confirmed_remove_all(self):
        for page in ("rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertEqual(content.count('id="remove-all-accepted-shapes"'), 1)
                self.assertIn(">Remove all</button>", content)

        javascript = (FRONTEND / "js" / "shapes.js").read_text(encoding="utf-8")
        self.assertIn("function removeAllAccepted()", javascript)
        self.assertIn("window.confirm(", javascript)
        self.assertIn("This action cannot be undone.", javascript)
        self.assertIn('action: "remove_all"', javascript)

    def test_frontend_uses_data_constraint_terminology_and_compact_namespaces(self):
        for page in ("index.html", "rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertNotIn("Business rule", content)
        for page in ("rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            self.assertIn('id="ontology-prefix"', content)
            self.assertIn('class="namespace-mini-panels"', content)
            self.assertIn("Multiple: TTL, RDF, OWL, NT", content)
        self.assertTrue((FRONTEND / "templates" / "data_constraints_template.html").is_file())
        self.assertTrue((FRONTEND / "templates" / "data_constraints_template.md").is_file())

    def test_session_management_precedes_spaced_ontology_namespaces(self):
        for page in ("rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertEqual(content.count('class="session-toolbar"'), 1)
                self.assertLess(content.index('class="session-toolbar"'), content.index("<h2>Ontology</h2>"))
                self.assertLess(content.index('id="ontology-summary"'), content.index('class="namespace-mini-panels"'))

        layout = (FRONTEND / "css" / "layout.css").read_text(encoding="utf-8")
        self.assertIn("--rail-namespace-gap: 9px;", layout)
        self.assertIn("gap: var(--rail-namespace-gap);", layout)

    def test_session_import_exposes_manifest_driven_preloaded_examples(self):
        javascript = (FRONTEND / "js" / "export.js").read_text(encoding="utf-8")
        stylesheet = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")
        package = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('SESSION_EXAMPLES_MANIFEST = "examples/manifest.json"', javascript)
        self.assertIn("createSessionImportMenu(importBtn, importInput, options)", javascript)
        self.assertIn("session-import-menu", stylesheet)
        self.assertIn('"frontend/examples/*.json"', package)
        self.assertIn('"frontend/examples/*.md"', package)

    def test_global_and_ontology_clear_actions_have_distinct_scopes(self):
        for page in ("rule.html", "batch.html"):
            content = (FRONTEND / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                toolbar_start = content.index('class="session-toolbar"')
                toolbar_end = content.index("</section>", toolbar_start)
                reset = content.index('id="reset-demo"')
                ontology_clear = content.index('id="clear-ontology"')
                self.assertLess(toolbar_start, reset)
                self.assertLess(reset, toolbar_end)
                self.assertLess(content.index("<h2>Ontology</h2>"), ontology_clear)
                self.assertIn('class="icon-button danger-button"', content)
                self.assertIn('title="Clear all" aria-label="Clear all">↺</button>', content)
                self.assertIn(">Clear ontology</button>", content)

        ontology_js = (FRONTEND / "js" / "ontology.js").read_text(encoding="utf-8")
        export_js = (FRONTEND / "js" / "export.js").read_text(encoding="utf-8")
        core_js = (FRONTEND / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn('removeStoredValue(STORE.ontology);', ontology_js)
        self.assertIn('removeStoredValue(STORE.astreaBaseline);', ontology_js)
        self.assertNotIn('removeStoredValue(STORE.accepted);', ontology_js)
        self.assertIn('removeStoredValue(STORE.accepted);', export_js)
        self.assertIn("function removeStoredValue(key)", core_js)
        self.assertIn("localStorage.removeItem(LEGACY_STORE[key]);", core_js)
        workspace = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn(".session-toolbar .session-actions .secondary-button", workspace)
        self.assertIn("white-space: nowrap;", workspace)

    def test_namespace_inputs_are_compact_and_hide_the_datalist_indicator(self):
        workspace = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")
        layout = (FRONTEND / "css" / "layout.css").read_text(encoding="utf-8")

        self.assertIn("#base-namespace::-webkit-calendar-picker-indicator", workspace)
        self.assertIn("display: none !important;", workspace)
        self.assertIn("body.app-page .rail .namespace-input-row .input", layout)
        self.assertIn("height: 30px !important;", layout)

    def test_namespace_group_is_centered_between_section_dividers(self):
        workspace = (FRONTEND / "css" / "workspace.css").read_text(encoding="utf-8")
        layout = (FRONTEND / "css" / "layout.css").read_text(encoding="utf-8")
        support = (FRONTEND / "css" / "support-panels.css").read_text(encoding="utf-8")

        self.assertIn(".namespace-mini-panel:first-child { padding-top: 12px; }", workspace)
        self.assertIn(".namespace-mini-panel + .namespace-mini-panel { border-top: 0; }", workspace)
        self.assertIn("padding-top: 12px;", layout)
        self.assertIn("margin-top: 12px !important;", support)


if __name__ == "__main__":
    unittest.main()
