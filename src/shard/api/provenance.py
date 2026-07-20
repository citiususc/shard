"""Safe request-level provenance for the versioned SHARD API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from .contract import API_VERSION, EndpointSpec
from shard.deployment.policy import get_deployment_profile, requested_provider


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _non_empty(values: Mapping[str, Any]) -> Dict[str, str]:
    return {
        key: str(value)
        for key, value in values.items()
        if value is not None and str(value).strip()
    }


def _model_selection(payload: Mapping[str, Any]) -> Dict[str, str]:
    inference = _mapping(payload.get("inference"))
    return _non_empty({
        "generation": (
            inference.get("generation_model")
            or inference.get("model")
            or payload.get("llm_model")
            or payload.get("model")
        ),
        "embedding": inference.get("embedding_model") or payload.get("embedding_model"),
    })


def _input_selection(payload: Mapping[str, Any]) -> Dict[str, Any]:
    target = _mapping(payload.get("target"))
    ontology = _mapping(payload.get("ontology"))
    guide = _mapping(payload.get("guide"))
    rule = _mapping(payload.get("rule"))
    validation = _mapping(payload.get("validation"))
    validation_profiles = payload.get("validation_profiles")
    if validation_profiles is None:
        validation_profiles = validation.get("profiles")
    profile_names = []
    if isinstance(validation_profiles, list):
        profile_names = [
            str(_mapping(profile).get("name") or f"profile-{index + 1}")
            for index, profile in enumerate(validation_profiles)
        ]
    astrea = _mapping(payload.get("astrea"))
    baseline = _mapping(payload.get("astrea_baseline") or astrea.get("baseline"))
    guide_filename = payload.get("guide_filename") or guide.get("filename") or guide.get("name")
    ontology_filename = (
        payload.get("ontology_filename")
        or ontology.get("filename")
        or ontology.get("name")
    )
    astrea_use = payload.get("astrea_use_mode") or astrea.get("mode")
    merge_mode = (
        payload.get("merge_mode")
        or payload.get("technique")
        or astrea.get("merge_technique")
        or astrea.get("merge_mode")
    )
    return {
        **({"target": _non_empty({
            "iri": target.get("iri") or target.get("full_iri"),
            "type": target.get("type"),
        })} if target else {}),
        **({"rule_number": str(rule.get("number"))} if rule.get("number") else {}),
        **({"guide_filename": str(guide_filename)} if guide_filename else {}),
        **({"ontology_filename": str(ontology_filename)} if ontology_filename else {}),
        **({"validation_profiles": profile_names} if profile_names else {}),
        **({"baseline": str(baseline.get("name"))} if baseline.get("name") else {}),
        **({"astrea_use": str(astrea_use)} if astrea_use else {}),
        **({"merge_mode": str(merge_mode)} if merge_mode else {}),
    }


def request_provenance(
    endpoint: EndpointSpec,
    payload: Mapping[str, Any] | None,
    request_id: str,
) -> Dict[str, Any]:
    """Build non-secret provenance for one canonical API request."""
    request_payload = _mapping(payload)
    models = _model_selection(request_payload)
    inputs = _input_selection(request_payload)
    provider = requested_provider(request_payload)
    inference = {
        **({"provider": provider} if provider else {}),
        **({"models": models} if models else {}),
    }
    return {
        "request_id": request_id,
        "api_version": API_VERSION,
        "operation": endpoint.operation,
        "service": endpoint.service_id or "platform",
        "route": endpoint.path,
        "deployment_profile": get_deployment_profile(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        **({"inference": inference} if inference else {}),
        **({"inputs": inputs} if inputs else {}),
    }
