"""Provider-neutral chat and embedding inference facade."""

from __future__ import annotations

from .databricks import (
    DEFAULT_TEMPERATURE,
    DEFAULT_EVAL_MAX_NEW_TOKENS,
    DEFAULT_GEN_MAX_NEW_TOKENS,
    DEFAULT_LLM_MODEL_ID,
)

# Keep the historical unified-loader default without importing the optional
# Hugging Face backend (and therefore torch) until it is actually selected.
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"

from shard.deployment.policy import ensure_provider_enabled
from shard.observability import logger
from .context import get_inference_config


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def _is_hf_model_id(model_id: str) -> bool:
    """
    HuggingFace model IDs always contain '/' separating org and model name.
    Databricks endpoint names are short strings without '/'.

    Examples:
        "meta-llama/Llama-3.3-70B-Instruct"  → True  (HuggingFace)
        "openai/gpt-oss-120b"                 → True  (HuggingFace)
        "system.ai.gemma-3-12b" → False (Databricks)
        "gemma-3-12b"           → False (Databricks)
    """
    return "/" in model_id


def _use_hf_backend(model_id: str) -> bool:
    """Respect the UI provider when supplied; otherwise infer from model id."""
    provider = (get_inference_config().get("provider") or "").lower()
    if provider == "huggingface":
        ensure_provider_enabled("huggingface")
        return True
    if provider == "databricks":
        return False
    use_huggingface = _is_hf_model_id(model_id)
    if use_huggingface:
        ensure_provider_enabled("huggingface")
    return use_huggingface


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _hf():
    from . import huggingface as _mod
    return _mod


def _db():
    from . import databricks as _mod
    return _mod


# ---------------------------------------------------------------------------
# Chat — routed by model_id format
# ---------------------------------------------------------------------------


def get_chat_llm(
    llm_model_id: str   = DEFAULT_LLM_MODEL_ID,
    kind: str           = "generator",
    temperature: float  = DEFAULT_TEMPERATURE,
    max_new_tokens: int = DEFAULT_GEN_MAX_NEW_TOKENS,
):
    """
    Return a LangChain Runnable for generation or resolution.
    Routes to HuggingFace if model_id contains '/', Databricks otherwise.
    """
    if _use_hf_backend(llm_model_id):
        logger.debug(
            f"[inference] get_chat_llm: '{llm_model_id}' (kind={kind}) -> Hugging Face."
        )
        return _hf().get_chat_llm(llm_model_id, kind, temperature, max_new_tokens)

    logger.debug(
        f"[inference] get_chat_llm: '{llm_model_id}' (kind={kind}) -> Databricks."
    )
    return _db().get_chat_llm(llm_model_id, kind, temperature, max_new_tokens)


# ---------------------------------------------------------------------------
# Embeddings — HuggingFace by default, Databricks if endpoint name
# ---------------------------------------------------------------------------

def get_embedding_function(
    embedding_model_id: str = "Qwen/Qwen3-Embedding-0.6B",
):
    """
    Return a LangChain-compatible embeddings object.

    Default: HuggingFace (Qwen/Qwen3-Embedding-0.6B).
    Pass a Databricks endpoint name (no '/') to use Databricks instead.
    """
    if _use_hf_backend(embedding_model_id):
        logger.debug(
            f"[inference] get_embedding_function: '{embedding_model_id}' -> Hugging Face."
        )
        return _hf().get_embedding_function(embedding_model_id)

    logger.debug(
        f"[inference] get_embedding_function: '{embedding_model_id}' -> Databricks."
    )
    return _db().get_embedding_function(embedding_model_id)
