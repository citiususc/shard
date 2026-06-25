#!/usr/bin/env python3
"""
find-relevant-terms service  (br2shacl-ui)

Ranks ontology entities by relevance to a business rule.

Upgraded from the dummy lexical matcher to semantic ranking using the real
text2shacl embedding backend (model_loader.get_embedding_function). Because the
Databricks embedding endpoint is throttled, the per-entity embedding matrix is
computed once per ontology and cached in memory, so only the (single) rule
embedding is computed on each call. If embeddings are unavailable (no key,
missing package, endpoint error), it transparently falls back to the original
lexical ranking so the UI keeps working.

Endpoint:  POST http://127.0.0.1:9101/find-relevant-terms
  request : {business_rule, ontology_terms, model?, embedding_model?, provider?, ...}
  response: {candidates: [{entity_id, score, reasons[]}], method, message}
"""

import hashlib
import json
import math
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "text2shacl_core"))

HOST = "127.0.0.1"
PORT = 9101
TOP_K = 8

# Cache key: (embedding model, ontology content hash, entity fingerprint).
# It deliberately survives ontology changes/resets for the lifetime of this
# service process, so reloading the exact same ontology can reuse its vectors.
_EMBED_CACHE = {}
_PREPARE_JOBS = {}
_CACHE_LOCK = threading.Lock()
_MODEL_LOCKS = {}


