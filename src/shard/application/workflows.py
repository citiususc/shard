"""High-level SHARD workflows for programmatic API clients.

The web interface calls individual application operations as the user moves
through a review workflow. External clients commonly need the same behavior in
one request. This module composes the existing operations without duplicating
ontology parsing, target resolution, generation, validation, or merge logic.
"""

from __future__ import annotations

from html import escape
from typing import Any, Callable, Dict, Mapping, Optional

from shard.application.baseline_generation import generate_astrea_baseline
from shard.application.batch_generation import generate_batch_shapes
from shard.integrations.astrea import (
    AstreaResponseError,
    AstreaRateLimitError,
    AstreaTimeoutError,
    AstreaUnavailableError,
)


ASTREA_USE_MODES = {"none", "evidence", "merge", "evidence-and-merge"}
ASTREA_USE_MODE_ALIASES = {"baseline": "evidence", "both": "evidence-and-merge"}
ASTREA_FAILURE_POLICIES = {"continue", "fail"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _set_if_present(target: Dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def normalize_workflow_payload(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Translate the developer-facing nested contract to application fields.

    Existing flat application payloads remain valid. When both forms provide a
    value, the explicit nested workflow section wins because it is the public
    contract of the high-level endpoints.
    """
    request = dict(_mapping(payload))

    ontology = _mapping(request.get("ontology"))
    _set_if_present(request, "ontology_content", ontology.get("content"))
    _set_if_present(
        request,
        "ontology_filename",
        ontology.get("filename") or ontology.get("name"),
    )

    rule = _mapping(request.get("rule"))
    _set_if_present(
        request,
        "business_rule",
        rule.get("text") if rule.get("text") is not None else rule.get("business_rule"),
    )
    _set_if_present(request, "rule_number", rule.get("number"))
    _set_if_present(request, "rule_title", rule.get("title"))

    batch = _mapping(request.get("batch"))
    _set_if_present(request, "batch_content", batch.get("content"))
    _set_if_present(
        request,
        "batch_filename",
        batch.get("filename") or batch.get("name"),
    )

    generation = _mapping(request.get("generation"))
    generation_fields = {
        "domain_context": "domain_context",
        "generation_guidance": "generation_guidance",
        "guidance": "generation_guidance",
        "prefixes": "prefixes",
        "base_namespace": "base_namespace",
        "shape_namespace": "shape_namespace",
        "shape_prefix": "shape_prefix",
        "llm_review": "llm_review",
        "review_max_attempts": "review_max_attempts",
    }
    for public_key, application_key in generation_fields.items():
        _set_if_present(request, application_key, generation.get(public_key))

    resolver = _mapping(request.get("resolver"))
    resolver_fields = {
        "semantic_threshold": "semantic_threshold",
        "semantic_target_margin": "semantic_target_margin",
        "semantic_max_targets": "semantic_max_targets",
        "top_k": "top_k",
        "llm_fallback": "resolver_llm_fallback",
        "wait_embeddings": "wait_embeddings",
        "embedding_timeout": "embedding_timeout",
        "embedding_poll_seconds": "embedding_poll_seconds",
        "strict_semantic": "strict_semantic",
    }
    for public_key, application_key in resolver_fields.items():
        _set_if_present(request, application_key, resolver.get(public_key))

    inference = _mapping(request.get("inference"))
    _set_if_present(request, "provider", inference.get("provider"))
    generation_model = inference.get("generation_model") or inference.get("model")
    _set_if_present(request, "llm_model", generation_model)
    _set_if_present(request, "model", generation_model)
    _set_if_present(request, "embedding_model", inference.get("embedding_model"))
    _set_if_present(request, "temperature", inference.get("temperature"))

    if inference:
        config = dict(_mapping(request.get("inference_config") or request.get("model_config")))
        _set_if_present(config, "provider", inference.get("provider"))
        _set_if_present(config, "temperature", inference.get("temperature"))
        for provider in ("databricks", "huggingface"):
            provider_config = _mapping(inference.get(provider))
            if provider_config:
                config[provider] = dict(provider_config)
        request["inference_config"] = config

    validation = _mapping(request.get("validation"))
    profiles = request.get("validation_profiles")
    if profiles is None:
        profiles = validation.get("profiles")
    _set_if_present(request, "validation_profiles", profiles)

    astrea = _mapping(request.get("astrea"))
    _set_if_present(request, "astrea_use_mode", astrea.get("mode"))
    _set_if_present(
        request,
        "astrea_merge_technique",
        astrea.get("merge_strategy") or astrea.get("merge_technique") or astrea.get("merge_mode"),
    )
    _set_if_present(request, "astrea_failure_policy", astrea.get("failure_policy"))
    _set_if_present(request, "astrea_baseline", astrea.get("baseline"))

    return request


def _normalized_choice(value: Any, choices: set[str], field: str, default: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in choices:
        options = ", ".join(sorted(choices))
        raise ValueError(f"{field} must be one of: {options}.")
    return normalized


def _normalized_alias_choice(
    value: Any,
    choices: set[str],
    aliases: Mapping[str, str],
    field: str,
    default: str,
) -> str:
    normalized = str(value or default).strip().lower()
    normalized = aliases.get(normalized, normalized)
    return _normalized_choice(normalized, choices, field, default)


def _baseline_payload(result: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "name": str(result.get("name") or "astrea.ttl"),
        "content": str(result.get("shape_document") or ""),
        "merge_content": str(result.get("merge_shape_document") or ""),
    }


def _prepare_astrea(
    request: Dict[str, Any],
    *,
    baseline_generator: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    requested_mode = _normalized_alias_choice(
        request.get("astrea_use_mode"),
        ASTREA_USE_MODES,
        ASTREA_USE_MODE_ALIASES,
        "astrea.mode",
        "none",
    )
    failure_policy = _normalized_choice(
        request.get("astrea_failure_policy"),
        ASTREA_FAILURE_POLICIES,
        "astrea.failure_policy",
        "continue",
    )
    internal_mode = {
        "evidence": "baseline",
        "evidence-and-merge": "both",
    }.get(requested_mode, requested_mode)
    request["astrea_use_mode"] = internal_mode
    status = {
        "requested_mode": requested_mode,
        "effective_mode": requested_mode,
        "failure_policy": failure_policy,
        "available": None,
        "evidence_safe": None,
        "merge_safe": None,
        "source": None,
        "name": None,
        "warnings": [],
        "message": "Astrea was not requested.",
    }
    if requested_mode == "none":
        return status

    supplied = _mapping(request.get("astrea_baseline"))
    if str(supplied.get("content") or "").strip():
        status.update({
            "available": True,
            "source": "request",
            "name": str(supplied.get("name") or supplied.get("filename") or "astrea.ttl"),
            "message": "Using the Astrea baseline supplied in the request.",
        })
        return status

    try:
        result = baseline_generator(request)
        baseline = _baseline_payload(result)
        if not baseline["content"].strip():
            raise AstreaResponseError("Astrea returned an empty SHACL document.")
        request["astrea_baseline"] = baseline
        status.update({
            "available": bool(result.get("available", True)),
            "evidence_safe": bool(result.get("evidence_safe", True)),
            "merge_safe": bool(result.get("merge_safe", True)),
            "source": str(result.get("source") or "astrea-api"),
            "name": baseline["name"],
            "shape_count": result.get("shape_count"),
            "validation": result.get("validation"),
            "merge_validation": result.get("merge_validation"),
            "normalization": result.get("normalization"),
            "warnings": list(result.get("warnings") or []),
            "message": str(result.get("message") or "Astrea baseline generated."),
        })
        return status
    except (AstreaUnavailableError, AstreaRateLimitError, AstreaResponseError) as exc:
        if failure_policy == "fail":
            raise
        request["astrea_use_mode"] = "none"
        status.update({
            "effective_mode": "none",
            "available": False,
            "error_type": (
                "astrea_rate_limited"
                if isinstance(exc, AstreaRateLimitError)
                else (
                "astrea_timeout"
                if isinstance(exc, AstreaTimeoutError)
                else (
                    "astrea_unavailable"
                    if isinstance(exc, AstreaUnavailableError)
                    else "astrea_response"
                )
                )
            ),
            "message": f"{exc} Continuing without Astrea.",
        })
        return status


def generate_batch_workflow(
    payload: Mapping[str, Any],
    *,
    event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    generator: Callable[..., Dict[str, Any]] = generate_batch_shapes,
    baseline_generator: Callable[[Dict[str, Any]], Dict[str, Any]] = generate_astrea_baseline,
) -> Dict[str, Any]:
    """Generate a batch whose rule shapes already include requested Astrea merges."""
    request = normalize_workflow_payload(payload)
    if not str(request.get("ontology_content") or "").strip():
        raise ValueError("ontology.content is required.")
    if not str(request.get("batch_content") or "").strip():
        raise ValueError("batch.content is required.")

    request.setdefault("ontology_filename", "ontology.ttl")
    request.setdefault("batch_filename", "business_rules.md")
    astrea_status = _prepare_astrea(request, baseline_generator=baseline_generator)
    generation = generator(request, event_callback=event_callback)

    final_shape_document = str(generation.get("shape_document") or "")

    return {
        "workflow": "batch-to-shapes",
        "summary": generation.get("summary") or {},
        "generation": generation,
        "astrea": astrea_status,
        "merge": None,
        "final_shape_document": final_shape_document,
    }


def _single_rule_batch(number: str, title: str, text: str) -> str:
    paragraphs = "".join(
        f"<p>{escape(paragraph.strip())}</p>"
        for paragraph in str(text or "").splitlines()
        if paragraph.strip()
    )
    if not paragraphs:
        paragraphs = "<p></p>"
    return (
        "<!doctype html><html><body>"
        '<section class="rule">'
        f'<p class="number">Number: {escape(number)}</p>'
        f'<p class="title">Title: {escape(title)}</p>'
        f'<div class="data-constraint">{paragraphs}</div>'
        "</section></body></html>"
    )


def generate_rule_workflow(
    payload: Mapping[str, Any],
    *,
    event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    generator: Callable[..., Dict[str, Any]] = generate_batch_shapes,
    baseline_generator: Callable[[Dict[str, Any]], Dict[str, Any]] = generate_astrea_baseline,
) -> Dict[str, Any]:
    """Resolve and generate one data constraint through the shared batch pipeline."""
    request = normalize_workflow_payload(payload)
    text = str(request.get("business_rule") or "").strip()
    if not text:
        raise ValueError("rule.text is required.")

    number = str(request.get("rule_number") or "RULE-001").strip()
    title = str(request.get("rule_title") or "Data constraint").strip()
    request["batch_content"] = _single_rule_batch(number, title, text)
    request["batch_filename"] = "single_rule.html"
    result = generate_batch_workflow(
        request,
        event_callback=event_callback,
        generator=generator,
        baseline_generator=baseline_generator,
    )

    generation = _mapping(result.get("generation"))
    rule_rows = generation.get("rules") or []
    shape_rows = generation.get("shapes") or []
    unresolved = generation.get("unresolved_rules") or []
    rule_row = dict(_mapping(rule_rows[0])) if rule_rows else {
        "rule_number": number,
        "title": title,
        "text": text,
        "resolution": None,
    }
    rule_row.pop("generated", None)
    return {
        "workflow": "rule-to-shape",
        "rule": rule_row,
        "shape": dict(_mapping(shape_rows[0])) if shape_rows else None,
        "unresolved": bool(unresolved),
        "unresolved_rules": unresolved,
        "summary": result.get("summary") or {},
        "namespaces": {
            "prefixes": generation.get("prefixes", ""),
            "base_namespace": generation.get("base_namespace", ""),
            "shape_namespace": generation.get("shape_namespace", ""),
            "shape_prefix": generation.get("shape_prefix", ""),
        },
        "astrea": result.get("astrea"),
        "merge": result.get("merge"),
        "final_shape_document": result.get("final_shape_document", ""),
    }
