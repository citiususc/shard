"""
model_loader_hf.py

Loads all three model types used in text2shacl via HuggingFace Transformers,
running locally on GPU (or CPU as fallback).

Drop-in replacement for model_loader_databricks.py — same public API.
Models are downloaded automatically on first use and cached in the HuggingFace
default cache directory (~/.cache/huggingface/).

SUPPORTED MODELS
----------------
Text / Chat:
  meta-llama/Llama-3.3-70B-Instruct     Standard causal LM with chat template
  Qwen/Qwen3-Next-80B-A3B-Instruct      Hybrid Transformer-Mamba MoE (requires
                                         transformers from main branch)
  openai/gpt-oss-120b                    MoE with MXFP4 quant + harmony format
  mistralai/Mixtral-8x7B-Instruct-v0.1  Standard MoE causal LM

Vision:
  google/gemma-3-12b-it                  Multimodal via AutoProcessor +
                                         Gemma3ForConditionalGeneration

Embeddings:
  Qwen/Qwen3-Embedding-0.6B             Uses last-token pooling (NOT mean
                                         pooling) with task-specific instructions

CONFIGURATION
-------------
The demo UI sends the HuggingFace access token per request when a gated/private
model needs it. The following environment variables remain optional tuning knobs:

HF_HOME                     Override HuggingFace cache directory
TORCH_DTYPE                 "float16" | "bfloat16" | "float32"  (default: auto)
RAG_TEXT_MAX_NEW_TOKENS     Max tokens for text summarisation   (default: 256)
RAG_IMG_MAX_NEW_TOKENS      Max tokens for image summarisation  (default: 900)

USAGE
-----
    from model_loader_hf import (
        get_chat_llm,
        get_text_model,
        get_embedding_function,
        get_vision_backend,
        internvl_preprocess,
        DEFAULT_TEXT_MODEL_ID,
        DEFAULT_VISION_MODEL_ID,
        DEFAULT_EMBEDDING_MODEL_ID,
        DEFAULT_EVAL_MAX_NEW_TOKENS,
        DEFAULT_GEN_MAX_NEW_TOKENS,
        IMG_MAX_NEW_TOKENS,
    )
"""

from __future__ import annotations

import os
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from PIL import Image

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from Logger import logger
from runtime_config import get_hf_token


# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

DEFAULT_LLM_MODEL_ID       = "meta-llama/Llama-3.3-70B-Instruct"
DEFAULT_TEXT_MODEL_ID      = "meta-llama/Llama-3.3-70B-Instruct"
DEFAULT_VISION_MODEL_ID    = "google/gemma-3-12b-it"
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_TEMPERATURE        = 0.50

TEXT_MAX_NEW_TOKENS         = int(os.environ.get("RAG_TEXT_MAX_NEW_TOKENS", "800"))
IMG_MAX_NEW_TOKENS          = int(os.environ.get("RAG_IMG_MAX_NEW_TOKENS",  "2000"))
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
    """Return a complete local snapshot path, downloading it when necessary."""
    from huggingface_hub import snapshot_download

    ignore_patterns = [
        "*.msgpack", "*.h5",
        "flax_model*", "tf_model*",
        "original/*",
    ]
    try:
        snapshot_path = snapshot_download(
            repo_id=model_id,
            token=get_hf_token() or None,
            ignore_patterns=ignore_patterns,
            local_files_only=True,
        )
        logger.debug(f"Model '{model_id}' found in HuggingFace cache.")
        return snapshot_path
    except Exception:
        pass

    logger.info(f"Model '{model_id}' not found locally — downloading.")
    snapshot_path = snapshot_download(
        repo_id=model_id,
        token=get_hf_token() or None,
        ignore_patterns=ignore_patterns,
    )
    logger.info(f"Model '{model_id}' downloaded successfully.")
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


def get_text_model(
    text_model_id: str = DEFAULT_TEXT_MODEL_ID,
    temperature: float = DEFAULT_TEMPERATURE,
) -> _LocalChatRunnable:
    """Return a LangChain Runnable for text summarisation (used by rag.py)."""
    key = (text_model_id, round(float(temperature), 4), int(TEXT_MAX_NEW_TOKENS))
    if key not in _RUNNABLE_CACHE:
        logger.info(
            f"Creating text model runnable for '{text_model_id}' "
            f"(temperature={temperature:.2f}, max_tokens={TEXT_MAX_NEW_TOKENS})."
        )
        _RUNNABLE_CACHE[key] = _LocalChatRunnable(
            model_id=text_model_id,
            temperature=temperature,
            max_tokens=TEXT_MAX_NEW_TOKENS,
        )
    return _RUNNABLE_CACHE[key]


