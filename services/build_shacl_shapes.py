#!/usr/bin/env python3
"""
build-shacl-shape service  (br2shacl-ui)  — Mode A (single rule)

Generates a SHACL shape for one selected ontology target from a hand-written
business rule, reusing the real text2shacl generator:

  * prompts/multiagent.json → generator_agent_property_without_astrea
    (and ..._with_error on a failed parse)
  * utils.clean_shacl_response to extract the Turtle
  * the rdflib parse-and-retry loop ported from multiagent._generator_agent
  * model_loader.get_chat_llm for inference (Databricks or HF, routed by id)

The hand-written rule plays the role of the RAG evidence. The ontology context is
rebuilt by re-parsing the uploaded ontology and calling the same helpers the
OntologyAgent uses (get_info_by_name, get_property_domain), so the model gets the
rich context, not just the flattened term.

Endpoint:  POST http://127.0.0.1:9102/build-shacl-shape
  request : {business_rule, target, prefixes, ontology_content, model, provider,
             temperature?, inference_config?, base_namespace?}
  response: {shape, valid, error, attempts, hints[], fallback, message}
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "text2shacl_core"))

from rdflib import Graph
from langchain_core.output_parsers import StrOutputParser

HOST = "127.0.0.1"
PORT = 9102
MAX_RETRIES = 10


def _runtime_config(payload):
    return payload.get("inference_config") or payload.get("model_config") or payload


def _build_ontology_info(ontology_content, target):
    """Rebuild ontology context for the target, mirroring OntologyAgent iter 0."""
    from utils import get_info_by_name, get_property_domain

    if not ontology_content:
        # No ontology content: fall back to the flattened note from the UI.
        return f"# {target.get('iri')}\n{target.get('ontologyNote', '')}\n"

    g = Graph(bind_namespaces="none")
    g.parse(data=ontology_content, format="turtle")

    prop = target.get("full_iri") or target.get("iri")
    info = ""
    prop_info = get_info_by_name(g, prop)
    if prop_info:
        info += f"# {prop}\n{prop_info}\n\n"

    if target.get("type") == "property":
        domain = get_property_domain(g, prop)
        if domain:
            info += f"Domain of {prop}: {domain}\n\n"
            for owl_class in domain:
                cls_info = get_info_by_name(g, owl_class)
                if cls_info:
                    info += f"## {owl_class}\n{cls_info}\n\n"
    return info or f"# {prop}\n{target.get('ontologyNote', '')}\n"


def _hints_from_shape(shape_str, prefixes):
    """Derive constraint hints by listing sh:* predicates present in the shape."""
    hints = []
    try:
        g = Graph(bind_namespaces="none")
        g.parse(data=f"{prefixes}\n{shape_str}", format="turtle")
        SH = "http://www.w3.org/ns/shacl#"
        for s, p, o in g:
            if str(p).startswith(SH):
                local = str(p).rsplit("#", 1)[-1]
                if local in {"path", "targetClass", "property"}:
                    continue
                try:
                    oq = g.qname(o)
                except Exception:
                    oq = str(o)
                hints.append({"reason": f"constraint sh:{local}", "constraint": f"sh:{local} {oq}"})
    except Exception:
        pass
    # de-duplicate, cap
    seen, out = set(), []
    for h in hints:
        if h["constraint"] not in seen:
            seen.add(h["constraint"])
            out.append(h)
    return out[:12]


def build_shape(payload):
    from model_loader import get_chat_llm, DEFAULT_GEN_MAX_NEW_TOKENS
    from prompts import load_prompt_from_json
    from utils import clean_shacl_response
    from Logger import logger
    import ns_utils

    target = payload.get("target") or {}
    rule = payload.get("business_rule", "")
    domain_context = (payload.get("domain_context") or "").strip() or "(none provided)"
    generation_guidance = (payload.get("generation_guidance") or "").strip() or "(none provided)"
    prefixes = payload.get("prefixes") or ""
    ontology_content = payload.get("ontology_content", "")
    temperature = float(payload.get("temperature", 0.5))
    model_id = payload.get("model") or "databricks-gpt-oss-120b"

    base_ns = payload.get("base_namespace") or ""
    if not base_ns and ontology_content:
        g = Graph(bind_namespaces="none")
        g.parse(data=ontology_content, format="turtle")
        base_ns = ns_utils.derive_base_namespace(g)

    logger.info(f"[build] target={target.get('iri')} type={target.get('type')} model={model_id}")
    ontology_info = _build_ontology_info(ontology_content, target)

    prompt_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "text2shacl_core", "prompts")
    prompt_file = os.path.join(prompt_dir, "rule_general.json")

    gen_model = get_chat_llm(model_id, kind="generator", temperature=temperature,
                             max_new_tokens=DEFAULT_GEN_MAX_NEW_TOKENS)

    attempt = 0
    error_message = None
    last_result = ""

    while attempt < MAX_RETRIES:
        key = "generator_with_error" if error_message else "generator"
        logger.debug(f"[build] attempt {attempt + 1}/{MAX_RETRIES} using prompt '{key}'")
        prompt = load_prompt_from_json(prompt_file, key)
        chain = prompt | gen_model | StrOutputParser()

        invoke_vars = {
            "property": target.get("full_iri") or target.get("iri"),
            "prefixes": prefixes,
            "ontology_info": ontology_info,
            "domain_context": domain_context,
            "rule": rule,
            "generation_guidance": generation_guidance,
            "shacl_history": "(none)",
        }
        if error_message:
            invoke_vars["previous_invalid_shapes"] = last_result
            invoke_vars["error"] = error_message

        try:
            result = chain.invoke(invoke_vars)
        except Exception as e:
            # Backend/credentials/endpoint error — NOT a Turtle parse error.
            logger.error(f"[build] backend error: {e}")
            return {"shape": "", "valid": False, "error": str(e), "attempts": attempt,
                    "hints": [], "fallback": False, "error_type": "backend",
                    "message": f"Backend error calling the model: {e}"}
        last_result = clean_shacl_response(result)

        if "SHACL shapes not found" in result:
            logger.info("[build] model reported: SHACL shapes not found")
            return {"shape": "", "valid": False, "error": None, "attempts": attempt + 1,
                    "hints": [], "fallback": False, "not_found": True,
                    "message": "The model reported no shape can be justified from this rule and context."}

        try:
            Graph(bind_namespaces="none").parse(data=f"{prefixes}\n{last_result}", format="turtle")
        except Exception as e:
            logger.warn(f"[build] parse failed on attempt {attempt + 1}: {e}")
            error_message = str(e)
            attempt += 1
            continue

        logger.info(f"[build] valid SHACL on attempt {attempt + 1}")
        hints = _hints_from_shape(last_result, prefixes)
        return {"shape": last_result, "valid": True, "error": None, "attempts": attempt + 1,
                "hints": hints, "fallback": False, "error_type": "none",
                "message": f"Valid SHACL generated by '{model_id}' (attempt {attempt + 1})."}

    # Retries exhausted: return the invalid shape with the parse error.
    logger.error(f"[build] exhausted {MAX_RETRIES} attempts; last parse error: {error_message}")
    return {"shape": last_result, "valid": False, "error": error_message, "attempts": MAX_RETRIES,
            "hints": [], "fallback": False, "error_type": "parse",
            "message": f"Reached {MAX_RETRIES} attempts; returning last output with its parse error."}


def validate_model(payload):
    """Lightweight availability check before a custom model is added in the UI."""
    provider = str(payload.get("provider") or "").strip().lower()
    model = str(payload.get("model") or "").strip()
    role = str(payload.get("role") or "chat").strip().lower()

    if not model:
        return {"ok": False, "message": "Enter a model id first."}
    if provider not in {"databricks", "huggingface"}:
        return {"ok": False, "message": "Choose Databricks or Hugging Face first."}

    if provider == "databricks":
        import httpx
        from runtime_config import get_databricks_base_url, get_databricks_token

        token = get_databricks_token()
        base_url = get_databricks_base_url()
        if not token or not base_url:
            return {
                "ok": False,
                "message": "Databricks token and base URL are required to validate this model.",
            }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if role == "embedding":
            url = f"{base_url}/embeddings"
            body = {"model": model, "input": ["ping"]}
        else:
            url = f"{base_url}/chat/completions"
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            }

        try:
            res = httpx.post(url, headers=headers, json=body, timeout=20)
        except Exception as exc:
            return {"ok": False, "message": f"Could not reach Databricks endpoint: {exc}"}

        if 200 <= res.status_code < 300:
            return {"ok": True, "message": f"Model '{model}' is available."}
        detail = res.text[:500]
        return {
            "ok": False,
            "message": f"Databricks rejected '{model}' ({res.status_code}): {detail}",
        }

    # Hugging Face: check repository visibility/access without downloading weights.
    try:
        from huggingface_hub import model_info
        from runtime_config import get_hf_token

        info = model_info(model, token=get_hf_token() or None)
        pipeline = getattr(info, "pipeline_tag", None)
        suffix = f" ({pipeline})" if pipeline else ""
        return {"ok": True, "message": f"Model '{model}' is available on Hugging Face{suffix}."}
    except Exception as exc:
        return {"ok": False, "message": f"Hugging Face model '{model}' is not available: {exc}"}


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
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")

        # Real rdflib Turtle validation for the editor "Check" button.
        if self.path == "/validate-shape":
            shape = payload.get("shape", "")
            prefixes = payload.get("prefixes", "")
            try:
                Graph(bind_namespaces="none").parse(data=f"{prefixes}\n{shape}", format="turtle")
                self._send_json(200, {"valid": True, "error": None})
            except Exception as exc:
                self._send_json(200, {"valid": False, "error": str(exc)})
            return

        if self.path == "/validate-model":
            from runtime_config import inference_config
            with inference_config(_runtime_config(payload)):
                self._send_json(200, validate_model(payload))
            return

        if self.path != "/build-shacl-shape":
            self._send_json(404, {"error": "unknown endpoint"})
            return

        # Capture the original project's debug prints (Logger + model_loader)
        # emitted during this generation, and return them to the UI Logs panel.
        import contextlib, io
        from Logger import logger
        logger.set_verbosity(3)
        buf = io.StringIO()
        try:
            from runtime_config import inference_config
            with contextlib.redirect_stdout(buf), inference_config(_runtime_config(payload)):
                result = build_shape(payload)
        except Exception as exc:
            self._send_json(200, {"shape": "", "valid": False, "error": str(exc),
                                  "attempts": 0, "hints": [], "fallback": True,
                                  "logs": buf.getvalue(),
                                  "message": f"build-shacl-shape failed: {exc}"})
            return
        self._send_json(200, {"provider": payload.get("provider"),
                              "model": payload.get("model"),
                              "logs": buf.getvalue(), **result})


if __name__ == "__main__":
    print(f"build-shacl-shape service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
