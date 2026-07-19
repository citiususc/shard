"""
model_loader.py

Unified model loader that routes to the appropriate backend:

  Text/Chat   → HuggingFace if model_id contains '/'  (org/model-name)
                Databricks  if model_id contains no '/' (endpoint name)

  Embeddings  → HuggingFace by default (Qwen/Qwen3-Embedding-0.6B)
                Databricks  if an endpoint name is passed (no '/')

  Vision      → Databricks by default (system.ai.gemma-3-12b)
                HuggingFace if a HuggingFace model ID is passed (contains '/')

Public API matches both backends exactly — all call sites remain unchanged.
"""

from __future__ import annotations

from typing import Any

from model_loader_databricks import (
    DEFAULT_TEMPERATURE,
    DEFAULT_EVAL_MAX_NEW_TOKENS,
    DEFAULT_GEN_MAX_NEW_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_TEXT_MODEL_ID,
    DEFAULT_VISION_MODEL_ID,
    IMG_MAX_NEW_TOKENS,
    TEXT_MAX_NEW_TOKENS,
)

# Keep the historical unified-loader default without importing the optional
# Hugging Face backend (and therefore torch) until it is actually selected.
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"

from Logger import logger
from deployment_policy import ensure_provider_enabled
from runtime_config import get_inference_config


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
    import model_loader_hf as _mod
    return _mod


def _db():
    import model_loader_databricks as _mod
    return _mod


# ---------------------------------------------------------------------------
# Text / Chat — routed by model_id format
# ---------------------------------------------------------------------------

def get_text_model(
    text_model_id: str = DEFAULT_TEXT_MODEL_ID,
    temperature: float = DEFAULT_TEMPERATURE,
):
    """
    Return a LangChain Runnable for text summarisation (used by rag.py).
    Routes to HuggingFace if model_id contains '/', Databricks otherwise.
    """
    if _use_hf_backend(text_model_id):
        logger.debug(
            f"[model_loader] get_text_model: '{text_model_id}' → HuggingFace."
        )
        return _hf().get_text_model(text_model_id, temperature)

    logger.debug(
        f"[model_loader] get_text_model: '{text_model_id}' → Databricks."
    )
    return _db().get_text_model(text_model_id, temperature)


def get_chat_llm(
    llm_model_id: str   = DEFAULT_TEXT_MODEL_ID,
    kind: str           = "generator",
    temperature: float  = DEFAULT_TEMPERATURE,
    max_new_tokens: int = DEFAULT_GEN_MAX_NEW_TOKENS,
):
    """
    Return a LangChain Runnable for evaluator / generator (used by multiagent.py).
    Routes to HuggingFace if model_id contains '/', Databricks otherwise.
    """
    if _use_hf_backend(llm_model_id):
        logger.debug(
            f"[model_loader] get_chat_llm: '{llm_model_id}' (kind={kind}) → HuggingFace."
        )
        return _hf().get_chat_llm(llm_model_id, kind, temperature, max_new_tokens)

    logger.debug(
        f"[model_loader] get_chat_llm: '{llm_model_id}' (kind={kind}) → Databricks."
    )
    return _db().get_chat_llm(llm_model_id, kind, temperature, max_new_tokens)


# ---------------------------------------------------------------------------
# Embeddings — HuggingFace by default, Databricks if endpoint name
# ---------------------------------------------------------------------------

def get_embedding_function(
    embedding_model_id: str = "Qwen/Qwen3-Embedding-0.6B",
):
    """
    Return a Chroma-compatible embeddings object.

    Default: HuggingFace (Qwen/Qwen3-Embedding-0.6B).
    Pass a Databricks endpoint name (no '/') to use Databricks instead.
    """
    if _use_hf_backend(embedding_model_id):
        logger.debug(
            f"[model_loader] get_embedding_function: '{embedding_model_id}' → HuggingFace."
        )
        return _hf().get_embedding_function(embedding_model_id)

    logger.debug(
        f"[model_loader] get_embedding_function: '{embedding_model_id}' → Databricks."
    )
    return _db().get_embedding_function(embedding_model_id)


# ---------------------------------------------------------------------------
# Vision — Databricks by default, HuggingFace if HF model ID passed
# ---------------------------------------------------------------------------

def get_vision_backend(
    vision_model_id: str = "system.ai.gemma-3-12b",
):
    """
    Return a vision backend dict compatible with rag.py.

    Default: Databricks (system.ai.gemma-3-12b).
    Pass a HuggingFace model ID (contains '/') to use HuggingFace instead.
    """
    if _use_hf_backend(vision_model_id):
        logger.debug(
            f"[model_loader] get_vision_backend: '{vision_model_id}' → HuggingFace."
        )
        return _hf().get_vision_backend(vision_model_id)

    logger.debug(
        f"[model_loader] get_vision_backend: '{vision_model_id}' → Databricks."
    )
    return _db().get_vision_backend(vision_model_id)


def internvl_preprocess(img: Any, input_size: int = 448) -> Any:
    """
    API-compatibility shim for rag.py.
    Delegates to whichever vision backend has a registered image.
    Since vision routing happens in get_vision_backend(), we delegate
    to the HF backend if any HF vision model is cached, Databricks otherwise.
    """
    try:
        import model_loader_hf as hf_mod
        if hf_mod._VISION_CACHE:
            logger.debug(
                "[model_loader] internvl_preprocess → HuggingFace (active vision cache)."
            )
            return hf_mod.internvl_preprocess(img, input_size)
    except Exception:
        pass

    logger.debug(
        "[model_loader] internvl_preprocess → Databricks."
    )
    return _db().internvl_preprocess(img, input_size)
