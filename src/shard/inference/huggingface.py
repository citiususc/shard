"""Local Hugging Face adapters for chat and embedding inference."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import torch
import torch.nn.functional as F

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from shard.observability import logger
from .context import get_hf_token
from .local_store import cached_model_path


# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

DEFAULT_LLM_MODEL_ID       = "meta-llama/Llama-3.3-70B-Instruct"
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_TEMPERATURE        = 0.50

DEFAULT_EVAL_MAX_NEW_TOKENS = 700
DEFAULT_GEN_MAX_NEW_TOKENS  = 3000

# Model IDs that need special handling
_GPT_OSS_IDS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}
_QWEN3_NEXT_IDS = {"Qwen/Qwen3-Next-80B-A3B-Instruct"}


# ---------------------------------------------------------------------------
# Torch dtype / device helpers
# ---------------------------------------------------------------------------

def _torch_dtype() -> torch.dtype:
    spec = os.environ.get("TORCH_DTYPE", "auto").lower()
    if spec == "float16":
        return torch.float16
    if spec == "float32":
        return torch.float32
    if spec == "bfloat16":
        return torch.bfloat16
    # auto: prefer bfloat16 on Ampere+ GPUs
    if torch.cuda.is_available():
        return torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
    return torch.float32


def _device_map() -> str:
    return "auto" if torch.cuda.is_available() else "cpu"


# ---------------------------------------------------------------------------
# HuggingFace download helper
# ---------------------------------------------------------------------------

def _ensure_model(model_id: str) -> str:
    """Return an explicitly downloaded snapshot without network access."""
    snapshot_path = cached_model_path(model_id)
    logger.debug(f"Model '{model_id}' found in the local cache.")
    return snapshot_path


# ---------------------------------------------------------------------------
# Message normalisation
# ---------------------------------------------------------------------------

def _extract_text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text", "")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content).strip()


def _messages_to_prompt(messages: List[Dict[str, str]], tokenizer) -> str:
    """Apply chat template if available, else use a plain-text fallback."""
    if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
        try:
            return tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
            )
        except Exception as e:
            logger.debug(f"apply_chat_template failed ({e}), using fallback.")
    parts = [f"<|{m.get('role','user')}|>\n{m.get('content','')}" for m in messages]
    parts.append("<|assistant|>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Chat / text model loading — per-model-family strategies
# ---------------------------------------------------------------------------

_CHAT_MODEL_CACHE: Dict[str, Dict[str, Any]] = {}


def _load_standard_causal_lm(model_id: str) -> Dict[str, Any]:
    """Load a standard causal LM (Llama, Mixtral, Qwen3-Next, etc.)."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_path = _ensure_model(model_id)
    hf_token = get_hf_token() or None

    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        trust_remote_code=True,
        token=hf_token,
        clean_up_tokenization_spaces=False,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=_torch_dtype(),
        device_map=_device_map(),
        trust_remote_code=True,
        token=hf_token,
    )
    model.eval()
    return {"model": model, "tokenizer": tokenizer, "backend": "causal_lm"}


def _load_gpt_oss(model_id: str) -> Dict[str, Any]:
    from transformers import pipeline as hf_pipeline, AutoTokenizer

    model_path = _ensure_model(model_id)
    hf_token = get_hf_token() or None

    logger.info(
        f"Loading gpt-oss model '{model_id}' — uses built-in MXFP4 quantization."
    )

    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        trust_remote_code=True,
        token=hf_token,
        clean_up_tokenization_spaces=False,
    )

    pipe = hf_pipeline(
        "text-generation",
        model=model_path,
        tokenizer=tokenizer,
        torch_dtype="auto",      # Preserve the MXFP4 dtype from config.json.
        device_map="balanced",
        token=hf_token,
        trust_remote_code=True,
    )

    return {"pipeline": pipe, "tokenizer": tokenizer, "backend": "gpt_oss"}


def _load_chat_model(model_id: str) -> Dict[str, Any]:
    if model_id in _CHAT_MODEL_CACHE:
        logger.debug(f"Reusing cached chat model '{model_id}'.")
        return _CHAT_MODEL_CACHE[model_id]

    logger.info(f"Loading chat model '{model_id}'.")

    if model_id in _GPT_OSS_IDS:
        backend = _load_gpt_oss(model_id)
    else:
        # Llama-3.3, Qwen3-Next, Mixtral all load identically via AutoModelForCausalLM
        backend = _load_standard_causal_lm(model_id)

    _CHAT_MODEL_CACHE[model_id] = backend
    logger.info(f"Chat model '{model_id}' loaded (backend={backend['backend']}).")
    return backend


# ---------------------------------------------------------------------------
# LocalChatRunnable — LangChain Runnable backed by local HF models
# ---------------------------------------------------------------------------

