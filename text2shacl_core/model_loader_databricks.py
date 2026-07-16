"""
model_loader_databricks.py

Loads all three model types used in text2shacl via Databricks AI Gateway.
Uses httpx directly for text/chat calls to avoid langchain-openai renaming
max_tokens → max_completion_tokens, which Databricks does not support.

Drop-in replacement for model_loader.py — same public API, remote inference.

MODEL MAPPING
-------------
Role            Local model                         Databricks endpoint
────────────────────────────────────────────────────────────────────────
Text / Chat     meta-llama/Llama-3.3-70B-Instruct  system.ai.gemma-3-12b
Vision          OpenGVLab/InternVL3_5-38B          system.ai.gemma-3-12b
Embeddings      BAAI/bge-large-en-v1.5             system.ai.qwen3-embedding-0-6b

CONFIGURATION
-------------
The demo UI sends Databricks token and AI Gateway base URL per request.

USAGE
-----
    from model_loader_databricks import (
        get_chat_llm,
        get_text_model,
        get_embedding_function,
        get_vision_backend,
        internvl_preprocess,
        DEFAULT_LLM_MODEL_ID,
        DEFAULT_TEXT_MODEL_ID,
        DEFAULT_VISION_MODEL_ID,
        DEFAULT_EMBEDDING_MODEL_ID,
        DEFAULT_EVAL_MAX_NEW_TOKENS,
        DEFAULT_GEN_MAX_NEW_TOKENS,
        IMG_MAX_NEW_TOKENS,
    )
"""

from __future__ import annotations

import base64
import os
import time
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image
from openai import OpenAI

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from Logger import logger
from runtime_config import get_databricks_base_url, get_databricks_token

# ---------------------------------------------------------------------------
# Default endpoint names
# ---------------------------------------------------------------------------
DEFAULT_LLM_MODEL_ID = "system.ai.gemma-3-12b"
DEFAULT_TEXT_MODEL_ID = "system.ai.gemma-3-12b"
DEFAULT_VISION_MODEL_ID = "system.ai.gemma-3-12b"
DEFAULT_EMBEDDING_MODEL_ID = "system.ai.qwen3-embedding-0-6b"
DEFAULT_TEMPERATURE = 0.50


def normalize_model_id(model_id: str) -> str:
    """Return the AI Gateway model id expected by Databricks.

    The UI intentionally shows short names such as ``gemma-3-12b``. The
    Databricks OpenAI-compatible AI Gateway expects ``system.ai.gemma-3-12b``.
    Legacy ``databricks-*`` names are also accepted and converted.
    """
    clean = str(model_id or "").strip()
    if not clean or "/" in clean:
        return clean
    if clean.startswith("system.ai."):
        return clean
    if clean.startswith("databricks-") and clean != "databricks-genie":
        clean = clean[len("databricks-"):]
    return f"system.ai.{clean}"

# Token budgets
TEXT_MAX_NEW_TOKENS = int(os.environ.get("RAG_TEXT_MAX_NEW_TOKENS", "800"))
IMG_MAX_NEW_TOKENS = int(os.environ.get("RAG_IMG_MAX_NEW_TOKENS", "2000"))
DEFAULT_EVAL_MAX_NEW_TOKENS = 700
DEFAULT_GEN_MAX_NEW_TOKENS = 3000

# Module-level caches
_CHAT_LLM_CACHE: Dict[Tuple, Any] = {}
_TEXT_MODEL_CACHE: Dict[Tuple, Any] = {}


# ---------------------------------------------------------------------------
# Credentials helpers
# ---------------------------------------------------------------------------

def _get_credentials() -> Tuple[str, str]:
    token = get_databricks_token()
    base_url = get_databricks_base_url()

    if not token or not base_url:
        logger.error("Databricks credentials are not configured.")
        raise EnvironmentError(
            "Databricks credentials not set.\n"
            "Configure Databricks token and base URL in the UI model settings."
        )

    logger.debug(f"Using Databricks base URL: {base_url}")
    return token, base_url


# OpenAI client -> used only for embeddings (no field renaming issues there).
# Cache per credential/base URL pair so different browser sessions can coexist.
_OAI_CLIENTS: Dict[Tuple[str, str], OpenAI] = {}


