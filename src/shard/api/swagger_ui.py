"""Render interactive Swagger UI documentation for the SHARD API."""

from __future__ import annotations

import json
from html import escape


SWAGGER_UI_VERSION = "5.32.9"
SWAGGER_UI_ORIGIN = "https://unpkg.com"
SWAGGER_UI_CSS_INTEGRITY = (
    "sha384-9Q2fpS+xeS4ffJy6CagnwoUl+4ldAYhOs9pgZuEKxypVModhmZFzeMlvVsAjf7uT"
)
SWAGGER_UI_BUNDLE_INTEGRITY = (
    "sha384-7FpIrfnye9wip2SqkAsMf4AwNYHk26Vh4hFxfZsWK6dr1Zr2Ig5fk25hy9lNlGHq"
)
SWAGGER_UI_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com; "
    "img-src 'self' data:; "
    "font-src 'self' data: https://unpkg.com; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def swagger_ui_document(openapi_url: str) -> str:
    """Return a Swagger UI page bound to the deployment's OpenAPI document."""
    safe_href = escape(str(openapi_url or "/api/v1/openapi.json"), quote=True)
    spec_url = json.dumps(str(openapi_url or "/api/v1/openapi.json"))
    version = SWAGGER_UI_VERSION
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Interactive documentation for the SHARD REST API">
  <title>SHARD REST API - Swagger</title>
  <link rel="stylesheet" href="{SWAGGER_UI_ORIGIN}/swagger-ui-dist@{version}/swagger-ui.css" integrity="{SWAGGER_UI_CSS_INTEGRITY}" crossorigin="anonymous">
  <style>
    :root {{
      color-scheme: light;
      --shard-navy: #06275f;
      --shard-teal: #00a9ad;
      --shard-cyan: #08cbd1;
      --shard-border: #d7e1ec;
      --shard-surface: #f5f8fb;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--shard-surface); color: #172033; }}
    .shard-api-header {{
      min-height: 72px;
      padding: 14px clamp(18px, 4vw, 54px);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      background: #fff;
      border-bottom: 1px solid var(--shard-border);
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
    }}
    .shard-api-brand {{ display: flex; align-items: baseline; gap: 12px; min-width: 0; }}
    .shard-api-name {{ color: var(--shard-navy); font-size: 24px; font-weight: 800; letter-spacing: 0; }}
    .shard-api-version {{ color: #526071; font-size: 13px; font-weight: 650; letter-spacing: 0; }}
    .shard-api-links {{ display: flex; align-items: center; gap: 8px; }}
    .shard-api-link {{
      min-height: 36px;
      padding: 8px 13px;
      display: inline-flex;
      align-items: center;
      color: var(--shard-navy);
      background: #fff;
      border: 1px solid #b9c8d8;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
    }}
    .shard-api-link:hover {{ border-color: var(--shard-teal); color: #006f73; }}
    #swagger-ui {{ max-width: 1500px; margin: 0 auto; }}
    .swagger-ui .info {{ margin: 34px 0 24px; }}
    .swagger-ui .info .title {{ color: var(--shard-navy); }}
    .swagger-ui .scheme-container {{ box-shadow: none; border: 1px solid var(--shard-border); }}
    .swagger-ui .opblock-tag {{ border-bottom-color: var(--shard-border); }}
    .swagger-ui .btn.execute {{ background: var(--shard-teal); border-color: var(--shard-teal); }}
    .swagger-ui .btn.execute:hover {{ background: #008e92; border-color: #008e92; }}
    .swagger-ui .download-contents {{ background: var(--shard-navy); }}
    .swagger-load-error {{
      max-width: 900px;
      margin: 40px auto;
      padding: 18px;
      background: #fff;
      border: 1px solid #d5a5a5;
      border-radius: 6px;
      font: 15px/1.5 Inter, ui-sans-serif, system-ui, sans-serif;
    }}
    @media (max-width: 680px) {{
      body {{ overflow-x: hidden; }}
      .shard-api-header {{ align-items: flex-start; flex-direction: column; }}
      .shard-api-brand {{ align-items: flex-start; flex-direction: column; gap: 2px; }}
      #swagger-ui,
      .swagger-ui,
      .swagger-ui .wrapper {{ width: 100%; max-width: 100%; min-width: 0; overflow-x: hidden; }}
      .swagger-ui .wrapper {{ padding: 0 14px; }}
      .swagger-ui .info {{ width: 100%; max-width: 100%; min-width: 0; margin: 24px 0 18px; }}
      .swagger-ui .info .title {{
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        font-size: 26px;
        line-height: 1.15;
      }}
      .swagger-ui .info .title small {{ top: 0; }}
      .swagger-ui .info a {{ word-break: break-all; }}
      .swagger-ui .info li,
      .swagger-ui .info p,
      .swagger-ui .info .main,
      .swagger-ui .renderedMarkdown {{
        width: 100%;
        max-width: 100%;
        min-width: 0;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
      }}
      .swagger-ui .scheme-container {{ padding: 20px 0; }}
      .swagger-ui .opblock-tag {{
        position: relative;
        align-items: flex-start;
        flex-wrap: wrap;
        gap: 4px 8px;
        padding: 14px 42px 14px 10px;
      }}
      .swagger-ui .opblock-tag small {{
        display: block;
        flex: 1 1 100%;
        width: 100%;
        max-width: 100%;
        margin: 0;
        white-space: normal;
        overflow-wrap: anywhere;
      }}
      .swagger-ui .opblock-tag .expand-operation {{
        position: absolute;
        top: 14px;
        right: 10px;
      }}
      .swagger-ui .opblock .opblock-summary {{
        align-items: flex-start;
        flex-wrap: wrap;
        gap: 5px;
      }}
      .swagger-ui .opblock-summary-path {{
        max-width: calc(100% - 88px);
        white-space: normal;
        overflow-wrap: anywhere;
      }}
      .swagger-ui .opblock-summary-description {{
        flex: 1 1 100%;
        margin: 2px 0 0 80px;
      }}
      .swagger-ui .opblock-summary-operation-id {{ margin-left: 80px; }}
    }}
  </style>
</head>
<body>
  <header class="shard-api-header">
    <div class="shard-api-brand">
      <span class="shard-api-name">SHARD</span>
      <span class="shard-api-version">REST API - v1</span>
    </div>
    <nav class="shard-api-links" aria-label="API documentation links">
      <a class="shard-api-link" href="{safe_href}">OpenAPI JSON</a>
      <a class="shard-api-link" href="/api/v1">API root</a>
    </nav>
  </header>
  <main id="swagger-ui" aria-label="Swagger API documentation"></main>
  <noscript>
    <div class="swagger-load-error">JavaScript is required for Swagger UI. The raw OpenAPI document remains available at <a href="{safe_href}">{safe_href}</a>.</div>
  </noscript>
  <script src="{SWAGGER_UI_ORIGIN}/swagger-ui-dist@{version}/swagger-ui-bundle.js" integrity="{SWAGGER_UI_BUNDLE_INTEGRITY}" crossorigin="anonymous"></script>
  <script>
    window.addEventListener("load", function () {{
      if (typeof window.SwaggerUIBundle !== "function") {{
        document.getElementById("swagger-ui").innerHTML = '<div class="swagger-load-error">Swagger UI assets could not be loaded. <a href="{safe_href}">Open the raw OpenAPI document</a>.</div>';
        return;
      }}
      window.ui = window.SwaggerUIBundle({{
        url: {spec_url},
        dom_id: "#swagger-ui",
        deepLinking: true,
        displayOperationId: true,
        displayRequestDuration: true,
        docExpansion: "list",
        filter: true,
        requestSnippetsEnabled: true,
        showCommonExtensions: true,
        showExtensions: true,
        syntaxHighlight: {{ activate: true, theme: "agate" }},
        tryItOutEnabled: false,
        validatorUrl: null,
        presets: [window.SwaggerUIBundle.presets.apis],
        layout: "BaseLayout"
      }});
    }});
  </script>
</body>
</html>
"""
