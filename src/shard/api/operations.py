"""HTTP adapters for SHARD application operations.

Application modules return Python values and do not know about HTTP. This
module is the single translation layer used by the canonical API and the
optional compatibility listeners.
"""

from __future__ import annotations

import json
import traceback
from typing import Any, Dict, Mapping

from shard.api.http import reject_disabled_provider, send_json, send_provenance_headers
from shard.application.baseline_generation import generate_astrea_baseline
from shard.application.guide_generation import (
    parse_business_rules_template,
    prepare_astrea_graph,
    resolver_llm_from_payload,
    runtime_config_from_payload,
    stream_guide_generation,
)
from shard.application.model_check import validate_model
from shard.application.ontology_catalog import parse_ontology
from shard.application.shape_generation import build_shape
from shard.application.shape_merge import merge_shapes
from shard.application.shape_validation import (
    validate_shape_content,
    validation_profiles_from_payload,
)
from shard.application.target_resolution import resolve_template
from shard.application.term_ranking import (
    cancel_embeddings,
    embedding_status,
    prepare_embeddings,
    rank_terms,
)
from shard.application.workflows import (
    generate_guide_workflow,
    generate_rule_workflow,
    normalize_workflow_payload,
)
from shard.domain.ontology import parse_ontology_graph
from shard.inference.context import inference_config
from shard.inference.local_store import download_local_model, local_model_status
from shard.integrations.astrea import AstreaResponseError, AstreaUnavailableError
from shard.observability import logger


TERM_OPERATIONS = {
    "ontology.search": rank_terms,
    "ontology.index.prepare": prepare_embeddings,
    "ontology.index.status": embedding_status,
    "ontology.index.cancel": cancel_embeddings,
}

WORKFLOW_OPERATIONS = {
    "workflows.rule.generate": generate_rule_workflow,
    "workflows.guide.generate": generate_guide_workflow,
}

_SECRET_FIELDS = {
    "api_key",
    "authorization",
    "databricks_token",
    "hf_token",
    "token",
}


def _request_secrets(value: Any) -> list[str]:
    secrets = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().lower() in _SECRET_FIELDS:
                secret = str(child or "")
                if secret:
                    secrets.append(secret)
            else:
                secrets.extend(_request_secrets(child))
    elif isinstance(value, list):
        for child in value:
            secrets.extend(_request_secrets(child))
    return secrets