def _oai_client() -> OpenAI:
    token, base_url = _get_credentials()
    key = (base_url, token)
    if key not in _OAI_CLIENTS:
        logger.debug("Initializing OpenAI-compatible client for Databricks embeddings.")
        _OAI_CLIENTS[key] = OpenAI(api_key=token, base_url=base_url)
    return _OAI_CLIENTS[key]

# ---------------------------------------------------------------------------
# Message parsing helpers
# ---------------------------------------------------------------------------
def _extract_text_from_chat_content(content: Any) -> str:
        """
        Normalize Databricks/OpenAI chat message content to plain text.

        Some models return:
        - a plain string
        Others may return:
        - a list of structured content parts
        """
        if content is None:
            return ""

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: List[str] = []

            for item in content:
                if isinstance(item, str):
                    if item.strip():
                        parts.append(item.strip())
                    continue

                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                        continue

                    # Defensive fallback for typed blocks
                    if item.get("type") in {"output_text", "text"}:
                        nested_text = item.get("text", "")
                        if isinstance(nested_text, str) and nested_text.strip():
                            parts.append(nested_text.strip())

            return "\n".join(parts).strip()

        return str(content).strip()



# ---------------------------------------------------------------------------
# Chat Runnable — calls Databricks via httpx, controlling payload directly
# ---------------------------------------------------------------------------

class _DatabricksChatRunnable(Runnable):
    """
    LangChain Runnable that calls Databricks AI Gateway via httpx.

    Uses httpx instead of langchain-openai to avoid the automatic renaming
    of max_tokens → max_completion_tokens introduced in recent versions of
    the openai SDK, which Databricks AI Gateway does not support.

    Compatible with LangChain's pipe operator:
        chain = prompt | model | StrOutputParser()
        result = chain.invoke({...})
        results = chain.batch([{...}, {...}])
    """

    def __init__(
        self,
        model_id: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_GEN_MAX_NEW_TOKENS,
        top_p: float = 1.0,
    ):
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p

    # -----------------------------------------------------------------------
    # Message conversion
    # -----------------------------------------------------------------------
    def _to_openai_messages(self, messages) -> list:
        """Convert LangChain message objects or plain dicts to OpenAI-format dicts."""
        result = []
        for m in messages:
            if isinstance(m, dict):
                result.append(m)
            elif isinstance(m, SystemMessage):
                result.append({"role": "system", "content": m.content})
            elif isinstance(m, HumanMessage):
                result.append({"role": "user", "content": m.content})
            elif isinstance(m, AIMessage):
                result.append({"role": "assistant", "content": m.content})
            elif isinstance(m, BaseMessage):
                role = getattr(m, "type", "user")
                if role == "human":
                    role = "user"
                elif role == "ai":
                    role = "assistant"
                result.append({"role": role, "content": m.content})
            else:
                result.append({"role": "user", "content": str(m)})
        return result

    # -----------------------------------------------------------------------
    # Core API call
    # -----------------------------------------------------------------------
    def _call_api(self, messages: list) -> str:
        import time
        import random

        token, base_url = _get_credentials()

        payload: Dict[str, Any] = {
            "model":       self.model_id,
            "messages":    messages,
            "max_tokens":  self.max_tokens,
            "temperature": self.temperature,
        }
        if self.top_p < 1.0:
            payload["top_p"] = self.top_p

        max_retries = int(os.environ.get("DATABRICKS_CHAT_MAX_RETRIES", "6"))
        base_delay  = float(os.environ.get("DATABRICKS_CHAT_RETRY_DELAY", "5.0"))

        for attempt in range(max_retries):
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
                json=payload,
                timeout=120,
            )

            if response.status_code == 200:
                data = response.json()
                raw_content = data["choices"][0]["message"]["content"]
                return _extract_text_from_chat_content(raw_content)

            if response.status_code == 429:
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        f"Databricks API error 429 after {max_retries} retries: {response.text}"
                    )
                wait = base_delay * (2 ** attempt) + random.uniform(0, 1)
                logger.warn(
                    f"[CHAT] Rate limit hit on model '{self.model_id}'. "
                    f"Retry {attempt + 1}/{max_retries} in {wait:.1f}s..."
                )
                time.sleep(wait)
                continue

            # Any other error — fail immediately
            raise RuntimeError(
                f"Databricks API error {response.status_code}: {response.text}"
            )

        raise RuntimeError("Unreachable — max_retries exhausted")

    # -----------------------------------------------------------------------
    # Runnable interface
    # -----------------------------------------------------------------------
    def invoke(self, input, config=None, **kwargs) -> AIMessage:
        """
        Accept:
          - a list of LangChain message objects (from a ChatPromptTemplate)
          - a list of plain dicts
        """
        if isinstance(input, list):
            messages = self._to_openai_messages(input)
        else:
            # Fallback: treat as single user message
            messages = [{"role": "user", "content": str(input)}]

        content = self._call_api(messages)
        return AIMessage(content=content)

    def stream(self, input, config=None, **kwargs):
        """Minimal stream support — yields the full response as one chunk."""
        result = self.invoke(input, config, **kwargs)
        yield result

    def batch(self, inputs, config=None, **kwargs) -> list:
        """Process a list of inputs sequentially."""
        logger.debug(
            f"Processing chat batch sequentially for model '{self.model_id}' "
            f"with {len(inputs)} input(s)."
        )
        return [self.invoke(i, config, **kwargs) for i in inputs]


