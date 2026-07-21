"""Tests for the user-facing data-constraint batch templates."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from shard.domain.business_rules import parse_business_rules_document  # noqa: E402


TEMPLATES = ROOT / "frontend" / "templates"


class DataConstraintTemplateTests(unittest.TestCase):
    def test_downloadable_templates_are_accepted_by_the_shared_parser(self):
        for filename in ("data_constraints_template.html", "data_constraints_template.md"):
            with self.subTest(filename=filename):
                document = parse_business_rules_document(TEMPLATES / filename)
                self.assertEqual(len(document.rules), 3)

    def test_legacy_markdown_heading_remains_accepted(self):
        document = parse_business_rules_document(
            """## Rule

- Number: LEGACY-1
- Title: Legacy input

### Business rule

Every Book has one title.
""",
            fmt="md",
        )
        self.assertEqual(document.rules[0].text, "Every Book has one title.")


if __name__ == "__main__":
    unittest.main()
