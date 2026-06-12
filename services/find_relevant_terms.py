#!/usr/bin/env python3
"""Dummy service for ranking ontology terms from a business rule."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re


HOST = "127.0.0.1"
PORT = 9101


def tokenize(value):
    stopwords = {"the", "and", "for", "with", "must", "shall", "every", "each", "value", "record"}
    return [
        token
        for token in re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
        if len(token) > 2 and token not in stopwords
    ]


def rank_terms(payload):
    rule = payload.get("business_rule", "")
    terms = payload.get("ontology_terms", [])
    rule_tokens = tokenize(rule)
    ranked = []

    for term in terms:
        haystack = " ".join(
            str(term.get(key, ""))
            for key in ["label", "iri", "kind", "type", "domain", "range", "ontologyNote"]
        ).lower()
        score = 0
        matched = []

        for token in rule_tokens:
            if token in haystack:
                matched.append(token)
                score += 20 if token in str(term.get("label", "")).lower() else 8

        if term.get("type") == "property" and re.search(r"required|mandatory|exactly|format|greater|less|one of", rule, re.I):
            score += 7
        if term.get("type") == "class" and re.search(r"record|instance|class|closed", rule, re.I):
            score += 5

        if score:
            ranked.append(
                {
                    "entity_id": term.get("id"),
                    "score": min(99, score),
                    "reasons": [
                        "dummy lexical match",
                        f"matched: {', '.join(sorted(set(matched))[:4])}" if matched else "generic rule signal",
                    ],
                }
            )

    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:8]


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_POST(self):
        if self.path != "/find-relevant-terms":
            self._send_json(404, {"error": "unknown endpoint"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self._send_json(
            200,
            {
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "candidates": rank_terms(payload),
                "message": "Dummy lexical ranking. Replace this service with retrieval/LLM logic.",
            },
        )


if __name__ == "__main__":
    print(f"find-relevant-terms service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