# ---------------------------------------------------------------------------
# TEXT MODEL  (used by rag.py for summarization chains)
# ---------------------------------------------------------------------------

def get_text_model(
    text_model_id: str = DEFAULT_TEXT_MODEL_ID,
    temperature: float = DEFAULT_TEMPERATURE,
) -> _DatabricksChatRunnable:
    """
    Return a LangChain Runnable backed by Databricks llama3_3_70b.
    Drop-in replacement for model_loader.get_text_model().
    """
    text_model_id = normalize_model_id(text_model_id)
    key = (text_model_id, round(float(temperature), 4))
    if key not in _TEXT_MODEL_CACHE:
        logger.info(
            f"Creating text model wrapper for Databricks endpoint '{text_model_id}' "
            f"(temperature={temperature:.2f}, max_tokens={TEXT_MAX_NEW_TOKENS})."
        )
        _TEXT_MODEL_CACHE[key] = _DatabricksChatRunnable(
            model_id=text_model_id,
            temperature=temperature,
            max_tokens=TEXT_MAX_NEW_TOKENS,
        )
    else:
        logger.debug(f"Reusing cached text model wrapper for '{text_model_id}'.")
    return _TEXT_MODEL_CACHE[key]


# ---------------------------------------------------------------------------
# CHAT LLM  (used by multiagent.py for evaluator / generator)
# ---------------------------------------------------------------------------

def get_chat_llm(
    llm_model_id: str = DEFAULT_LLM_MODEL_ID,
    kind: str = "generator",
    temperature: float = DEFAULT_TEMPERATURE,
    max_new_tokens: int = DEFAULT_GEN_MAX_NEW_TOKENS,
) -> _DatabricksChatRunnable:
    """
    Return a LangChain Runnable for the evaluator or generator role.
    Drop-in replacement for model_loader.get_chat_llm().
    """
    llm_model_id = normalize_model_id(llm_model_id)
    key = (
        llm_model_id,
        kind,
        round(float(temperature), 4),
        int(max_new_tokens),
    )
    if key not in _CHAT_LLM_CACHE:
        logger.info(
            f"Creating chat LLM wrapper for endpoint '{llm_model_id}' "
            f"(kind={kind}, temperature={temperature:.2f}, max_tokens={max_new_tokens})."
        )
        _CHAT_LLM_CACHE[key] = _DatabricksChatRunnable(
            model_id=llm_model_id,
            temperature=temperature,
            max_tokens=int(max_new_tokens),
        )
    else:
        logger.debug(f"Reusing cached chat LLM wrapper for endpoint '{llm_model_id}' (kind={kind}).")
    return _CHAT_LLM_CACHE[key]


# ---------------------------------------------------------------------------
# EMBEDDINGS  (used by rag.py for Chroma)
# ---------------------------------------------------------------------------

