"""
Business-rule template parsing shared by guide services and tests.

The parser is domain-agnostic: rule identifiers are opaque strings and no
ontology-specific index convention is assumed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Dict, List, Optional

try:
    from Logger import logger
except ImportError:  # Allow package-style imports from project-root tests.
    from .Logger import logger


@dataclass
class BusinessRule:
    """Normalized business-rule entry extracted from a template."""

    number: str
    title: str
    text: str
    source_format: str
    raw: str


@dataclass
class BusinessRulesDocument:
    """Parsed business-rule template with optional document metadata."""

    source_format: str
    metadata: Dict[str, str]
    rules: List[BusinessRule]
    filename: str = ""


def _looks_like_path(value: str) -> bool:
    try:
        return bool(value) and Path(value).exists() and Path(value).is_file()
    except OSError:
        return False


def _normalise_format(fmt: Optional[str], filename: str = "", content: str = "") -> str:
    value = (fmt or "").strip().lower().lstrip(".")
    if value in {"html", "htm"}:
        return "html"
    if value in {"md", "markdown"}:
        return "md"

    suffix = Path(filename or "").suffix.lower()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".md", ".markdown"}:
        return "md"

    sample = (content or "").lstrip().lower()
    if "<section" in sample or "<html" in sample or "<!doctype html" in sample:
        return "html"
    if re.search(r"(?im)^\s*##\s+rule\s*$", content or ""):
        return "md"
    raise ValueError("Business rules template format must be .html or .md.")


def _read_source(path_or_content: str | Path, filename: str = "") -> tuple[str, str]:
    if isinstance(path_or_content, Path) or _looks_like_path(str(path_or_content)):
        path = Path(path_or_content)
        return path.read_text(encoding="utf-8"), path.name
    return str(path_or_content or ""), filename


def _field_text(el, field_name: str) -> str:
    if el is None:
        logger.warn(f"Business rule template: missing {field_name}; using empty string.")
        return ""
    text = el.get_text(" ", strip=True)
    return re.sub(rf"^\s*{re.escape(field_name)}\s*:\s*", "", text, flags=re.I).strip()


def _business_rule_html_text(el) -> str:
    if el is None:
        logger.warn("Business rule template: missing business-rule text; using empty string.")
        return ""
    paragraphs = [p.get_text(" ", strip=True) for p in el.find_all("p")]
    paragraphs = [p for p in paragraphs if p]
    if paragraphs:
        return "\n\n".join(paragraphs)
    return el.get_text("\n", strip=True)


def _parse_html_document(content: str, filename: str = "") -> BusinessRulesDocument:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content or "", "html.parser")
    metadata = soup.select_one(".metadata")
    meta = {"ontology": "", "author": "", "date": "", "description": ""}
    for key in meta:
        el = metadata.select_one(f".{key}") if metadata is not None else None
        if el is not None:
            meta[key] = el.get_text(" ", strip=True)

    sections = soup.select("section.rule")
    if not sections:
        logger.warn("Business rule template: no <section class=\"rule\"> entries found.")

    rules: List[BusinessRule] = []
    for idx, section in enumerate(sections, start=1):
        number = _field_text(section.select_one(".number"), "Number")
        title = _field_text(section.select_one(".title"), "Title")
        text = _business_rule_html_text(section.select_one(".business-rule"))
        if not number:
            logger.warn(f"Business rule template: rule {idx} has no Number.")
        if not title:
            logger.warn(f"Business rule template: rule {idx} has no Title.")
        if not text:
            logger.warn(f"Business rule template: rule {number or idx} has empty business-rule text.")
        rules.append(BusinessRule(
            number=number,
            title=title,
            text=text,
            source_format="html",
            raw=str(section).strip(),
        ))

    return BusinessRulesDocument("html", meta, rules, filename=filename)


def _metadata_value(markdown: str, label: str) -> str:
    match = re.search(rf"(?im)^\s*-\s*{re.escape(label)}\s*:\s*(.*)$", markdown)
    return match.group(1).strip() if match else ""


def _parse_md_document(content: str, filename: str = "") -> BusinessRulesDocument:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    meta = {
        "ontology": _metadata_value(text, "Ontology"),
        "author": _metadata_value(text, "Author"),
        "date": _metadata_value(text, "Date"),
        "description": _metadata_value(text, "Description"),
    }

    headings = list(re.finditer(r"(?im)^\s*##\s+Rule\s*$", text))
    if not headings:
        logger.warn("Business rule template: no '## Rule' sections found.")

    rules: List[BusinessRule] = []
    for idx, heading in enumerate(headings, start=1):
        start = heading.end()
        end = headings[idx].start() if idx < len(headings) else len(text)
        raw = text[heading.start():end].strip()
        block = text[start:end].strip()

        number_match = re.search(r"(?im)^\s*-\s*Number\s*:\s*(.*)$", block)
        title_match = re.search(r"(?im)^\s*-\s*Title\s*:\s*(.*)$", block)
        rule_match = re.search(r"(?im)^\s*###\s+Business rule\s*$", block)

        number = number_match.group(1).strip() if number_match else ""
        title = title_match.group(1).strip() if title_match else ""
        if not number_match:
            logger.warn(f"Business rule template: rule {idx} has no Number.")
        if not title_match:
            logger.warn(f"Business rule template: rule {idx} has no Title.")

        rule_text = ""
        if rule_match:
            rule_text = block[rule_match.end():]
            rule_text = re.split(r"(?m)^\s*---\s*$", rule_text, maxsplit=1)[0].strip()
        else:
            logger.warn(f"Business rule template: rule {number or idx} has no Business rule section.")
        if not rule_text:
            logger.warn(f"Business rule template: rule {number or idx} has empty business-rule text.")

        rules.append(BusinessRule(
            number=number,
            title=title,
            text=rule_text,
            source_format="md",
            raw=raw,
        ))

    return BusinessRulesDocument("md", meta, rules, filename=filename)


def parse_business_rules_document(
    path_or_content: str | Path,
    fmt: Optional[str] = None,
    filename: str = "",
) -> BusinessRulesDocument:
    """Parse a Business Rules template into a normalized document object."""
    content, inferred_filename = _read_source(path_or_content, filename)
    filename = filename or inferred_filename
    source_format = _normalise_format(fmt, filename=filename, content=content)
    if source_format == "html":
        return _parse_html_document(content, filename=filename)
    return _parse_md_document(content, filename=filename)


def parse_business_rules(
    path_or_content: str | Path,
    fmt: Optional[str] = None,
    filename: str = "",
) -> List[BusinessRule]:
    """Parse a Business Rules template and return normalized rule entries."""
    return parse_business_rules_document(path_or_content, fmt=fmt, filename=filename).rules


def business_rule_to_dict(rule: BusinessRule) -> Dict[str, str]:
    """Convert a normalized business rule dataclass to a JSON-safe dict."""
    return asdict(rule)