def _redact_request_secrets(value: Any, payload: Mapping[str, Any]) -> str:
    text = str(value or "")
    for secret in sorted(set(_request_secrets(payload)), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def _request_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("inference_config") or payload.get("model_config") or payload


def _handle_ontology_parse(handler, payload: Dict[str, Any], request_id: str) -> None:
    try:
        result = parse_ontology(payload.get("filename", ""), payload.get("content", ""))
    except Exception as exc:
        send_json(handler, 400, {
            "error": str(exc),
            "entities": [],
            "prefixes": "",
            "base_namespace": "",
            "shape_namespace": "",
            "shape_prefix": "",
            "namespace_analysis": {},
        }, request_id=request_id)
        return
    send_json(handler, 200, result, request_id=request_id)


def _handle_term_operation(
    handler,
    operation: str,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    function = TERM_OPERATIONS.get(operation)
    if function is None:
        send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)
        return
    if reject_disabled_provider(handler, payload, request_id=request_id):
        return
    with logger.request_context(request_id), inference_config(_request_config(payload)):
        result = function(payload)
    send_json(handler, 200, {
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        **result,
    }, request_id=request_id)


def _handle_target_resolution(handler, payload: Dict[str, Any], request_id: str) -> None:
    if reject_disabled_provider(handler, payload, request_id=request_id):
        return
    with logger.request_context(request_id), inference_config(_request_config(payload)):
        try:
            result = resolve_template(
                payload,
                llm=resolver_llm_from_payload(payload),
            )
        except ValueError as exc:
            send_json(handler, 400, {"error": str(exc)}, request_id=request_id)
            return
        except Exception as exc:
            send_json(handler, 500, {"error": str(exc)}, request_id=request_id)
            return
    send_json(handler, 200, result, request_id=request_id)


def _handle_shape_operation(
    handler,
    operation: str,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    if operation == "baselines.astrea.generate":
        try:
            result = generate_astrea_baseline(payload)
        except AstreaUnavailableError as exc:
            send_json(handler, 503, {
                "available": False,
                "error": str(exc),
                "error_type": "astrea_unavailable",
                "message": "Astrea is currently unavailable. SHARD will continue without it.",
            }, request_id=request_id)
            return
        except AstreaResponseError as exc:
            send_json(handler, 502, {
                "available": False,
                "error": str(exc),
                "error_type": "astrea_response",
                "message": "Astrea did not return a usable SHACL baseline.",
            }, request_id=request_id)
            return
        except ValueError as exc:
            send_json(handler, 400, {
                "available": False,
                "error": str(exc),
                "error_type": "ontology",
                "message": "The ontology could not be prepared for Astrea.",
            }, request_id=request_id)
            return
        except Exception as exc:
            send_json(handler, 500, {
                "available": False,
                "error": str(exc),
                "error_type": "service",
                "message": "Astrea baseline generation failed.",
            }, request_id=request_id)
            return
        send_json(handler, 200, result, request_id=request_id)
        return

    if operation == "shapes.validate":
        result = validate_shape_content(
            payload.get("shape", ""),
            payload.get("prefixes", ""),
            validation_profiles_from_payload(payload),
        )
        send_json(handler, 200, result, request_id=request_id)
        return

    if operation == "shapes.merge":
        try:
            result = merge_shapes(payload)
        except ValueError as exc:
            send_json(handler, 400, {
                "valid": False,
                "error": str(exc),
                "error_type": "merge",
            }, request_id=request_id)
            return
        except Exception as exc:
            send_json(handler, 500, {
                "valid": False,
                "error": str(exc),
                "error_type": "merge",
            }, request_id=request_id)
            return
        send_json(handler, 200, result, request_id=request_id)
        return

    if reject_disabled_provider(handler, payload, request_id=request_id):
        return

    if operation == "models.check":
        with inference_config(_request_config(payload)):
            result = validate_model(payload)
        send_json(handler, 200, result, request_id=request_id)
        return

    if operation != "shapes.build":
        send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)
        return

    logger.set_verbosity(3)
    log_lines = []
    try:
        with logger.request_context(request_id) as log_lines, inference_config(_request_config(payload)):
            result = build_shape(payload)
    except Exception as exc:
        status = 400 if isinstance(exc, ValueError) else 500
        error_type = "request" if status == 400 else "service"
        send_json(handler, status, {
            "shape": "",
            "valid": False,
            "error": str(exc),
            "attempts": 0,
            "hints": [],
            "fallback": True,
            "logs": "\n".join(log_lines),
            "error_type": error_type,
            "message": f"Shape generation failed: {exc}",
        }, request_id=request_id)
        return
    status = 502 if result.get("error_type") == "backend" else 200
    send_json(handler, status, {
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "logs": "\n".join(log_lines),
        **result,
    }, request_id=request_id)


def _handle_local_model_status(
    handler,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    request_payload = {**payload, "provider": "huggingface"}
    if reject_disabled_provider(handler, request_payload, request_id=request_id):
        return
    try:
        with inference_config(_request_config(request_payload)):
            result = local_model_status(request_payload.get("model", ""))
    except ValueError as exc:
        send_json(handler, 400, {"error": str(exc)}, request_id=request_id)
        return
    send_json(handler, 200, result, request_id=request_id)


def _send_local_model_event(handler, event: Dict[str, Any], request_id: str) -> None:
    document = dict(event)
    document.setdefault("request_id", request_id)
    provenance = getattr(handler, "response_provenance", None)
    if provenance and "provenance" not in document:
        document["provenance"] = provenance
    handler.wfile.write(
        f"data: {json.dumps(document, ensure_ascii=False)}\n\n".encode("utf-8")
    )
    handler.wfile.flush()


def _handle_local_model_download(
    handler,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    request_payload = {**payload, "provider": "huggingface"}
    if reject_disabled_provider(handler, request_payload, request_id=request_id):
        return
    model_id = str(request_payload.get("model") or "").strip()
    if not model_id or "/" not in model_id:
        send_json(
            handler,
            400,
            {"error": "A repository-style local model id is required."},
            request_id=request_id,
        )
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("X-Request-ID", request_id)
    send_provenance_headers(handler)
    handler.end_headers()

    emit = lambda event: _send_local_model_event(handler, event, request_id)
    try:
        with inference_config(_request_config(request_payload)):
            download_local_model(model_id, emit)
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception as exc:
        emit({
            "type": "error",
            "model": model_id,
            "message": str(exc),
        })


def send_guide_event(handler, event: Dict[str, Any], request_id: str) -> None:
    """Write one SSE event with request provenance."""
    document = dict(event)
    document.setdefault("request_id", request_id)
    provenance = getattr(handler, "response_provenance", None)
    if provenance and "provenance" not in document:
        document["provenance"] = provenance
    handler.wfile.write(
        f"data: {json.dumps(document, ensure_ascii=False)}\n\n".encode("utf-8")
    )
    handler.wfile.flush()


def _handle_guide_generation(handler, payload: Dict[str, Any], request_id: str) -> None:
    if reject_disabled_provider(handler, payload, request_id=request_id):
        return
    if not payload.get("ontology_content"):
        send_json(handler, 400, {"error": "Missing ontology_content."}, request_id=request_id)
        return
    try:
        payload["_business_rules"] = parse_business_rules_template(
            payload.get("guide_content", ""),
            payload.get("guide_filename", ""),
        )
        parse_ontology_graph(
            payload.get("ontology_content", ""),
            payload.get("ontology_filename", "ontology.ttl"),
        )
        prepare_astrea_graph(payload)
    except Exception as exc:
        send_json(handler, 400, {"error": str(exc)}, request_id=request_id)
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("X-Request-ID", request_id)
    send_provenance_headers(handler)
    handler.end_headers()

    emit = lambda event: send_guide_event(handler, event, request_id)
    try:
        with logger.request_context(request_id), inference_config(
            runtime_config_from_payload(payload)
        ):
            stream_guide_generation(payload, emit)
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception as exc:
        emit({
            "type": "error",
            "message": str(exc),
            "trace": traceback.format_exc()[-1500:],
        })


def _handle_workflow(
    handler,
    operation: str,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    """Execute one complete JSON workflow through existing application services."""
    workflow = WORKFLOW_OPERATIONS.get(operation)
    if workflow is None:
        send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)
        return

    normalized = normalize_workflow_payload(payload)
    if reject_disabled_provider(handler, normalized, request_id=request_id):
        return

    log_lines = []
    try:
        with logger.request_context(request_id) as log_lines, inference_config(
            runtime_config_from_payload(normalized)
        ):
            result = workflow(normalized)
    except ValueError as exc:
        send_json(handler, 400, {
            "error": "invalid_request",
            "code": "invalid_request",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except AstreaUnavailableError as exc:
        send_json(handler, 503, {
            "error": "astrea_unavailable",
            "code": "astrea_unavailable",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except AstreaResponseError as exc:
        send_json(handler, 502, {
            "error": "astrea_response",
            "code": "astrea_response",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except TimeoutError as exc:
        send_json(handler, 504, {
            "error": "workflow_timeout",
            "code": "workflow_timeout",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except Exception as exc:
        send_json(handler, 500, {
            "error": "workflow_failed",
            "code": "workflow_failed",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return

    send_json(handler, 200, {
        **result,
        "logs": _redact_request_secrets("\n".join(log_lines), normalized),
    }, request_id=request_id)


def dispatch_post_operation(
    handler,
    operation: str,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    """Execute one stable API operation through its HTTP adapter."""
    if operation == "ontology.parse":
        _handle_ontology_parse(handler, payload, request_id)
    elif operation.startswith("ontology."):
        _handle_term_operation(handler, operation, payload, request_id)
    elif operation == "rules.resolve-targets":
        _handle_target_resolution(handler, payload, request_id)
    elif operation in {
        "baselines.astrea.generate",
        "shapes.build",
        "shapes.validate",
        "shapes.merge",
        "models.check",
    }:
        _handle_shape_operation(handler, operation, payload, request_id)
    elif operation == "models.local.status":
        _handle_local_model_status(handler, payload, request_id)
    elif operation == "models.local.download":
        _handle_local_model_download(handler, payload, request_id)
    elif operation == "guides.generate":
        _handle_guide_generation(handler, payload, request_id)
    elif operation in WORKFLOW_OPERATIONS:
        _handle_workflow(handler, operation, payload, request_id)
    else:
        send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)