class _DatabricksEmbeddings:
    """
    Chroma-compatible embeddings wrapper using a Databricks embeddings endpoint.

    Applies:
    - BGE-style query prefix on embed_query()
    - Batching to avoid oversized requests
    - Fixed throttle between batches to respect workspace QPS limits
    - Exponential backoff with jitter on rate-limit errors
    """

    _QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    # Conservative defaults — tunable via environment variables
    _BATCH_SIZE = int(os.environ.get("DATABRICKS_EMBED_BATCH_SIZE", "8"))
    _THROTTLE_SECS = float(os.environ.get("DATABRICKS_EMBED_THROTTLE_SECS", "1.0"))
    _MAX_RETRIES = int(os.environ.get("DATABRICKS_EMBED_MAX_RETRIES", "10"))

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self._last_call_time: float = 0.0
        self._cancel_event = None

    def set_cancel_event(self, cancel_event) -> None:
        """Allow long throttled indexing jobs to be cancelled cooperatively."""
        self._cancel_event = cancel_event

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise RuntimeError("Embedding request cancelled.")

    def _throttle(self) -> None:
        """Sleep if needed to ensure minimum interval between calls."""
        self._raise_if_cancelled()
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self._THROTTLE_SECS:
            sleep_time = self._THROTTLE_SECS - elapsed
            logger.debug(
                f"Embedding throttle active for endpoint '{self.endpoint}'. "
                f"Sleeping {sleep_time:.2f}s."
            )
            if self._cancel_event is not None:
                if self._cancel_event.wait(sleep_time):
                    self._raise_if_cancelled()
            else:
                time.sleep(sleep_time)
        self._raise_if_cancelled()
        self._last_call_time = time.monotonic()

    def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        """Call the API for a single batch with retry on rate-limit errors."""
        import random
        from openai import RateLimitError

        for attempt in range(self._MAX_RETRIES):
            try:
                self._raise_if_cancelled()
                self._throttle()
                response = _oai_client().embeddings.create(
                    model=self.endpoint,
                    input=batch,
                )
                items = sorted(response.data, key=lambda x: x.index)
                logger.debug(
                    f"Embedding batch succeeded for endpoint '{self.endpoint}' "
                    f"(batch_size={len(batch)}, attempt={attempt + 1})."
                )
                return [item.embedding for item in items]

            except RateLimitError:
                if attempt == self._MAX_RETRIES - 1:
                    logger.error(
                        f"Embedding rate limit persisted for endpoint '{self.endpoint}' "
                        f"after {self._MAX_RETRIES} attempt(s)."
                    )
                    raise
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warn(
                    f"Embedding rate limit on endpoint '{self.endpoint}' for batch size {len(batch)}. "
                    f"Retry {attempt + 1}/{self._MAX_RETRIES} in {wait:.1f}s."
                )
                if self._cancel_event is not None:
                    if self._cancel_event.wait(wait):
                        self._raise_if_cancelled()
                else:
                    time.sleep(wait)

    def _embed(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts, batching to respect workspace QPS limits."""
        if not texts:
            logger.debug("Received empty embedding request.")
            return []

        all_embeddings: List[List[float]] = []
        total_batches = (len(texts) + self._BATCH_SIZE - 1) // self._BATCH_SIZE

        logger.info(
            f"Embedding {len(texts)} text(s) with endpoint '{self.endpoint}' "
            f"in {total_batches} batch(es) of up to {self._BATCH_SIZE}."
        )

        for i in range(0, len(texts), self._BATCH_SIZE):
            self._raise_if_cancelled()
            batch = texts[i : i + self._BATCH_SIZE]
            batch_num = i // self._BATCH_SIZE + 1
            logger.debug(
                f"Embedding batch {batch_num}/{total_batches} "
                f"for endpoint '{self.endpoint}' ({len(batch)} item(s))."
            )
            embeddings = self._embed_batch(batch)
            all_embeddings.extend(embeddings)

        logger.debug(
            f"Completed embedding request for endpoint '{self.endpoint}' "
            f"({len(all_embeddings)} embedding(s) returned)."
        )
        return all_embeddings

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        logger.debug(f"Embedding query text with endpoint '{self.endpoint}'.")
        return self._embed([self._QUERY_PREFIX + text])[0]


def get_embedding_function(
    embedding_model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
) -> _DatabricksEmbeddings:
    """
    Return a Chroma-compatible embeddings object backed by a Databricks endpoint.
    Drop-in replacement for model_loader.get_embedding_function().
    """
    embedding_model_id = normalize_model_id(embedding_model_id)
    logger.info(f"Creating embeddings wrapper for endpoint '{embedding_model_id}'.")
    return _DatabricksEmbeddings(endpoint=embedding_model_id)


# ---------------------------------------------------------------------------
# VISION BACKEND  (used by rag.py for image summarization)
# ---------------------------------------------------------------------------

def _pil_to_base64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


class _DatabricksVisionModel:
    """
    Vision model backed by a Databricks vision-capable endpoint via Chat Completions API.

    Exposes the same .chat() interface that rag.py uses with InternVL:
        out = model.chat(tokenizer, pixel_values, question, generation_config)

    pixel_values is ignored — the PIL image is stored by internvl_preprocess()
    and encoded as base64 directly in the API call.

    Uses httpx directly (same reason as _DatabricksChatRunnable).
    """

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self._last_pil_image: Optional[Image.Image] = None

    def chat(
        self,
        tokenizer,       # ignored (local concept)
        pixel_values,    # ignored — image comes from _last_pil_image
        question: str,
        generation_config: dict,
    ) -> str:
        if self._last_pil_image is None:
            logger.error(
                f"Vision model '{self.endpoint}' was called without a registered source image."
            )
            raise RuntimeError(
                "_DatabricksVisionModel.chat() called without a prior "
                "internvl_preprocess() call to register the source image."
            )

        clean_question = question.replace("<image>", "").strip()
        img_b64 = _pil_to_base64(self._last_pil_image)
        max_tokens = generation_config.get("max_new_tokens", IMG_MAX_NEW_TOKENS)

        logger.debug(
            f"Calling Databricks vision endpoint '{self.endpoint}' "
            f"(max_tokens={max_tokens}, question_chars={len(clean_question)})."
        )

        token, base_url = _get_credentials()

        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.endpoint,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_b64}",
                                },
                            },
                            {
                                "type": "text",
                                "text": clean_question,
                            },
                        ],
                    }
                ],
                "max_tokens": max_tokens,
            },
            timeout=httpx.Timeout(300.0, connect=30.0),
        )

        if response.status_code != 200:
            logger.error(
                f"Databricks vision API error for endpoint '{self.endpoint}': "
                f"status={response.status_code}"
            )
            logger.debug(f"Databricks vision API response body: {response.text}")
            raise RuntimeError(
                f"Databricks vision API error {response.status_code}: {response.text}"
            )

        logger.debug(f"Databricks vision call completed successfully for endpoint '{self.endpoint}'.")
        data = response.json()
        raw_content = data["choices"][0]["message"]["content"]
        return _extract_text_from_chat_content(raw_content)


# Singleton vision model cache
_VISION_CACHE: Dict[str, _DatabricksVisionModel] = {}


def get_vision_backend(
    vision_model_id: str = DEFAULT_VISION_MODEL_ID,
) -> Dict[str, Any]:
    """
    Return a cached vision backend dict compatible with rag.py:
        backend["model"]     → _DatabricksVisionModel
        backend["tokenizer"] → None  (not used remotely)

    Drop-in replacement for model_loader.get_vision_backend().
    """
    vision_model_id = normalize_model_id(vision_model_id)
    if vision_model_id not in _VISION_CACHE:
        logger.info(f"Creating vision backend for endpoint '{vision_model_id}'.")
        _VISION_CACHE[vision_model_id] = _DatabricksVisionModel(endpoint=vision_model_id)
    else:
        logger.debug(f"Reusing cached vision backend for endpoint '{vision_model_id}'.")

    return {
        "model": _VISION_CACHE[vision_model_id],
        "tokenizer": None,
    }


def internvl_preprocess(
    img: Image.Image,
    input_size: int = 448,
) -> Any:
    """
    API-compatibility shim for model_loader.internvl_preprocess().

    Locally this returns a (1, C, H, W) tensor. Here it stores the PIL image
    on all cached vision model instances and returns a sentinel object whose
    .to() method is a no-op — matching the tensor API used in rag.py:

        pixel_values = internvl_preprocess(img).to(dtype=..., device=...)
        out = model.chat(tokenizer, pixel_values, question, generation_config)
    """
    logger.debug(
        f"Registering image for vision backends via internvl_preprocess() "
        f"(input_size={input_size})."
    )

    class _Sentinel:
        def to(self, **kwargs) -> "_Sentinel":
            return self

    for model in _VISION_CACHE.values():
        model._last_pil_image = img

    return _Sentinel()
