"""Typed, secret-free request provenance for the versioned SHARD API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from .contract import API_VERSION, EndpointSpec
from shard.deployment.policy import get_deployment_profile, requested_provider


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reference(value: Any) -> Optional[Dict[str, str]]:
    if isinstance(value, str):
        return {"iri": value} if value.strip() else None
    item = _mapping(value)
    iri = str(item.get("iri") or item.get("target") or item.get("full_iri") or "").strip()
    if not iri:
        return None
    label = str(item.get("label") or "").strip()
    return {"iri": iri, **({"label": label} if label else {})}


def _references(values: Any) -> List[Dict[str, str]]:
    if not isinstance(values, list):
        return []
    return [reference for value in values if (reference := _reference(value))]


def _rule(payload: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    nested = _mapping(payload.get("rule"))
    text = nested.get("text") or payload.get("business_rule")
    if not str(text or "").strip():
        return None
    return {
        "number": str(nested.get("number") or payload.get("rule_number") or "RULE-001"),
        "title": str(nested.get("title") or payload.get("rule_title") or "Data constraint"),
        "text": str(text),
    }


def _target_roles(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    roles = _mapping(payload.get("target_roles"))
    if not roles:
        return None
    return {
        name: _references(roles.get(name) or [])
        for name in ("focus_nodes", "constraint_paths", "related_terms")
    }


def _selected_targets(payload: Mapping[str, Any]) -> List[Dict[str, str]]:
    roles = _target_roles(payload) or {}
    result: List[Dict[str, str]] = []
    seen = set()
    target = _reference(payload.get("target"))
    values = ([target] if target else []) + [
        item
        for name in ("focus_nodes", "constraint_paths", "related_terms")
        for item in roles.get(name, [])
    ]
    for item in values:
        if item and item["iri"] not in seen:
            seen.add(item["iri"])
            result.append(item)
    return result


def _profile_names(payload: Mapping[str, Any]) -> List[str]:
    validation = _mapping(payload.get("validation"))
    profiles = payload.get("validation_profiles")
    if profiles is None:
        profiles = validation.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
    return ["shacl-shacl.ttl", *[
        str(_mapping(profile).get("name") or f"profile-{index + 1}")
        for index, profile in enumerate(profiles)
    ]]


def _canonical_astrea_mode(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return {
        "baseline": "evidence",
        "both": "evidence-and-merge",
    }.get(normalized, normalized or None)


def _canonical_merge_strategy(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return "generated-priority" if normalized == "priority-llm" else normalized or None


AUTHORING_OPERATIONS = {
    "rules.resolve-targets",
    "shapes.build",
    "shapes.validate",
    "shapes.prepare-export",
    "baselines.astrea.generate",
    "shapes.merge",
    "workflows.rule.generate",
    "workflows.batch.generate",
    "batches.generate",
}


def is_authoring_operation(operation: str) -> bool:
    return operation in AUTHORING_OPERATIONS


def request_operation_metadata(
    endpoint: EndpointSpec,
    request_id: str,
) -> Dict[str, Any]:
    """Build secret-free metadata shared by every canonical API response."""
    return {
        "request_id": request_id,
        "operation": endpoint.operation,
        "service": endpoint.service_id or "platform",
        "api_version": API_VERSION,
        "deployment_profile": get_deployment_profile(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 0.0,
        "warnings": [],
    }


def request_authoring_provenance(
    endpoint: EndpointSpec,
    payload: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Build authoring provenance without credentials or transport metadata."""
    request = _mapping(payload)
    inference = _mapping(request.get("inference"))
    generation = _mapping(request.get("generation"))
    astrea = _mapping(request.get("astrea"))
    baseline = _mapping(request.get("astrea_baseline") or astrea.get("baseline"))
    source_rule = _rule(request)
    target_roles = _target_roles(request)
    generation_model = (
        inference.get("generation_model")
        or request.get("generation_model")
        or request.get("llm_model")
        or request.get("model")
    )
    embedding_model = inference.get("embedding_model") or request.get("embedding_model")
    baseline_usage = _canonical_astrea_mode(
        astrea.get("mode") or request.get("astrea_use_mode")
    )
    merge_strategy = _canonical_merge_strategy(
        astrea.get("merge_strategy")
        or astrea.get("merge_technique")
        or request.get("merge_strategy")
        or request.get("astrea_merge_technique")
        or request.get("technique")
    )
    generation_parameters = {}
    temperature = inference.get("temperature", request.get("temperature"))
    if temperature is not None:
        generation_parameters["temperature"] = temperature
    max_new_tokens = inference.get("max_new_tokens", request.get("max_new_tokens"))
    if max_new_tokens is not None:
        generation_parameters["max_new_tokens"] = max_new_tokens
    if generation.get("shape_prefix") or request.get("shape_prefix"):
        generation_parameters["shape_prefix"] = (
            generation.get("shape_prefix") or request.get("shape_prefix")
        )
    llm_review = generation.get("llm_review", request.get("llm_review"))
    if llm_review is not None:
        generation_parameters["llm_review"] = bool(llm_review)
    review_max_attempts = generation.get(
        "review_max_attempts", request.get("review_max_attempts")
    )
    if review_max_attempts is not None:
        generation_parameters["review_max_attempts"] = int(review_max_attempts)

    return {
        **({"source_rule": source_rule} if source_rule else {}),
        "selected_targets": _selected_targets(request),
        **({"target_roles": target_roles} if target_roles else {}),
        **({"generation_model": str(generation_model)} if generation_model else {}),
        **({"embedding_model": str(embedding_model)} if embedding_model else {}),
        **({"inference_provider": requested_provider(request)} if requested_provider(request) else {}),
        "generation_parameters": generation_parameters,
        "validation_profiles": _profile_names(request),
        "validation_results": [],
        **({"baseline_usage": baseline_usage} if baseline_usage else {}),
        **({"baseline_source": str(baseline.get("name"))} if baseline.get("name") else {}),
        **({"merge_strategy": merge_strategy} if merge_strategy else {}),
        "evidence": [],
        "warnings": [],
        "errors": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def request_provenance(
    endpoint: EndpointSpec,
    payload: Mapping[str, Any] | None,
    request_id: str,
) -> Dict[str, Any]:
    """Compatibility wrapper returning authoring provenance only."""
    del request_id
    return request_authoring_provenance(endpoint, payload)