def get_chat_llm(
    llm_model_id: str   = DEFAULT_LLM_MODEL_ID,
    kind: str           = "generator",
    temperature: float  = DEFAULT_TEMPERATURE,
    max_new_tokens: int = DEFAULT_GEN_MAX_NEW_TOKENS,
) -> _LocalChatRunnable:
    """Return a LangChain Runnable for evaluator / generator (used by multiagent.py)."""
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
    Chroma-compatible embeddings wrapper.

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
    """Return a Chroma-compatible embeddings object backed by a local model."""
    if embedding_model_id not in _EMBEDDING_CACHE:
        logger.info(f"Creating embeddings wrapper for '{embedding_model_id}'.")
        _EMBEDDING_CACHE[embedding_model_id] = _LocalEmbeddings(embedding_model_id)
    return _EMBEDDING_CACHE[embedding_model_id]


# ---------------------------------------------------------------------------
# Vision backend — google/gemma-3-12b-it (and generic VLMs)
# ---------------------------------------------------------------------------

_VISION_CACHE: Dict[str, "_LocalVisionModel"] = {}

_GEMMA3_IDS = {"google/gemma-3-12b-it", "google/gemma-3-27b-it", "google/gemma-3-4b-it"}


class _LocalVisionModel:
    """
    Vision model backed by a local HuggingFace model.

    Gemma 3 (google/gemma-3-12b-it) is a multimodal model that uses
    AutoProcessor + Gemma3ForConditionalGeneration. It does NOT follow the
    InternVL .chat() interface — the processor handles image encoding natively.

    For InternVL models (OpenGVLab/InternVL*), uses the model's own .chat()
    method with InternVL-style tiled pixel_values.

    For all other VLMs, uses AutoProcessor + model.generate() as a generic
    fallback.

    Exposes the same .chat() interface expected by rag.py:
        out = model.chat(tokenizer, pixel_values, question, generation_config)
    """

    def __init__(self, model_id: str):
        self.model_id        = model_id
        self._model          = None
        self._tokenizer      = None
        self._processor      = None
        self._backend        = None   # "gemma3" | "internvl" | "generic"
        self._last_pil_image: Optional[Image.Image] = None

    def _load(self) -> None:
        if self._model is not None:
            return

        model_path = _ensure_model(self.model_id)
        hf_token = get_hf_token() or None
        dtype    = _torch_dtype()

        if self.model_id in _GEMMA3_IDS:
            self._load_gemma3(model_path, hf_token, dtype)
        else:
            self._load_generic(model_path, hf_token, dtype)

    def _load_gemma3(self, model_path, hf_token, dtype) -> None:
        from transformers import AutoProcessor, Gemma3ForConditionalGeneration

        logger.info(f"Loading Gemma 3 vision model '{self.model_id}'.")
        self._processor = AutoProcessor.from_pretrained(
            model_path, token=hf_token,
        )
        self._model = Gemma3ForConditionalGeneration.from_pretrained(
            model_path,
            torch_dtype=dtype,
            device_map=_device_map(),
            token=hf_token,
        )
        self._model.eval()
        self._backend = "gemma3"
        logger.info(f"Gemma 3 vision model '{self.model_id}' loaded.")

    def _load_generic(self, model_path, hf_token, dtype) -> None:
        from transformers import AutoConfig, AutoModel, AutoProcessor

        load_kwargs = {
            "torch_dtype": dtype,
            "device_map": _device_map(),
            "trust_remote_code": True,
            "token": hf_token,
        }
        config = AutoConfig.from_pretrained(
            model_path, trust_remote_code=True, token=hf_token,
        )
        architectures = " ".join(getattr(config, "architectures", None) or [])
        if "InternVL" in architectures or "InternLM" in architectures:
            self._model = AutoModel.from_pretrained(model_path, **load_kwargs)
        else:
            try:
                from transformers import AutoModelForImageTextToText
                self._model = AutoModelForImageTextToText.from_pretrained(
                    model_path, **load_kwargs,
                )
            except (ImportError, ValueError):
                try:
                    from transformers import AutoModelForVision2Seq
                    self._model = AutoModelForVision2Seq.from_pretrained(
                        model_path, **load_kwargs,
                    )
                except (ImportError, ValueError):
                    self._model = AutoModel.from_pretrained(model_path, **load_kwargs)
        self._model.eval()

        arch = type(self._model).__name__
        if "InternVL" in arch or "InternLM" in arch:
            from transformers import AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained(
                model_path, trust_remote_code=True, token=hf_token,
            )
            self._backend = "internvl"
            logger.info(f"Vision model '{self.model_id}' loaded as InternVL backend.")
        else:
            try:
                self._processor = AutoProcessor.from_pretrained(
                    model_path, trust_remote_code=True, token=hf_token,
                )
            except Exception:
                from transformers import AutoTokenizer
                self._tokenizer = AutoTokenizer.from_pretrained(
                    model_path, trust_remote_code=True, token=hf_token,
                )
            self._backend = "generic"
            logger.info(f"Vision model '{self.model_id}' loaded as generic VLM backend.")

    def register_image(self, img: Image.Image) -> None:
        self._last_pil_image = img

    # ---- Gemma 3 generation -----------------------------------------------

    def _chat_gemma3(self, question: str, max_tokens: int) -> str:
        if self._last_pil_image is None:
            raise RuntimeError("No image registered. Call internvl_preprocess() first.")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": self._last_pil_image},
                    {"type": "text",  "text":  question},
                ],
            }
        ]
        inputs = self._processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=False,
            )
        input_len = inputs["input_ids"].shape[1]
        new_ids   = output_ids[0][input_len:]
        return self._processor.tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    # ---- InternVL pixel_values builder ------------------------------------

    def _build_internvl_pixel_values(
        self, img: Image.Image, input_size: int = 448
    ) -> torch.Tensor:
        import torchvision.transforms as T
        from torchvision.transforms.functional import InterpolationMode

        transform = T.Compose([
            T.Resize((input_size, input_size), interpolation=InterpolationMode.BICUBIC),
            T.ToTensor(),
            T.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ])
        tensor = transform(img.convert("RGB")).unsqueeze(0)
        device = next(self._model.parameters()).device
        dtype  = next(self._model.parameters()).dtype
        return tensor.to(device=device, dtype=dtype)

    # ---- Generic VLM generation -------------------------------------------

    def _chat_generic(self, question: str, max_tokens: int) -> str:
        if self._last_pil_image is None:
            raise RuntimeError("No image registered. Call internvl_preprocess() first.")

        device = next(self._model.parameters()).device

        if self._processor is not None:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": self._last_pil_image},
                        {"type": "text", "text": question},
                    ],
                }
            ]
            try:
                inputs = self._processor.apply_chat_template(
                    messages,
                    add_generation_prompt=True,
                    tokenize=True,
                    return_dict=True,
                    return_tensors="pt",
                )
            except (AttributeError, TypeError, ValueError):
                inputs = self._processor(
                    text=f"<image>\n{question}",
                    images=self._last_pil_image,
                    return_tensors="pt",
                )
            inputs = {k: v.to(device) for k, v in inputs.items()}
            tok    = self._processor.tokenizer
        else:
            inputs = self._tokenizer(question, return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            tok    = self._tokenizer

        with torch.inference_mode():
            output_ids = self._model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
        input_ids = inputs.get("input_ids")
        input_len = input_ids.shape[1] if input_ids is not None else 0
        return tok.decode(output_ids[0][input_len:], skip_special_tokens=True).strip()

    # ---- Public .chat() interface -----------------------------------------

    def chat(
        self,
        tokenizer,       # InternVL: real tokenizer; others: ignored
        pixel_values,    # InternVL: real tensor; others: sentinel from internvl_preprocess
        question: str,
        generation_config: dict,
    ) -> str:
        self._load()

        max_tokens = generation_config.get("max_new_tokens", IMG_MAX_NEW_TOKENS)
        clean_q    = question.replace("<image>", "").strip()

        if self._backend == "gemma3":
            return self._chat_gemma3(clean_q, max_tokens)

        if self._backend == "internvl":
            if self._last_pil_image is None:
                raise RuntimeError("No image registered.")
            pv  = self._build_internvl_pixel_values(self._last_pil_image)
            tok = tokenizer if tokenizer is not None else self._tokenizer
            return self._model.chat(
                tokenizer=tok,
                pixel_values=pv,
                question=clean_q,
                generation_config=generation_config,
            )

        return self._chat_generic(clean_q, max_tokens)


def get_vision_backend(
    vision_model_id: str = DEFAULT_VISION_MODEL_ID,
) -> Dict[str, Any]:
    """
    Return a cached vision backend dict compatible with rag.py:
        backend["model"]     → _LocalVisionModel
        backend["tokenizer"] → tokenizer (None for Gemma 3 / generic VLMs)
    """
    if vision_model_id not in _VISION_CACHE:
        logger.info(f"Creating vision backend for '{vision_model_id}'.")
        _VISION_CACHE[vision_model_id] = _LocalVisionModel(vision_model_id)

    vm = _VISION_CACHE[vision_model_id]
    vm._load()

    return {"model": vm, "tokenizer": vm._tokenizer}


def internvl_preprocess(
    img: Image.Image,
    input_size: int = 448,
) -> Any:
    """
    API-compatibility shim for rag.py:

        pixel_values = internvl_preprocess(img)
        out = model.chat(tokenizer, pixel_values, question, generation_config)

    Registers the PIL image on all cached vision models and returns a sentinel
    whose .to() is a no-op. The actual tensor/processor call happens inside
    _LocalVisionModel.chat() so device and dtype are handled correctly per model.
    """
    logger.debug(f"Registering image for vision backends (input_size={input_size}).")
    for vm in _VISION_CACHE.values():
        vm.register_image(img)

    class _Sentinel:
        def to(self, **kwargs) -> "_Sentinel":
            return self

    return _Sentinel()