class _LocalChatRunnable(Runnable):
    """
    LangChain Runnable with the same interface as _DatabricksChatRunnable.

    Compatible with:
        chain = prompt | model | StrOutputParser()
        result = chain.invoke({...})
        results = chain.batch([...])
    """

    def __init__(
        self,
        model_id: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int    = DEFAULT_GEN_MAX_NEW_TOKENS,
        top_p: float       = 1.0,
    ):
        self.model_id    = model_id
        self.temperature = temperature
        self.max_tokens  = max_tokens
        self.top_p       = top_p

    # ---- Message conversion -----------------------------------------------

    def _to_dicts(self, messages) -> List[Dict[str, str]]:
        result = []
        for m in messages:
            if isinstance(m, dict):
                result.append(m)
            elif isinstance(m, SystemMessage):
                result.append({"role": "system",    "content": m.content})
            elif isinstance(m, HumanMessage):
                result.append({"role": "user",      "content": m.content})
            elif isinstance(m, AIMessage):
                result.append({"role": "assistant", "content": m.content})
            elif isinstance(m, BaseMessage):
                role = {"human": "user", "ai": "assistant"}.get(
                    getattr(m, "type", "user"), "user"
                )
                result.append({"role": role, "content": m.content})
            else:
                result.append({"role": "user", "content": str(m)})
        return result

    # ---- Causal LM generation ---------------------------------------------

    def _generate_causal_lm(self, backend: dict, messages: List[Dict]) -> str:
        model     = backend["model"]
        tokenizer = backend["tokenizer"]

        prompt = _messages_to_prompt(messages, tokenizer)
        inputs = tokenizer(prompt, return_tensors="pt")
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

        do_sample = self.temperature > 0.0
        with torch.inference_mode():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=self.max_tokens,
                do_sample=do_sample,
                temperature=self.temperature if do_sample else None,
                top_p=self.top_p if (do_sample and self.top_p < 1.0) else None,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
                max_length=None,
            )

        input_len = inputs["input_ids"].shape[1]
        new_ids   = output_ids[0][input_len:]
        return tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    # ---- gpt-oss pipeline generation -------------------------------------

    def _generate_gpt_oss(self, backend: dict, messages: List[Dict]) -> str:
        pipe = backend["pipeline"]
        do_sample = self.temperature > 0.0

        outputs = pipe(
            messages,
            max_new_tokens=self.max_tokens,
            temperature=self.temperature if do_sample else None,
            top_p=self.top_p if (do_sample and self.top_p < 1.0) else None,
            do_sample=do_sample,
        )
        # The pipeline returns the full conversation; extract the last assistant turn
        generated = outputs[0]["generated_text"]
        if isinstance(generated, list):
            # Chat template format: list of dicts, last item is assistant response
            last = generated[-1]
            if isinstance(last, dict):
                return last.get("content", "").strip()
        return str(generated).strip()

    # ---- Core dispatch ----------------------------------------------------

    def _generate(self, messages) -> str:
        backend = _load_chat_model(self.model_id)

        if backend["backend"] == "gpt_oss":
            return self._generate_gpt_oss(backend, messages)
        return self._generate_causal_lm(backend, messages)

    # ---- Runnable interface -----------------------------------------------

    def invoke(self, input, config=None, **kwargs) -> AIMessage:
        messages = input if isinstance(input, list) else [{"role": "user", "content": str(input)}]
        return AIMessage(content=self._generate(self._to_dicts(messages)))

    def stream(self, input, config=None, **kwargs):
        yield self.invoke(input, config, **kwargs)

    def batch(self, inputs, config=None, **kwargs) -> List[AIMessage]:
        logger.debug(
            f"Processing chat batch sequentially for '{self.model_id}' "
            f"({len(inputs)} input(s))."
        )
        return [self.invoke(i, config, **kwargs) for i in inputs]


# ---------------------------------------------------------------------------
# Public chat / text factories
# ---------------------------------------------------------------------------

_RUNNABLE_CACHE: Dict[Tuple, _LocalChatRunnable] = {}


def get_chat_llm(
    llm_model_id: str   = DEFAULT_LLM_MODEL_ID,
    kind: str           = "generator",
    temperature: float  = DEFAULT_TEMPERATURE,
    max_new_tokens: int = DEFAULT_GEN_MAX_NEW_TOKENS,
) -> _LocalChatRunnable:
    """Return a LangChain Runnable for generation or resolution."""
    key = (llm_model_id, kind, round(float(temperature), 4), int(max_new_tokens))
    if key not in _RUNNABLE_CACHE:
        logger.info(
            f"Creating chat LLM runnable for '{llm_model_id}' "
            f"(kind={kind}, temperature={temperature:.2f}, max_tokens={max_new_tokens})."
        )
        _RUNNABLE_CACHE[key] = _LocalChatRunnable(
            model_id=llm_model_id,
            temperature=temperature,
            max_tokens=int(max_new_tokens),
        )
    return _RUNNABLE_CACHE[key]


