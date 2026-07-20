"""Tests for the interactive Swagger UI document."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.api.swagger_ui import (  # noqa: E402
    SWAGGER_UI_BUNDLE_INTEGRITY,
    SWAGGER_UI_CSS_INTEGRITY,
    SWAGGER_UI_CSP,
    SWAGGER_UI_VERSION,
    swagger_ui_document,
)


class SwaggerUiTests(unittest.TestCase):
    def test_document_loads_a_pinned_swagger_ui_release(self):
        document = swagger_ui_document("/api/v1/openapi.json")
        self.assertIn(f"swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css", document)
        self.assertIn(f"swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js", document)
        self.assertIn('url: "/api/v1/openapi.json"', document)
        self.assertIn("window.SwaggerUIBundle", document)
        self.assertIn(f'integrity="{SWAGGER_UI_CSS_INTEGRITY}"', document)
        self.assertIn(f'integrity="{SWAGGER_UI_BUNDLE_INTEGRITY}"', document)

    def test_document_keeps_validation_and_credentials_local(self):
        document = swagger_ui_document("/api/v1/openapi.json")
        self.assertIn("validatorUrl: null", document)
        self.assertIn("persistAuthorization: false", document)
        self.assertNotIn("petstore", document.lower())
        self.assertIn("connect-src 'self'", SWAGGER_UI_CSP)
        self.assertIn("frame-ancestors 'none'", SWAGGER_UI_CSP)


if __name__ == "__main__":
    unittest.main()
