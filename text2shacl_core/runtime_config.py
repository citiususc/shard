"""
Per-request inference configuration for the demo services.

The UI sends provider credentials and generation options with each request.
Context variables let the existing model_loader modules read those values
without falling back to process-wide os.environ mutations, which would be
unsafe with ThreadingHTTPServer handling multiple requests at once.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from typing import Any, Dict, Iterator, Mapping


_INFERENCE_CONFIG: ContextVar[Dict[str, Any]] = ContextVar(
    "text2shacl_inference_config",
    default={},
)


def _clean_mapping(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): v for k, v in value.items() if v is not None}


def normalize_inference_config(config: Any) -> Dict[str, Any]:
    """Return the subset of a UI config that model loaders understand."""
    cfg = _clean_mapping(config)
    provider = str(cfg.get("provider") or "").strip().lower()
    databricks = _clean_mapping(cfg.get("databricks"))
    huggingface = _clean_mapping(cfg.get("huggingface"))

    # Accept a few flat aliases so service payloads can remain flexible.
    if cfg.get("databricks_token") and not databricks.get("token"):
        databricks["token"] = cfg.get("databricks_token")
    if cfg.get("databricks_base_url") and not databricks.get("base_url"):
        databricks["base_url"] = cfg.get("databricks_base_url")
    if cfg.get("base_url") and not databricks.get("base_url"):
        databricks["base_url"] = cfg.get("base_url")
    if cfg.get("api_key") and provider == "databricks" and not databricks.get("token"):
        databricks["token"] = cfg.get("api_key")
    if cfg.get("hf_token") and not huggingface.get("token"):
        huggingface["token"] = cfg.get("hf_token")
    if cfg.get("api_key") and provider == "huggingface" and not huggingface.get("token"):
        huggingface["token"] = cfg.get("api_key")

    if databricks.get("base_url"):
        databricks["base_url"] = str(databricks["base_url"]).rstrip("/")
    if databricks.get("token"):
        databricks["token"] = str(databricks["token"]).strip()
    if huggingface.get("token"):
        huggingface["token"] = str(huggingface["token"]).strip()

    return {
        "provider": provider,
        "databricks": databricks,
        "huggingface": huggingface,
    }


def set_inference_config(config: Any):
    """Set config for the current context; returns a token for reset."""
    return _INFERENCE_CONFIG.set(normalize_inference_config(config))


def reset_inference_config(token) -> None:
    _INFERENCE_CONFIG.reset(token)


@contextmanager
def inference_config(config: Any) -> Iterator[None]:
    """Temporarily expose a UI-supplied inference config to model loaders."""
    token = set_inference_config(config)
    try:
        yield
    finally:
        reset_inference_config(token)


def get_inference_config() -> Dict[str, Any]:
    return _INFERENCE_CONFIG.get() or {}


def get_databricks_token() -> str:
    cfg = get_inference_config()
    databricks = _clean_mapping(cfg.get("databricks"))
    return str(databricks.get("token") or os.environ.get("DATABRICKS_TOKEN", "")).strip()


def get_databricks_base_url() -> str:
    cfg = get_inference_config()
    databricks = _clean_mapping(cfg.get("databricks"))
    return str(databricks.get("base_url") or os.environ.get("DATABRICKS_BASE_URL", "")).rstrip("/")


def get_hf_token() -> str:
    cfg = get_inference_config()
    huggingface = _clean_mapping(cfg.get("huggingface"))
    return str(huggingface.get("token") or os.environ.get("HF_TOKEN", "")).strip()


def config_fingerprint(config: Any = None) -> str:
    """Stable, non-secret cache key material for credentials + endpoint."""
    if config is None:
        cfg = get_inference_config()
    else:
        cfg = normalize_inference_config(config)

    db_token = get_databricks_token() if config is None else str(
        _clean_mapping(cfg.get("databricks")).get("token") or os.environ.get("DATABRICKS_TOKEN", "")
    ).strip()
    hf_token = get_hf_token() if config is None else str(
        _clean_mapping(cfg.get("huggingface")).get("token") or os.environ.get("HF_TOKEN", "")
    ).strip()
    db_base_url = get_databricks_base_url() if config is None else str(
        _clean_mapping(cfg.get("databricks")).get("base_url") or os.environ.get("DATABRICKS_BASE_URL", "")
    ).rstrip("/")

    safe = {
        "provider": cfg.get("provider", ""),
        "databricks_base_url": db_base_url,
        "databricks_token_sha256": hashlib.sha256(db_token.encode("utf-8")).hexdigest() if db_token else "",
        "hf_token_sha256": hashlib.sha256(hf_token.encode("utf-8")).hexdigest() if hf_token else "",
    }
    raw = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