# ---------------------------------------------------------------------------
# Embeddings — Qwen3-Embedding-0.6B (last-token pooling)
# ---------------------------------------------------------------------------

class _LocalEmbeddings:
    """
    LangChain-compatible embeddings wrapper.

    Supports two pooling strategies detected automatically:
    - Qwen3-Embedding: last-token pooling with task-specific instruction prefix
    - Standard sentence-embedding models: pooling via SentenceTransformer

    Qwen3-Embedding uses a causal LM architecture (decoder-only) where the
    embedding is the hidden state of the last token. It does NOT work correctly
    with sentence-transformers' default mean pooling — use AutoModel + manual
    last-token pooling instead.
    """

    # Qwen3-Embedding instruction format
    _QWEN3_EMBED_TASK = (
        "Given a technical document about an ontology and its domain, "
        "retrieve relevant ontology terms that match the query"
    )
    _QWEN3_EMBED_IDS = {"Qwen/Qwen3-Embedding-0.6B", "Qwen/Qwen3-Embedding-4B", "Qwen/Qwen3-Embedding-8B"}

    def __init__(self, model_id: str):
        self.model_id      = model_id
        self._model        = None
        self._tokenizer    = None
        self._st_model     = None
        self._is_qwen3_emb = model_id in self._QWEN3_EMBED_IDS

    # ---- Lazy loading -------------------------------------------------------

    def _load(self) -> None:
        if self._model is not None or self._st_model is not None:
            return

        model_path = _ensure_model(self.model_id)
        hf_token = get_hf_token() or None
        device   = "cuda" if torch.cuda.is_available() else "cpu"

        if self._is_qwen3_emb:
            from transformers import AutoModel, AutoTokenizer
            logger.info(
                f"Loading Qwen3-Embedding model '{self.model_id}' "
                f"with last-token pooling."
            )
            self._tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=True,
                padding_side="left",
                token=hf_token,
            )
            self._model = AutoModel.from_pretrained(
                model_path,
                torch_dtype=_torch_dtype(),
                device_map=device,
                trust_remote_code=True,
                token=hf_token,
            )
            self._model.eval()
            logger.info(f"Qwen3-Embedding model '{self.model_id}' loaded on {device}.")
        else:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model '{self.model_id}' via SentenceTransformer.")
            self._st_model = SentenceTransformer(model_path, device=device)
            logger.info(f"Embedding model '{self.model_id}' loaded on {device}.")

    # ---- Qwen3-Embedding: last-token pooling --------------------------------

    @staticmethod
    def _last_token_pool(
        last_hidden_state: torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> torch.Tensor:
        """
        Extract the embedding from the last non-padding token.
        Qwen3-Embedding is a decoder-only model — mean pooling is incorrect.
        """
        left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
        if left_padding:
            return last_hidden_state[:, -1]
        seq_lens = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_state.shape[0]
        return last_hidden_state[
            torch.arange(batch_size, device=last_hidden_state.device), seq_lens
        ]

    def _qwen3_embed(self, texts: List[str]) -> List[List[float]]:
        MAX_LENGTH = 8192
        batch_dict = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="pt",
        )
        batch_dict = {k: v.to(self._model.device) for k, v in batch_dict.items()}

        with torch.inference_mode():
            outputs = self._model(**batch_dict)

        embeddings = self._last_token_pool(
            outputs.last_hidden_state, batch_dict["attention_mask"]
        )
        embeddings = F.normalize(embeddings, p=2, dim=1)
        return embeddings.cpu().float().tolist()

    def _qwen3_query_text(self, text: str) -> str:
        return f"Instruct: {self._QWEN3_EMBED_TASK}\nQuery: {text}"

    # ---- Public API ---------------------------------------------------------

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        self._load()
        logger.debug(f"Embedding {len(texts)} document(s) with '{self.model_id}'.")
        if self._is_qwen3_emb:
            return self._qwen3_embed(texts)
        encode = getattr(self._st_model, "encode_document", self._st_model.encode)
        vectors = encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> List[float]:
        self._load()
        logger.debug(f"Embedding query with '{self.model_id}'.")
        if self._is_qwen3_emb:
            return self._qwen3_embed([self._qwen3_query_text(text)])[0]
        encode = getattr(self._st_model, "encode_query", self._st_model.encode)
        vectors = encode([text], show_progress_bar=False, normalize_embeddings=True)
        return vectors[0].tolist()


_EMBEDDING_CACHE: Dict[str, _LocalEmbeddings] = {}


def get_embedding_function(
    embedding_model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
) -> _LocalEmbeddings:
    """Return a LangChain-compatible embeddings object backed by a local model."""
    if embedding_model_id not in _EMBEDDING_CACHE:
        logger.info(f"Creating embeddings wrapper for '{embedding_model_id}'.")
        _EMBEDDING_CACHE[embedding_model_id] = _LocalEmbeddings(embedding_model_id)
    return _EMBEDDING_CACHE[embedding_model_id]
