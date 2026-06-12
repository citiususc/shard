#!/usr/bin/env python3
"""Service for building SHACL shapes from a rule and selected target."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re


class MissingOpenAIError(Exception):
    pass


try:
    from openai import APIConnectionError, APIError, APITimeoutError, OpenAI
except ImportError:
    APIConnectionError = APIError = APITimeoutError = None
    OpenAI = None

OPENAI_ERRORS = tuple(error for error in (APIConnectionError, APIError, APITimeoutError) if error) or (MissingOpenAIError,)


HOST = "127.0.0.1"
PORT = 9102
DATABRICKS_BASE_URL = "https://dbc-89ad3629-477e.cloud.databricks.com/ai-gateway/mlflow/v1"


def shape_name(target):
    label = target.get("label") or "target"
    compact = re.sub(r"[^A-Za-z0-9]+", " ", label).strip().title().replace(" ", "")
    return f"ex:{compact or 'Target'}Shape"


def build_constraints(rule, target):
    constraints = []
    hints = []
    kind = target.get("kind", "")
    target_type = target.get("type", "")
    range_value = target.get("range", "")

    if target_type == "property":
        if re.search(r"must|shall|required|mandatory|every|each", rule, re.I):
            constraints.append("sh:minCount 1")
            hints.append({"reason": "dummy service detected required value", "constraint": "sh:minCount 1"})
        if re.search(r"exactly one|single|unique|only one", rule, re.I):
            constraints.append("sh:maxCount 1")
            hints.append({"reason": "dummy service detected single value", "constraint": "sh:maxCount 1"})
        if re.search(r"greater than 0|positive|above 0", rule, re.I):
            constraints.append("sh:minInclusive 1")
            hints.append({"reason": "dummy service detected positive numeric rule", "constraint": "sh:minInclusive 1"})
        if re.search(r"email|e-mail", rule, re.I):
            constraints.append('sh:pattern "^[^@\\\\s]+@[^@\\\\s]+\\\\.[^@\\\\s]+$"')
            hints.append({"reason": "dummy service detected email format", "constraint": "sh:pattern"})
        if kind == "ObjectProperty" or (range_value and not range_value.startswith("xsd:")):
            if range_value:
                constraints.append(f"sh:class {range_value}")
                hints.append({"reason": "ontology range used as SHACL class", "constraint": f"sh:class {range_value}"})
            constraints.append("sh:nodeKind sh:IRI")
            hints.append({"reason": "object property values should be IRIs", "constraint": "sh:nodeKind sh:IRI"})
        elif range_value.startswith("xsd:"):
            constraints.append(f"sh:datatype {range_value}")
            hints.append({"reason": "ontology range used as datatype", "constraint": f"sh:datatype {range_value}"})
    else:
        constraints.append('sh:message "Dummy class-level rule: review and add property constraints where needed."')
        hints.append({"reason": "dummy service produced a class-level placeholder", "constraint": "sh:message"})

    if not constraints:
        constraints.append('sh:message "Dummy SHACL proposal: review this rule with a domain expert."')
        hints.append({"reason": "dummy fallback", "constraint": "sh:message"})

    return constraints, hints


def build_shape(payload):
    target = payload.get("target") or {}
    rule = payload.get("business_rule", "")
    prefixes = payload.get("prefixes") or "@prefix ex: <https://example.org/shapes/> .\n@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
    constraints, hints = build_constraints(rule, target)

    if target.get("type") == "class":
        header = [
            f"{shape_name(target)} a sh:NodeShape ;",
            f"  sh:targetClass {target.get('iri', 'ex:Class')} ;",
        ]
    else:
        header = [
            f"{shape_name(target)} a sh:PropertyShape ;",
            f"  sh:targetClass {target.get('domain') or 'ex:Class'} ;",
            f"  sh:path {target.get('iri') or 'ex:property'} ;",
        ]

    body = [f"  {line}{' .' if index == len(constraints) - 1 else ' ;'}" for index, line in enumerate(constraints)]
    return f"{prefixes.strip()}\n\n" + "\n".join(header + body), hints


def target_summary(target):
    return json.dumps(
        {
            "type": target.get("type"),
            "label": target.get("label"),
            "iri": target.get("iri"),
            "kind": target.get("kind"),
            "domain": target.get("domain"),
            "range": target.get("range"),
            "ontologyNote": target.get("ontologyNote"),
        },
        ensure_ascii=False,
        indent=2,
    )


def databricks_messages(payload):
    return [
        {
            "role": "system",
            "content": (
                "You generate SHACL shapes from business rules and ontology context. "
                "Return only valid Turtle. Do not wrap the answer in Markdown. "
                "Use the provided prefixes and do not invent ontology terms."
            ),
        },
        {
            "role": "user",
            "content": (
                "Build a SHACL shape for the selected ontology target.\n\n"
                "--- PREFIXES ---\n"
                f"{payload.get('prefixes') or ''}\n\n"
                "--- BUSINESS RULE ---\n"
                f"{payload.get('business_rule') or ''}\n\n"
                "--- SELECTED TARGET ---\n"
                f"{target_summary(payload.get('target') or {})}\n\n"
                "If the target is a property, create a sh:PropertyShape with sh:targetClass and sh:path. "
                "If the target is a class, create a sh:NodeShape with sh:targetClass. "
                "Add only constraints supported by the rule or ontology context."
            ),
        },
    ]


def strip_markdown_fence(content):
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def call_databricks(payload):
    api_key = payload.get("api_key")
    model = payload.get("model")
    timeout = float(payload.get("timeout") or 10)
    if not api_key:
        raise ValueError("Missing Databricks API key")
    if not model:
        raise ValueError("Missing Databricks model")
    if OpenAI is None:
        raise ValueError("Missing openai package. Run: python3 -m pip install -r requirements.txt")

    client = OpenAI(
        api_key=api_key,
        base_url=DATABRICKS_BASE_URL,
        timeout=timeout,
        max_retries=0,
    )

    completion = client.chat.completions.create(
        messages=databricks_messages(payload),
        model=model,
        max_tokens=int(payload.get("max_tokens") or 1024),
        timeout=timeout,
    )
    response_payload = completion.model_dump()
    content = completion.choices[0].message.content or ""
    return strip_markdown_fence(content), response_payload


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
        if self.path != "/build-shacl-shape":
            self._send_json(404, {"error": "unknown endpoint"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        hints = []
        raw_response = None
        used_fallback = False
        try:
            shape, raw_response = call_databricks(payload)
            hints = [{"reason": "Databricks response content", "constraint": "choices[0].message.content"}]
            message = "Databricks SHACL generation completed."
        except (ValueError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            shape, hints = build_shape(payload)
            used_fallback = True
            message = f"Databricks call failed; returned dummy fallback. {exc}"
        except OPENAI_ERRORS as exc:
            shape, hints = build_shape(payload)
            used_fallback = True
            message = f"Databricks call failed; returned dummy fallback. {exc}"

        self._send_json(
            200,
            {
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "shape": shape,
                "hints": hints,
                "raw_content": shape,
                "raw_response": raw_response,
                "fallback": used_fallback,
                "message": message,
            },
        )


if __name__ == "__main__":
    print(f"build-shacl-shape service listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
