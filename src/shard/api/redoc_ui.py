"""Self-contained ReDoc host page for the SHARD OpenAPI document."""

from __future__ import annotations

import json
from html import escape


REDOC_VERSION = "2.5.0"
REDOC_ORIGIN = "https://cdn.jsdelivr.net/npm"
REDOC_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src "
    f"{REDOC_ORIGIN}; connect-src 'self'; img-src data:; font-src data:"
)


def redoc_document(openapi_url: str) -> str:
    """Return a minimal ReDoc page bound to the local OpenAPI URL."""
    resolved_url = str(openapi_url or "openapi.json")
    safe_url = escape(resolved_url, quote=True)
    spec_url = json.dumps(resolved_url)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SHARD API Reference</title>
  <style>body {{ margin: 0; }} redoc {{ display: block; }}</style>
</head>
<body>
  <redoc spec-url={json.dumps(safe_url)}></redoc>
  <noscript>JavaScript is required. The raw contract is available at <a href="{safe_url}">{safe_url}</a>.</noscript>
  <script src="{REDOC_ORIGIN}/redoc@{REDOC_VERSION}/bundles/redoc.standalone.js"></script>
  <script>Redoc.init({spec_url}, {{}}, document.querySelector('redoc'));</script>
</body>
</html>"""
