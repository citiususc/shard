#!/usr/bin/env python3
"""
generate-from-guide service  (br2shacl-ui)  — Mode B (full guide)

Runs the full text2shacl pipeline on an uploaded Business Rules template
(.html or .md) and streams the result shape-by-shape over Server-Sent-Events,
so the human-in-the-loop review queue fills up while generation continues.

Pipeline (self-contained-lite, all inference via model_loader / Databricks):
  1. parse the ontology
  2. validate + parse the Business Rules template
  3. index the extracted business rules in memory
     — emits {"type":"status",...} progress events
  4. stream generation (multiagent_stream.stream_shacl_generation)
     — emits {"type":"start"} then one {"type":"shape"} per property,
       including invalid ones (10 failed attempts) with the parse error,
       then {"type":"done"} with the aggregated node shapes.

Transport: the client POSTs a JSON body and reads the streaming response with
fetch()+ReadableStream (not EventSource, which is GET-only), parsing on "\n\n".

Endpoint: POST http://127.0.0.1:9103/generate-from-guide
  request : {ontology_content, guide_content, guide_filename,
             llm_model, text_model, vision_model, embedding_model, temperature,
             provider, inference_config?, prefixes?, base_namespace?}
"""

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "text2shacl_core"))

from business_rules import parse_business_rules_document
from ontology_io import ontology_base_namespace, ontology_prefix_block, parse_ontology_graph
from service_http import new_request_id, read_json, send_health, send_json, send_options

HOST = "127.0.0.1"
PORT = 9103


def _runtime_config(payload):
    return payload.get("inference_config") or payload.get("model_config") or payload


def parse_business_rules_template(content: str, filename: str):
    """Parse a Business Rules template, preserving the legacy service shape."""
    doc = parse_business_rules_document(content, filename=filename or "")
    if not doc.rules:
        raise ValueError("The template is valid, but no business rule entries were found.")
    return {
        "format": "markdown" if doc.source_format == "md" else doc.source_format,
        "metadata": doc.metadata,
        "filename": filename or doc.filename or "business_rules_template",
        "rules": [
            {
                "number": rule.number,
                "title": rule.title,
                "business_rule": rule.text,
                "source_format": rule.source_format,
                "raw": rule.raw,
            }
            for rule in doc.rules
        ],
    }


def business_rule_chunks(parsed: dict, domain_context: str = "", generation_guidance: str = ""):
    meta = parsed.get("metadata") or {}
    domain_context = (domain_context or "").strip()
    generation_guidance = (generation_guidance or "").strip()
    chunks = []
    for rule in parsed.get("rules") or []:
        lines = [
            "BUSINESS RULE TEMPLATE ENTRY",
            f"Ontology: {meta.get('ontology', '')}",
        ]
        if domain_context:
            lines.extend(["Domain context:", domain_context])
        if generation_guidance:
            lines.extend(["SHACL generation guidance:", generation_guidance])
        lines.extend([
            f"Rule number: {rule.get('number', '')}",
            f"Rule title: {rule.get('title', '')}",
            "Business rule:",
            rule.get("business_rule", ""),
        ])
        chunks.append("\n".join(lines).strip())
    return chunks


