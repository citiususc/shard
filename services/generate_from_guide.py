#!/usr/bin/env python3
"""
generate-from-guide service  (br2shacl-ui)  — Mode B (full guide)

Runs the full text2shacl pipeline on an uploaded guide and streams the result
shape-by-shape over Server-Sent-Events, so the human-in-the-loop review queue
fills up while generation continues.

Pipeline (self-contained-lite, all inference via model_loader / Databricks):
  1. parse the ontology
  2. preprocess + index the guide in memory (rag_inmemory.build_inmemory_retriever)
     — emits {"type":"status",...} progress events
  3. stream generation (multiagent_stream.stream_shacl_generation)
     — emits {"type":"start"} then one {"type":"shape"} per property,
       including invalid ones (10 failed attempts) with the parse error,
       then {"type":"done"} with the aggregated node shapes.

Transport: the client POSTs a JSON body and reads the streaming response with
fetch()+ReadableStream (not EventSource, which is GET-only), parsing on "\n\n".

Endpoint:  POST http://127.0.0.1:9103/generate-from-guide
  request : {ontology_content, guide_content, guide_filename, html_version,
             llm_model, text_model, vision_model, embedding_model, temperature,
             provider, inference_config?, prefixes?, base_namespace?}
"""

import base64
import json
import os
import sys
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "text2shacl_core"))

HOST = "127.0.0.1"
PORT = 9103


def _runtime_config(payload):
    return payload.get("inference_config") or payload.get("model_config") or payload


def _materialize_guide(guide_content, guide_filename, is_base64):
    """Write the uploaded guide to a temp file the preprocessors can read.

    HTML is used as-is. PDF is best-effort converted to a minimal HTML wrapper
    (text only) so the v1.6.1 (from-PDF) preprocessor can chunk it.
    Returns (path, html_version_hint).
    """
    suffix = os.path.splitext(guide_filename or "")[1].lower()
    if is_base64:
        raw = base64.b64decode(guide_content)
    else:
        raw = guide_content.encode("utf-8")

    if suffix == ".pdf":
        text = _pdf_to_text(raw)
        html = "<html><body>" + "".join(f"<p>{_esc(line)}</p>"
                                         for line in text.splitlines() if line.strip()) + "</body></html>"
        path = os.path.join(tempfile.gettempdir(), f"br2shacl_guide_{os.getpid()}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path, "1.6.1"

    # HTML (or anything else treated as HTML)
    path = os.path.join(tempfile.gettempdir(), f"br2shacl_guide_{os.getpid()}{suffix or '.html'}")
    with open(path, "wb") as f:
        f.write(raw)
    return path, None


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _pdf_to_text(raw):
    try:
        from pdfminer.high_level import extract_text
        path = os.path.join(tempfile.gettempdir(), f"br2shacl_pdf_{os.getpid()}.pdf")
        with open(path, "wb") as f:
            f.write(raw)
        return extract_text(path)
    except Exception as e:
        return f"[PDF extraction unavailable: {e}]"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _sse(self, event: dict):
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def do_POST(self):
        if self.path != "/generate-from-guide":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        try:
            from runtime_config import inference_config
            with inference_config(_runtime_config(payload)):
                self._run(payload)
        except Exception as exc:
            self._sse({"type": "error", "message": str(exc),
                       "trace": traceback.format_exc()[-1500:]})

    def _run(self, payload):
        from rdflib import Graph
        import ns_utils
        from rag_inmemory import build_inmemory_retriever
        from multiagent_stream import stream_shacl_generation

        ontology_content = payload.get("ontology_content", "")
        if not ontology_content:
            self._sse({"type": "error", "message": "Missing ontology_content."})
            return

        self._sse({"type": "status", "stage": "parsing", "message": "Parsing ontology…"})
        onto = Graph(bind_namespaces="none")
        onto.parse(data=ontology_content, format="turtle")
        base_ns = payload.get("base_namespace") or ns_utils.derive_base_namespace(onto)
        prefixes = payload.get("prefixes") or ns_utils.build_prefix_block(onto, base_ns)

        # --- Preprocess + index the guide ---------------------------------- #
        guide_path, version_hint = _materialize_guide(
            payload.get("guide_content", ""),
            payload.get("guide_filename", ""),
            payload.get("guide_is_base64", False),
        )
        html_version = version_hint or payload.get("html_version", "3.2.1")

        def progress(stage, current, total):
            self._sse({"type": "status", "stage": f"summarizing:{stage}",
                       "current": current, "total": total,
                       "message": f"Summarizing {stage} {current}/{total}…"})

        self._sse({"type": "status", "stage": "preprocessing",
                   "message": f"Preprocessing guide (v{html_version})…"})

        retriever = build_inmemory_retriever(
            file=guide_path,
            html_version=html_version,
            text_model_id=payload.get("text_model") or "databricks-gpt-oss-120b",
            vision_model_id=payload.get("vision_model") or "databricks-gemma-3-12b",
            embedding_model_id=payload.get("embedding_model") or "databricks-qwen3-embedding-0-6b",
            temperature=float(payload.get("temperature", 0.5)),
            progress=progress,
        )

        # --- Stream generation --------------------------------------------- #
        for event in stream_shacl_generation(
            ontology_graph=onto,
            retriever=retriever,
            llm_model_id=payload.get("llm_model") or "databricks-gpt-oss-120b",
            temperature=float(payload.get("temperature", 0.5)),
            astrea_graph=None,
            base_namespace=base_ns,
            prefix_block=prefixes,
        ):
            self._sse(event)

        try:
            os.remove(guide_path)
        except OSError:
            pass


if __name__ == "__main__":
    print(f"generate-from-guide service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
