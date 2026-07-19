"""Deployment capabilities and inference-provider policy.

The local profile exposes every inference backend. The public profile keeps
remote inference available while preventing this server from loading and
running Hugging Face models locally.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping


DEPLOYMENT_PROFILE_ENV = "BR2SHACL_DEPLOYMENT_PROFILE"
LOCAL_PROFILE = "local"
PUBLIC_PROFILE = "public"
SUPPORTED_PROFILES = (LOCAL_PROFILE, PUBLIC_PROFILE)
PROJECT_REPOSITORY_URL = "https://github.com/citiususc/br2shacl-ui"
HUGGINGFACE_PUBLIC_MESSAGE = (
    "Local Hugging Face inference is not available in this hosted version. "
    "Clone and run the project locally to use models on your own hardware."
)


class ProviderDisabledError(RuntimeError):
    """Raised when a request selects a provider disabled by deployment policy."""

    code = "provider_disabled"
    status = 403

    def __init__(self, provider: str, message: str = ""):
        self.provider = str(provider or "").strip().lower()
        super().__init__(message or f"Inference provider '{self.provider}' is disabled.")

    def as_payload(self) -> Dict[str, Any]:
        """Return a stable API error body without exposing configuration secrets."""
        return {
            "error": self.code,
            "code": self.code,
            "provider": self.provider,
            "message": str(self),
            "repository_url": PROJECT_REPOSITORY_URL,
        }


def normalize_deployment_profile(value: Any) -> str:
    """Normalize and validate a deployment profile name."""
    profile = str(value or LOCAL_PROFILE).strip().lower()
    if profile not in SUPPORTED_PROFILES:
        choices = ", ".join(SUPPORTED_PROFILES)
        raise ValueError(f"deployment profile must be one of: {choices}")
    return profile


def get_deployment_profile() -> str:
    """Return the process deployment profile, defaulting to local."""
    return normalize_deployment_profile(os.environ.get(DEPLOYMENT_PROFILE_ENV, LOCAL_PROFILE))


def provider_enabled(provider: str, profile: str | None = None) -> bool:
    """Return whether a provider may execute in the selected profile."""
    selected_profile = normalize_deployment_profile(profile or get_deployment_profile())
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "huggingface":
        return selected_profile == LOCAL_PROFILE
    return True


def capabilities(profile: str | None = None) -> Dict[str, Any]:
    """Return the non-secret capability document consumed by the web UI."""
    selected_profile = normalize_deployment_profile(profile or get_deployment_profile())
    huggingface_enabled = provider_enabled("huggingface", selected_profile)
    return {
        "deployment_profile": selected_profile,
        "repository_url": PROJECT_REPOSITORY_URL,
        "providers": {
            "databricks": {
                "enabled": True,
                "execution": "remote",
            },
            "huggingface": {
                "enabled": huggingface_enabled,
                "execution": "local",
                "message": "" if huggingface_enabled else HUGGINGFACE_PUBLIC_MESSAGE,
            },
        },
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def requested_provider(payload: Any) -> str:
    """Resolve the effective provider from a service request.

    Explicit provider selection wins. For backward-compatible requests without
    one, repository-style model ids are recognized as local Hugging Face models.
    """
    request = _mapping(payload)
    config = _mapping(request.get("inference_config") or request.get("model_config"))
    explicit = str(config.get("provider") or request.get("provider") or "").strip().lower()
    if explicit in {"databricks", "huggingface"}:
        return explicit

    model_keys = ("model", "llm_model", "text_model", "vision_model", "embedding_model")
    for source in (request, config):
        for key in model_keys:
            if "/" in str(source.get(key) or ""):
                return "huggingface"
    return explicit


def ensure_provider_enabled(provider: str, profile: str | None = None) -> None:
    """Raise a policy error if the provider cannot execute in this deployment."""
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider and not provider_enabled(normalized_provider, profile):
        message = HUGGINGFACE_PUBLIC_MESSAGE if normalized_provider == "huggingface" else ""
        raise ProviderDisabledError(normalized_provider, message)


def ensure_request_provider_enabled(payload: Any, profile: str | None = None) -> None:
    """Apply provider policy to a service request before inference starts."""
    ensure_provider_enabled(requested_provider(payload), profile)
