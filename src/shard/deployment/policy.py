"""Deployment capabilities and inference-provider policy.

The local profile exposes every inference backend. The public profile keeps
remote inference available while preventing this server from loading and
running Hugging Face models locally.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from shard import __description__, __title__, __version__


DEPLOYMENT_PROFILE_ENV = "SHARD_DEPLOYMENT_PROFILE"
LEGACY_DEPLOYMENT_PROFILE_ENV = "BR2SHACL_DEPLOYMENT_PROFILE"
LOCAL_PROFILE = "local"
PUBLIC_PROFILE = "public"
PROJECT_REPOSITORY_URL = "https://github.com/citiususc/br2shacl-ui"
HUGGINGFACE_PUBLIC_MESSAGE = (
    "Local inference is not available in this hosted version. "
    "Clone and run the project locally to use models on your own hardware."
)


@dataclass(frozen=True)
class ProviderPolicy:
    """Execution policy for one inference provider."""

    enabled: bool
    execution: str
    message: str = ""


@dataclass(frozen=True)
class DeploymentPolicy:
    """Non-secret capabilities attached to one deployment profile."""

    name: str
    audience: str
    description: str
    providers: Mapping[str, ProviderPolicy]


DEPLOYMENT_POLICIES = {
    LOCAL_PROFILE: DeploymentPolicy(
        name=LOCAL_PROFILE,
        audience="developer",
        description=(
            "Local development profile with remote Databricks and local Hugging Face "
            "inference available."
        ),
        providers={
            "databricks": ProviderPolicy(enabled=True, execution="remote"),
            "huggingface": ProviderPolicy(enabled=True, execution="local"),
        },
    ),
    PUBLIC_PROFILE: DeploymentPolicy(
        name=PUBLIC_PROFILE,
        audience="hosted-demo",
        description=(
            "Hosted demo profile with remote inference only; server-side local model "
            "execution is disabled."
        ),
        providers={
            "databricks": ProviderPolicy(enabled=True, execution="remote"),
            "huggingface": ProviderPolicy(
                enabled=False,
                execution="local",
                message=HUGGINGFACE_PUBLIC_MESSAGE,
            ),
        },
    ),
}
SUPPORTED_PROFILES = tuple(DEPLOYMENT_POLICIES)


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
    """Return the deployment profile, accepting the former environment alias."""
    value = os.environ.get(DEPLOYMENT_PROFILE_ENV)
    if value is None:
        value = os.environ.get(LEGACY_DEPLOYMENT_PROFILE_ENV, LOCAL_PROFILE)
    return normalize_deployment_profile(value)


def get_deployment_policy(profile: str | None = None) -> DeploymentPolicy:
    """Return the declarative policy for a normalized deployment profile."""
    return DEPLOYMENT_POLICIES[normalize_deployment_profile(profile or get_deployment_profile())]


def provider_enabled(provider: str, profile: str | None = None) -> bool:
    """Return whether a provider may execute in the selected profile."""
    policy = get_deployment_policy(profile)
    normalized_provider = str(provider or "").strip().lower()
    provider_policy = policy.providers.get(normalized_provider)
    return provider_policy.enabled if provider_policy else True


def capabilities(profile: str | None = None) -> Dict[str, Any]:
    """Return the non-secret capability document consumed by the web UI."""
    policy = get_deployment_policy(profile)
    return {
        "application": {
            "name": __title__,
            "title": __description__,
            "version": __version__,
        },
        "deployment_profile": policy.name,
        "deployment": {
            "audience": policy.audience,
            "description": policy.description,
        },
        "repository_url": PROJECT_REPOSITORY_URL,
        "providers": {
            provider: {
                "enabled": provider_policy.enabled,
                "execution": provider_policy.execution,
                **({"message": provider_policy.message}
                   if provider_policy.message or provider == "huggingface" else {}),
            }
            for provider, provider_policy in policy.providers.items()
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
    workflow_inference = _mapping(request.get("inference"))
    explicit = str(
        workflow_inference.get("provider")
        or config.get("provider")
        or request.get("provider")
        or ""
    ).strip().lower()
    if explicit in {"databricks", "huggingface"}:
        return explicit

    model_keys = ("model", "generation_model", "llm_model", "embedding_model")
    for source in (workflow_inference, request, config):
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
