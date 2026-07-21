"""HTTP adapters for SHARD application operations.

Application modules return Python values and do not know about HTTP. This
module is the single translation layer used by the canonical API and the
optional compatibility listeners.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Mapping

from shard.api.http import (
    reject_disabled_provider,
    send_cors_headers,
    send_json,
    send_provenance_headers,
)
from shard.api.errors import ResourceNotFound
from shard.api.jobs import JOBS
from shard.api.models import score_kind_for_resolution
from shard.api.sse import SseWriter
from shard.application.baseline_generation import generate_astrea_baseline
from shard.application.batch_generation import (
    parse_business_rules_template,
    prepare_astrea_graph,
    resolver_llm_from_payload,
    runtime_config_from_payload,
    stream_batch_generation,
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
    generate_batch_workflow,
    generate_rule_workflow,
    normalize_workflow_payload,
)
from shard.domain.ontology import parse_ontology_graph
from shard.inference.context import inference_config
from shard.inference.local_store import download_local_model, local_model_status
from shard.integrations.astrea import (
    AstreaResponseError,
    AstreaRateLimitError,
    AstreaTimeoutError,
    AstreaUnavailableError,
)
from shard.observability import logger


TERM_OPERATIONS = {
    "ontology.search": rank_terms,
}

WORKFLOW_OPERATIONS = {
    "workflows.rule.generate": generate_rule_workflow,
    "workflows.batch.generate": generate_batch_workflow,
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
            "error": "invalid_request",
            "code": "INVALID_ONTOLOGY",
            "message": _redact_request_secrets(exc, payload),
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
        "embedding_model": payload.get("embedding_model"),
        **result,
    }, request_id=request_id)


def _embedding_job_worker(payload: Dict[str, Any]):
    """Return a worker that mirrors the existing embedding cache job as a public job."""
    def worker(_job_id, cancel_event, update):
        state = prepare_embeddings(payload)
        if state.get("status") in {"disabled", "error", "cancelled", "none"}:
            raise RuntimeError(state.get("message") or "Embedding index could not be prepared.")
        if state.get("status") == "ready":
            total = int(state.get("total") or state.get("completed") or 0)
            update(
                progress=1.0,
                completed_terms=total,
                total_terms=total,
                message=str(state.get("message") or "Ontology embedding index is ready."),
            )
            return state
        while True:
            if cancel_event.is_set():
                cancel_embeddings(payload)
                return state
            state = embedding_status({
                **payload,
                "ontology_fingerprint": state.get("ontology_fingerprint"),
            })
            completed = int(state.get("completed") or 0)
            total = int(state.get("total") or 0)
            update(
                progress=(completed / total) if total else 0.0,
                completed_terms=completed,
                total_terms=total,
                message=str(state.get("message") or "Preparing ontology embeddings."),
            )
            if state.get("status") == "ready":
                return state
            if state.get("status") in {"disabled", "error", "cancelled", "missing"}:
                raise RuntimeError(state.get("message") or "Embedding index preparation failed.")
            time.sleep(0.2)
    return worker


def _handle_index_job_create(handler, payload: Dict[str, Any], request_id: str) -> None:
    if reject_disabled_provider(handler, payload, request_id=request_id):
        return
    job = JOBS.create(
        "ontology-index",
        _embedding_job_worker(payload),
        message="Ontology embedding index is queued.",
    )
    send_json(handler, 202, job, request_id=request_id)


def _handle_job_operation(handler, operation: str, request_id: str) -> None:
    job_id = str(getattr(handler, "path_params", {}).get("job_id") or "")
    try:
        job = JOBS.cancel(job_id) if operation.endswith(".delete") else JOBS.get(job_id)
    except ResourceNotFound as exc:
        code = (
            "ONTOLOGY_INDEX_JOB_NOT_FOUND"
            if operation.startswith("ontology.index.")
            else "LOCAL_MODEL_DOWNLOAD_JOB_NOT_FOUND"
        )
        raise ResourceNotFound(
            "No job exists with the supplied identifier.",
            {"job_id": job_id},
            code=code,
        ) from exc
    if operation.startswith("ontology.index."):
        job.setdefault("completed_terms", 0)
        job.setdefault("total_terms", 0)
    send_json(handler, 200, job, request_id=request_id)


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
            send_json(handler, 400, {
                "error": "invalid_request",
                "code": "INVALID_TARGET_RESOLUTION_REQUEST",
                "message": _redact_request_secrets(exc, payload),
            }, request_id=request_id)
            return
        except Exception:
            send_json(handler, 500, {
                "error": "internal_failure",
                "code": "UNEXPECTED_INTERNAL_ERROR",
                "message": "Target resolution failed unexpectedly.",
            }, request_id=request_id)
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
        except AstreaRateLimitError:
            send_json(handler, 429, {
                "error": "rate_limit_exceeded",
                "code": "ASTREA_RATE_LIMIT_EXCEEDED",
                "message": "Astrea rate limited the request.",
            }, request_id=request_id)
            return
        except AstreaTimeoutError:
            send_json(handler, 504, {
                "error": "upstream_timeout",
                "code": "ASTREA_REQUEST_TIMEOUT",
                "message": "Astrea did not respond before the configured timeout.",
                "details": {"provider": "astrea"},
            }, request_id=request_id)
            return
        except AstreaUnavailableError as exc:
            send_json(handler, 503, {
                "error": "upstream_unavailable",
                "code": "ASTREA_UNAVAILABLE",
                "message": "Astrea is currently unavailable.",
                "details": {"provider": "astrea"},
            }, request_id=request_id)
            return
        except AstreaResponseError as exc:
            send_json(handler, 502, {
                "error": "invalid_upstream_response",
                "code": "ASTREA_INVALID_RESPONSE",
                "message": "Astrea did not return a usable SHACL baseline.",
                "details": {"provider": "astrea"},
            }, request_id=request_id)
            return
        except ValueError as exc:
            send_json(handler, 400, {
                "error": "invalid_request",
                "code": "INVALID_ONTOLOGY",
                "message": "The ontology could not be prepared for Astrea.",
            }, request_id=request_id)
            return
        except Exception:
            send_json(handler, 500, {
                "error": "internal_failure",
                "code": "UNEXPECTED_INTERNAL_ERROR",
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
                "error": "invalid_request",
                "code": "INVALID_MERGE_INPUT",
                "message": _redact_request_secrets(exc, payload),
            }, request_id=request_id)
            return
        except Exception:
            send_json(handler, 500, {
                "error": "internal_failure",
                "code": "UNEXPECTED_INTERNAL_ERROR",
                "message": "Shape merge failed unexpectedly.",
            }, request_id=request_id)
            return
        send_json(handler, 200, result, request_id=request_id)
        return

    if reject_disabled_provider(handler, payload, request_id=request_id):
        return

    if operation == "models.check":
        with inference_config(_request_config(payload)):
            result = {
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                **validate_model(payload),
            }
        if getattr(handler, "api_is_canonical", False) and not result.get("ok"):
            error_code = str(result.get("error_code") or "provider_unavailable")
            status = {
                "provider_authentication_failed": 401,
                "rate_limited": 429,
                "provider_timeout": 504,
            }.get(error_code, 503)
            details = {
                "provider": payload.get("provider"),
                **(
                    {"upstream_status": result.get("upstream_status")}
                    if result.get("upstream_status") is not None
                    else {}
                ),
            }
            send_json(handler, status, {
                "error": {
                    401: "provider_authentication_failed",
                    429: "rate_limit_exceeded",
                    503: "upstream_unavailable",
                    504: "upstream_timeout",
                }[status],
                "code": {
                    401: "PROVIDER_AUTHENTICATION_FAILED",
                    429: "MODEL_RATE_LIMIT_EXCEEDED",
                    503: "MODEL_UNAVAILABLE",
                    504: "MODEL_REQUEST_TIMEOUT",
                }[status],
                "message": str(result.get("message") or "The model is unavailable."),
                "details": details,
            }, request_id=request_id)
            return
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
        send_json(
            handler,
            status,
            {
                "error": "invalid_request" if status == 400 else "internal_failure",
                "code": (
                    "INVALID_GROUNDED_RULE_CONTEXT"
                    if status == 400
                    else "UNEXPECTED_INTERNAL_ERROR"
                ),
                "message": (
                    _redact_request_secrets(exc, payload)
                    if status == 400
                    else "Shape generation failed unexpectedly."
                ),
            },
            request_id=request_id,
        )
        return
    status = {
        "backend": 503,
        "timeout": 504,
    }.get(result.get("error_type"), 200)
    if status >= 400 and getattr(handler, "api_is_canonical", False):
        send_json(handler, status, {
            "error": "upstream_timeout" if status == 504 else "upstream_unavailable",
            "code": "MODEL_REQUEST_TIMEOUT" if status == 504 else "MODEL_UNAVAILABLE",
            "message": (
                "The generation provider did not respond before the timeout."
                if status == 504
                else "The generation provider could not complete the request."
            ),
            "details": {"provider": payload.get("provider")},
        }, request_id=request_id)
        return
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
        send_json(handler, 400, {
            "error": "invalid_request",
            "code": "INVALID_MODEL_ID",
            "message": _redact_request_secrets(exc, request_payload),
        }, request_id=request_id)
        return
    send_json(handler, 200, result, request_id=request_id)


def _local_download_job_worker(model_id: str):
    def worker(_job_id, cancel_event, update):
        def emit(event):
            if cancel_event.is_set():
                return
            percent = float(event.get("percent") or 0)
            update(
                progress=max(0.0, min(1.0, percent / 100.0)),
                message=str(event.get("message") or "Downloading local model."),
            )
        return download_local_model(model_id, emit)
    return worker


def _handle_local_download_job_create(
    handler,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    request_payload = {**payload, "provider": "huggingface"}
    if reject_disabled_provider(handler, request_payload, request_id=request_id):
        return
    model_id = str(payload.get("model") or "").strip()
    job = JOBS.create(
        "local-model-download",
        _local_download_job_worker(model_id),
        message="Local model download is queued.",
    )
    send_json(handler, 202, job, request_id=request_id)


def _handle_batch_generation(handler, payload: Dict[str, Any], request_id: str) -> None:
    if reject_disabled_provider(handler, payload, request_id=request_id):
        return
    if not payload.get("ontology_content"):
        send_json(handler, 400, {
            "error": "invalid_request",
            "code": "MISSING_ONTOLOGY",
            "message": "An ontology document is required.",
        }, request_id=request_id)
        return
    try:
        payload["_business_rules"] = parse_business_rules_template(
            payload.get("batch_content", ""),
            payload.get("batch_filename", ""),
        )
        parse_ontology_graph(
            payload.get("ontology_content", ""),
            payload.get("ontology_filename", "ontology.ttl"),
        )
        prepare_astrea_graph(payload)
    except Exception as exc:
        send_json(handler, 400, {
            "error": "invalid_request",
            "code": "INVALID_BATCH_INPUT",
            "message": _redact_request_secrets(exc, payload),
        }, request_id=request_id)
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    send_cors_headers(handler)
    handler.send_header("X-Request-ID", request_id)
    send_provenance_headers(handler)
    handler.end_headers()

    writer = SseWriter(handler, request_id)
    writer.start()

    parsed_rules = list((payload.get("_business_rules") or {}).get("rules") or [])
    total_rules = len(parsed_rules)

    def rule_document(event):
        number = str(event.get("rule_number") or "")
        index = int(event.get("current") or event.get("index") or 0)
        candidate = next(
            (item for item in parsed_rules if str(item.get("number") or "") == number),
            parsed_rules[index - 1] if 0 < index <= len(parsed_rules) else {},
        )
        return {
            "number": number or str(candidate.get("number") or f"RULE-{max(index, 1):03d}"),
            "title": str(event.get("title") or candidate.get("title") or "Data constraint"),
            "text": str(
                candidate.get("text")
                or candidate.get("business_rule")
                or event.get("business_rule")
                or "Data constraint text unavailable."
            ),
        }

    def target_roles(event):
        return {
            name: [{"iri": str(item)} for item in event.get(name) or [] if item]
            for name in ("focus_nodes", "constraint_paths", "related_terms")
        }

    def progress_payload(event, message):
        current = int(event.get("current", event.get("index", 0)) or 0)
        total = int(event.get("total") or total_rules or 0)
        completed = max(0, min(current, total)) if total else 0
        return {
            "message": message,
            "completed_items": completed,
            "total_items": total,
            "progress": (completed / total) if total else 0.0,
            "completed_rules": completed,
            "total_rules": total,
        }

    def emit(event):
        event_type = str(event.get("type") or "status")
        stage = str(event.get("stage") or "")
        message = str(event.get("message") or event.get("error") or "")
        extensions = {"source_type": event_type, "stage": stage, **event}
        if stage == "parsing":
            writer.send("started", {
                "message": message or "Batch generation started.",
                "total_items": total_rules,
                "total_rules": total_rules,
                "extensions": extensions,
            })
            return
        if stage == "resolution":
            resolved_by = str(event.get("resolved_by") or "none")
            writer.send("rule_resolved", {
                "rule": rule_document(event),
                "target_roles": target_roles(event),
                "resolved_by": resolved_by,
                "resolution_score": event.get("resolution_score", event.get("confidence")),
                "score_kind": score_kind_for_resolution(resolved_by),
                "extensions": extensions,
            })
            return
        if event_type == "shape":
            target = {"iri": str(event["target"])} if event.get("target") else None
            valid = str(event.get("status") or "") == "valid"
            target_total = 1 if target else 0
            writer.send("shape_generated", {
                "rule": rule_document(event),
                "target": target,
                "target_index": 1 if target else 0,
                "target_total": target_total,
                "shape_document": str(event.get("shape") or ""),
                "valid": valid,
                "attempts": int(event.get("attempts") or 0),
                "llm_review_applied": bool(event.get("llm_review_applied")),
                "review_attempts": int(event.get("review_attempts") or 0),
                "semantic_review": event.get("semantic_review") or {},
                "error_type": str(event.get("error_type") or "none"),
                "message": message,
                "extensions": extensions,
            })
            if event.get("validation_level"):
                writer.send("validation_completed", {
                    "rule_number": rule_document(event)["number"],
                    "target": target,
                    "validation": {
                        "valid": valid,
                        "syntax_valid": bool(event.get("syntax_valid")),
                        "profile_valid": bool(event.get("profile_valid")),
                        "profile_count": int(event.get("profile_count") or 0),
                        "profile_names": event.get("profile_names") or [],
                        "generic_profile_active": bool(event.get("generic_profile_active", True)),
                        "generic_profile_name": str(event.get("generic_profile_name") or "shacl-shacl.ttl"),
                        "domain_profile_count": int(event.get("domain_profile_count") or 0),
                        "domain_profile_names": event.get("domain_profile_names") or [],
                        "validation_level": str(event.get("validation_level")),
                        "error": event.get("error") if not valid else None,
                        "error_type": str(event.get("error_type") or "none"),
                        "report_text": str(event.get("report_text") or ""),
                        "message": message,
                    },
                    "extensions": {"source_type": "validation", "stage": "validation"},
                })
            return
        if event_type == "done":
            total = int(event.get("total") or total_rules)
            writer.send("completed", {
                "message": message or "Batch generation completed.",
                "completed_items": total,
                "total_items": total,
                "completed_rules": total,
                "total_rules": total,
                "final_shape_document": str(event.get("shape_document") or ""),
                "extensions": extensions,
            })
            return
        if event_type == "warning":
            writer.send("warning", {
                "code": str(event.get("code") or "BATCH_GENERATION_WARNING"),
                "message": message or "Batch generation warning.",
                "extensions": extensions,
            })
            return
        if event_type == "error":
            writer.send("failed", {
                "error": {
                    "error": "internal_failure",
                    "code": "BATCH_GENERATION_FAILED",
                    "message": message or "Batch generation failed.",
                    "request_id": request_id,
                    "details": {},
                },
                "extensions": extensions,
            })
            return
        writer.send("progress", {
            **progress_payload(event, message or "Batch generation is running."),
            "extensions": extensions,
        })
    try:
        with logger.request_context(request_id), inference_config(
            runtime_config_from_payload(payload)
        ):
            stream_batch_generation(payload, emit)
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception as exc:
        timed_out = isinstance(exc, TimeoutError)
        writer.send("failed", {
            "error": {
                "error": "upstream_timeout" if timed_out else "internal_failure",
                "code": "MODEL_REQUEST_TIMEOUT" if timed_out else "BATCH_GENERATION_FAILED",
                "message": (
                    "The inference provider did not respond before the timeout."
                    if timed_out
                    else _redact_request_secrets(exc, payload)
                ),
                "request_id": request_id,
                "details": (
                    {"provider": str(payload.get("provider") or "configured")}
                    if timed_out
                    else {}
                ),
            },
        })
    finally:
        writer.close()


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
            "code": "INVALID_WORKFLOW_INPUT",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except AstreaRateLimitError as exc:
        send_json(handler, 429, {
            "error": "rate_limit_exceeded",
            "code": "ASTREA_RATE_LIMIT_EXCEEDED",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except AstreaTimeoutError as exc:
        send_json(handler, 504, {
            "error": "upstream_timeout",
            "code": "ASTREA_REQUEST_TIMEOUT",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except AstreaUnavailableError as exc:
        send_json(handler, 503, {
            "error": "upstream_unavailable",
            "code": "ASTREA_UNAVAILABLE",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except AstreaResponseError as exc:
        send_json(handler, 502, {
            "error": "invalid_upstream_response",
            "code": "ASTREA_INVALID_RESPONSE",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except TimeoutError as exc:
        send_json(handler, 504, {
            "error": "upstream_timeout",
            "code": "MODEL_REQUEST_TIMEOUT",
            "message": _redact_request_secrets(exc, normalized),
        }, request_id=request_id)
        return
    except Exception:
        send_json(handler, 500, {
            "error": "internal_failure",
            "code": "UNEXPECTED_INTERNAL_ERROR",
            "message": "The authoring workflow failed unexpectedly.",
        }, request_id=request_id)
        return

    send_json(handler, 200, {
        **result,
        "logs": _redact_request_secrets("\n".join(log_lines), normalized),
    }, request_id=request_id)


def dispatch_operation(
    handler,
    operation: str,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    """Execute one stable API operation through its HTTP adapter."""
    if operation == "ontology.parse":
        _handle_ontology_parse(handler, payload, request_id)
    elif operation == "ontology.index.create":
        _handle_index_job_create(handler, payload, request_id)
    elif operation in {
        "ontology.index.get", "ontology.index.delete",
        "models.local.download.get", "models.local.download.delete",
    }:
        _handle_job_operation(handler, operation, request_id)
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
    elif operation == "models.local.download.create":
        _handle_local_download_job_create(handler, payload, request_id)
    elif operation == "batches.generate":
        _handle_batch_generation(handler, payload, request_id)
    elif operation in WORKFLOW_OPERATIONS:
        _handle_workflow(handler, operation, payload, request_id)
    else:
        send_json(handler, 404, {"error": "unknown endpoint"}, request_id=request_id)


dispatch_post_operation = dispatch_operation