def business_rules_preview_html(parsed: dict):
    """Small normalized HTML representation for logs/debugging and future reuse."""
    meta = parsed.get("metadata") or {}
    rules_html = []
    for rule in parsed.get("rules") or []:
        body = "".join(f"<p>{escape(p.strip())}</p>" for p in rule.get("business_rule", "").split("\n\n") if p.strip())
        rules_html.append(
            f"<section class=\"rule\"><h2>{escape(rule.get('number', ''))}: {escape(rule.get('title', ''))}</h2>{body}</section>"
        )
    return (
        "<!doctype html><html><body><header class=\"metadata\">"
        "<h1>Business Rules</h1>"
        f"<p>Ontology: {escape(meta.get('ontology', ''))}</p>"
        f"<p>Author: {escape(meta.get('author', ''))}</p>"
        f"<p>Date: {escape(meta.get('date', ''))}</p>"
        f"<p>Description: {escape(meta.get('description', ''))}</p>"
        "</header><main>"
        + "\n".join(rules_html)
        + "</main></body></html>"
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        send_options(self)

    def do_GET(self):
        self.request_id = new_request_id(self.headers)
        if self.path == "/health":
            send_health(self, "generate-from-guide", request_id=self.request_id)
            return
        send_json(self, 404, {"error": "unknown endpoint"}, request_id=self.request_id)

    def _sse(self, event: dict):
        event.setdefault("request_id", getattr(self, "request_id", None))
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def do_POST(self):
        self.request_id = new_request_id(self.headers)
        if self.path != "/generate-from-guide":
            send_json(self, 404, {"error": "unknown endpoint"}, request_id=self.request_id)
            return

        try:
            payload = read_json(self)
        except ValueError as exc:
            send_json(self, 400, {"error": str(exc)}, request_id=self.request_id)
            return
        if not payload.get("ontology_content"):
            send_json(self, 400, {"error": "Missing ontology_content."}, request_id=self.request_id)
            return
        try:
            parsed_guide = parse_business_rules_template(
                payload.get("guide_content", ""),
                payload.get("guide_filename", ""),
            )
            payload["_business_rules"] = parsed_guide
            payload["_business_rules_html"] = business_rules_preview_html(parsed_guide)
        except Exception as exc:
            send_json(self, 400, {"error": str(exc)}, request_id=self.request_id)
            return
        try:
            parse_ontology_graph(
                payload.get("ontology_content", ""),
                payload.get("ontology_filename", "ontology.ttl"),
            )
        except Exception as exc:
            send_json(self, 400, {"error": str(exc)}, request_id=self.request_id)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Request-ID", self.request_id)
        self._cors()
        self.end_headers()

        try:
            from runtime_config import inference_config
            from Logger import logger
            with logger.request_context(self.request_id), inference_config(_runtime_config(payload)):
                self._run(payload)
        except Exception as exc:
            self._sse({"type": "error", "message": str(exc),
                       "trace": traceback.format_exc()[-1500:]})

    def _run(self, payload):
        from rag_inmemory import build_inmemory_retriever_from_texts
        from multiagent_stream import stream_shacl_generation

        ontology_content = payload.get("ontology_content", "")

        self._sse({"type": "status", "stage": "parsing", "message": "Parsing ontology…"})
        onto = parse_ontology_graph(
            ontology_content,
            payload.get("ontology_filename", "ontology.ttl"),
        )
        base_ns = payload.get("base_namespace") or ontology_base_namespace(onto)
        prefixes = payload.get("prefixes") or ontology_prefix_block(onto, base_ns)

        parsed_guide = payload.get("_business_rules") or parse_business_rules_template(
            payload.get("guide_content", ""),
            payload.get("guide_filename", ""),
        )
        chunks = business_rule_chunks(
            parsed_guide,
            domain_context=payload.get("domain_context", ""),
            generation_guidance=payload.get("generation_guidance", ""),
        )
        self._sse({"type": "status", "stage": "template",
                   "current": len(chunks), "total": len(chunks),
                   "message": f"Validated Business Rules template: {len(chunks)} rule(s)."})

        self._sse({"type": "status", "stage": "preprocessing",
                   "current": 0, "total": len(chunks),
                   "message": "Indexing business rules…"})
        retriever = build_inmemory_retriever_from_texts(
            texts=chunks,
            embedding_model_id=payload.get("embedding_model") or "system.ai.qwen3-embedding-0-6b",
        )
        self._sse({"type": "status", "stage": "preprocessing",
                   "current": len(chunks), "total": len(chunks),
                   "message": f"Indexed {len(chunks)} business rule(s)."})

        # --- Stream generation --------------------------------------------- #
        for event in stream_shacl_generation(
            ontology_graph=onto,
            retriever=retriever,
            llm_model_id=payload.get("llm_model") or "system.ai.gemma-3-12b",
            temperature=float(payload.get("temperature", 0.5)),
            astrea_graph=None,
            base_namespace=base_ns,
            prefix_block=prefixes,
        ):
            self._sse(event)


if __name__ == "__main__":
    print(f"generate-from-guide service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