class EmbeddingsPreparing(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Lexical fallback (original behaviour)
# --------------------------------------------------------------------------- #
def tokenize(value):
    stopwords = {"the", "and", "for", "with", "must", "shall", "every", "each", "value", "record"}
    return [t for t in re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
            if len(t) > 2 and t not in stopwords]


def rank_lexical(rule, terms, allowed_types=None, top_k=TOP_K):
    rule_tokens = tokenize(rule)
    ranked = []
    for term in terms:
        if allowed_types and term.get("type") not in allowed_types:
            continue
        haystack = " ".join(str(term.get(k, "")) for k in
                            ["label", "iri", "kind", "type", "domain", "range", "ontologyNote"]).lower()
        score, matched = 0, []
        for token in rule_tokens:
            if token in haystack:
                matched.append(token)
                score += 20 if token in str(term.get("label", "")).lower() else 8
        if term.get("type") == "property" and re.search(
                r"required|mandatory|exactly|format|greater|less|one of", rule, re.I):
            score += 7
        if term.get("type") == "class" and re.search(r"record|instance|class|closed", rule, re.I):
            score += 5
        if score:
            ranked.append({
                "entity_id": term.get("id"),
                "score": min(99, score),
                "reasons": ["lexical match",
                            f"matched: {', '.join(sorted(set(matched))[:4])}" if matched else "rule signal"],
            })
    return sorted(ranked, key=lambda i: i["score"], reverse=True)[:top_k]


# --------------------------------------------------------------------------- #
# Semantic ranking
# --------------------------------------------------------------------------- #
def _entity_text(term):
    parts = [term.get("label", ""), term.get("kind", ""), term.get("ontologyNote", "")]
    if term.get("type") == "property":
        parts.append(f"domain {term.get('domain', '')} range {term.get('range', '')}")
    return ". ".join(p for p in parts if p).strip()


def _fingerprint(terms):
    h = hashlib.sha1()
    for t in sorted(terms, key=lambda item: str(item.get("id", ""))):
        h.update((t.get("id", "") + "|" + _entity_text(t)).encode("utf-8"))
    return h.hexdigest()


def _cache_key(terms, embedding_model_id, ontology_hash=""):
    return embedding_model_id, ontology_hash or "no-content-hash", _fingerprint(terms)


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _job_payload(key, job):
    model_id, ontology_hash, ontology_fingerprint = key
    return {
        "status": job.get("status", "unknown"),
        "job_id": job.get("job_id"),
        "embedding_model": model_id,
        "ontology_hash": ontology_hash,
        "ontology_fingerprint": ontology_fingerprint,
        "completed": job.get("completed", 0),
        "total": job.get("total", 0),
        "message": job.get("message", ""),
    }


def _update_job(key, job_id, **patch):
    with _CACHE_LOCK:
        current = _PREPARE_JOBS.get(key)
        if not current or current.get("job_id") != job_id:
            return False
        current.update(patch)
        return True


def _model_lock(embedding_model_id):
    with _CACHE_LOCK:
        return _MODEL_LOCKS.setdefault(embedding_model_id, threading.Lock())


def _prepare_worker(key, terms, embedding_model_id, job_id, cancel_event):
    try:
        from model_loader import get_embedding_function  # imported lazily (heavy deps)
        embedder = get_embedding_function(embedding_model_id)
        if hasattr(embedder, "set_cancel_event"):
            embedder.set_cancel_event(cancel_event)
        batch_size = max(1, int(os.environ.get(
            "ONTOLOGY_EMBED_BATCH_SIZE",
            os.environ.get("DATABRICKS_EMBED_BATCH_SIZE", "8"),
        )))
        ids, vectors = [], []
        model_lock = _model_lock(embedding_model_id)

        for start in range(0, len(terms), batch_size):
            if cancel_event.is_set():
                _update_job(key, job_id, status="cancelled",
                            message="Ontology embedding preparation cancelled.")
                return
            batch = terms[start:start + batch_size]
            acquired = False
            while not acquired and not cancel_event.is_set():
                acquired = model_lock.acquire(timeout=0.2)
            if not acquired:
                _update_job(key, job_id, status="cancelled",
                            message="Ontology embedding preparation cancelled.")
                return
            try:
                batch_vectors = embedder.embed_documents([_entity_text(t) for t in batch])
            finally:
                model_lock.release()
            if cancel_event.is_set():
                _update_job(key, job_id, status="cancelled",
                            message="Ontology embedding preparation cancelled.")
                return
            ids.extend(t.get("id") for t in batch)
            vectors.extend(batch_vectors)
            _update_job(
                key, job_id,
                completed=len(ids),
                message=f"Prepared {len(ids)} of {len(terms)} ontology terms.",
            )

        cached = {"ids": ids, "embeddings": vectors}
        with _CACHE_LOCK:
            current = _PREPARE_JOBS.get(key)
            if (not current or current.get("job_id") != job_id
                    or cancel_event.is_set()):
                return
            _EMBED_CACHE[key] = cached
            current.update(
                status="ready",
                completed=len(ids),
                message=f"{len(ids)} ontology term embeddings ready.",
            )
    except Exception as exc:
        if cancel_event.is_set():
            _update_job(key, job_id, status="cancelled",
                        message="Ontology embedding preparation cancelled.")
        else:
            _update_job(key, job_id, status="error",
                        message=f"Could not prepare ontology embeddings: {exc}")


def prepare_embeddings(payload):
    terms = payload.get("ontology_terms", [])
    embedding_model_id = payload.get("embedding_model") or "databricks-qwen3-embedding-0-6b"
    ontology_hash = payload.get("ontology_hash", "")
    if not terms:
        return {"status": "none", "completed": 0, "total": 0,
                "message": "No ontology terms supplied."}

    key = _cache_key(terms, embedding_model_id, ontology_hash)
    with _CACHE_LOCK:
        cached = _EMBED_CACHE.get(key)
        if cached is not None:
            return {
                "status": "ready",
                "embedding_model": embedding_model_id,
                "ontology_hash": ontology_hash,
                "ontology_fingerprint": key[2],
                "completed": len(cached["ids"]),
                "total": len(cached["ids"]),
                "message": f"{len(cached['ids'])} ontology term embeddings reused from memory.",
            }

        current = _PREPARE_JOBS.get(key)
        if current and current.get("status") == "preparing":
            return _job_payload(key, current)

        job_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        job = {
            "job_id": job_id,
            "status": "preparing",
            "completed": 0,
            "total": len(terms),
            "message": f"Preparing embeddings for {len(terms)} ontology terms.",
            "cancel_event": cancel_event,
        }
        _PREPARE_JOBS[key] = job

    thread = threading.Thread(
        target=_prepare_worker,
        args=(key, list(terms), embedding_model_id, job_id, cancel_event),
        daemon=True,
        name=f"ontology-embeddings-{job_id[:8]}",
    )
    thread.start()
    return _job_payload(key, job)


def embedding_status(payload):
    terms = payload.get("ontology_terms", [])
    embedding_model_id = payload.get("embedding_model") or "databricks-qwen3-embedding-0-6b"
    ontology_hash = payload.get("ontology_hash", "")
    ontology_fingerprint = payload.get("ontology_fingerprint", "")
    if terms:
        key = _cache_key(terms, embedding_model_id, ontology_hash)
    elif ontology_fingerprint:
        key = (embedding_model_id, ontology_hash or "no-content-hash", ontology_fingerprint)
    else:
        return {"status": "none", "completed": 0, "total": 0}
    with _CACHE_LOCK:
        cached = _EMBED_CACHE.get(key)
        if cached is not None:
            return {
                "status": "ready",
                "embedding_model": embedding_model_id,
                "ontology_hash": ontology_hash,
                "ontology_fingerprint": key[2],
                "completed": len(cached["ids"]),
                "total": len(cached["ids"]),
                "message": f"{len(cached['ids'])} ontology term embeddings ready.",
            }
        job = _PREPARE_JOBS.get(key)
        if job:
            return _job_payload(key, job)
    return {
        "status": "missing",
        "embedding_model": embedding_model_id,
        "ontology_hash": ontology_hash,
        "ontology_fingerprint": key[2],
        "completed": 0,
        "total": len(terms) if terms else 0,
        "message": "Ontology embeddings have not been prepared.",
    }


def cancel_embeddings(payload):
    ontology_hash = payload.get("ontology_hash", "")
    embedding_model_id = payload.get("embedding_model")
    cancelled = 0
    with _CACHE_LOCK:
        for key, job in list(_PREPARE_JOBS.items()):
            model_id, key_hash, _ = key
            if ontology_hash and key_hash != ontology_hash:
                continue
            if embedding_model_id and model_id != embedding_model_id:
                continue
            if job.get("status") != "preparing":
                continue
            job["status"] = "cancelling"
            job["message"] = "Cancelling ontology embedding preparation…"
            job["cancel_event"].set()
            cancelled += 1
    return {
        "status": "cancelling" if cancelled else "idle",
        "cancelled_jobs": cancelled,
        "message": f"Cancellation requested for {cancelled} job(s).",
    }


def rank_semantic(rule, terms, embedding_model_id, ontology_hash="",
                  allowed_types=None, top_k=TOP_K):
    key = _cache_key(terms, embedding_model_id, ontology_hash)
    with _CACHE_LOCK:
        cached = _EMBED_CACHE.get(key)
        job = _PREPARE_JOBS.get(key)
    if cached is None:
        if not job or job.get("status") not in {"preparing", "cancelling"}:
            prepare_embeddings({
                "ontology_terms": terms,
                "embedding_model": embedding_model_id,
                "ontology_hash": ontology_hash,
            })
        raise EmbeddingsPreparing("ontology embeddings are still being prepared")

    from model_loader import get_embedding_function  # imported lazily (heavy deps)
    embedder = get_embedding_function(embedding_model_id)
    with _model_lock(embedding_model_id):
        rule_vec = embedder.embed_query(rule)
    terms_by_id = {term.get("id"): term for term in terms}

    scored = []
    for entity_id, vec in zip(cached["ids"], cached["embeddings"]):
        term = terms_by_id.get(entity_id)
        if not term or (allowed_types and term.get("type") not in allowed_types):
            continue
        sim = _cosine(rule_vec, vec)
        scored.append((entity_id, sim))
    scored.sort(key=lambda x: x[1], reverse=True)

    candidates = []
    for entity_id, sim in scored[:top_k]:
        candidates.append({
            "entity_id": entity_id,
            "score": max(1, min(99, round(sim * 100))),
            "reasons": ["semantic similarity", f"cosine {sim:.3f}"],
        })
    return candidates


def rank_terms(payload):
    rule = payload.get("business_rule", "")
    terms = payload.get("ontology_terms", [])
    if not rule or not terms:
        return {"candidates": [], "method": "none", "message": "Provide a rule and an ontology."}

    # Credentials (DATABRICKS_TOKEN/DATABRICKS_BASE_URL or HF_TOKEN) are read from
    # the environment (.env), loaded by run_demo.py. The model_loader routes to the
    # right backend by id format.
    embedding_model_id = payload.get("embedding_model") or "databricks-qwen3-embedding-0-6b"
    ontology_hash = payload.get("ontology_hash", "")
    allowed_types = set(payload.get("entity_types") or [])
    try:
        top_k = max(1, min(100, int(payload.get("top_k", TOP_K))))
    except (TypeError, ValueError):
        top_k = TOP_K
    try:
        candidates = rank_semantic(
            rule, terms, embedding_model_id, ontology_hash,
            allowed_types=allowed_types, top_k=top_k,
        )
        return {"candidates": candidates, "method": "semantic",
                "message": f"Semantic ranking via '{embedding_model_id}'."}
    except EmbeddingsPreparing:
        candidates = rank_lexical(
            rule, terms, allowed_types=allowed_types, top_k=top_k,
        )
        return {
            "candidates": candidates,
            "method": "lexical-preparing",
            "message": "Ontology embeddings are still being prepared; used lexical ranking for now.",
        }
    except Exception as exc:
        candidates = rank_lexical(
            rule, terms, allowed_types=allowed_types, top_k=top_k,
        )
        return {"candidates": candidates, "method": "lexical",
                "message": f"Embeddings unavailable ({exc}); used lexical fallback."}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

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
        if self.path not in {
            "/find-relevant-terms",
            "/prepare-ontology-embeddings",
            "/ontology-embedding-status",
            "/cancel-ontology-embeddings",
        }:
            self._send_json(404, {"error": "unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/prepare-ontology-embeddings":
            result = prepare_embeddings(payload)
        elif self.path == "/ontology-embedding-status":
            result = embedding_status(payload)
        elif self.path == "/cancel-ontology-embeddings":
            result = cancel_embeddings(payload)
        else:
            result = rank_terms(payload)
        self._send_json(200, {"provider": payload.get("provider"),
                              "model": payload.get("model"), **result})


if __name__ == "__main__":
    print(f"find-relevant-terms service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
