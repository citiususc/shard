"""
rag.py

RAG pre-processing pipeline for the RINF Application Guide HTML documents.

Responsibilities:
- Splits the HTML document into text chunks, tables, and images via the
  appropriate preprocessor (preprocess_html for v3.2.1, preprocess_html_from_pdf
  for v1.6.1).
- Summarizes content using LLMs via Databricks AI Gateway:
    - Text chunks and tables: text LLM (configurable, default Llama 3.3 70B)
    - Images: vision model (configurable, default Gemma 3 12B)
- Embeds summaries into a Chroma vector store and stores original chunks in
  a Redis docstore, connected through a LangChain MultiVectorRetriever.
- Caches RAG artifacts (summaries + images) per namespace to avoid
  re-summarization on subsequent runs.

Cache invalidation:
- If the Chroma collection is empty despite the index directory existing
  (e.g. interrupted indexing), the cache is cleared and rebuilt from scratch.
- Pass force_process=True to force a full rebuild regardless of cache state.

Required environment variables:
  DATABRICKS_TOKEN      dapi...
  DATABRICKS_BASE_URL   https://<workspace>/ai-gateway/mlflow/v1

Optional environment variables:
  CHROMA_ROOT           Override Chroma index directory (default: cache/chroma_db)
  RAG_CACHE_ROOT        Override processing cache directory (default: cache/processing_cache)
  RAG_MAX_CONCURRENCY   Summarization concurrency (default: 1)

Redis must be running at redis://localhost:6379 (or override REDIS_URL).
"""

from __future__ import annotations

import base64
import hashlib
import os
import pickle
import re
import shutil
from io import BytesIO
from typing import Any, Dict, List, Tuple

from PIL import Image

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_chroma import Chroma
from langchain_community.storage.redis import RedisStore

try:
    from langchain.retrievers.multi_vector import MultiVectorRetriever
except ModuleNotFoundError:
    from langchain_classic.retrievers.multi_vector import MultiVectorRetriever

from model_loader import (
    DEFAULT_TEXT_MODEL_ID,
    DEFAULT_VISION_MODEL_ID,
    DEFAULT_EMBEDDING_MODEL_ID,
    DEFAULT_TEMPERATURE,
    IMG_MAX_NEW_TOKENS,
    TEXT_MAX_NEW_TOKENS,
    get_text_model,
    get_embedding_function,
    get_vision_backend,
    internvl_preprocess,
)

from preprocess_html import split_html
from preprocess_html_from_pdf import split_html_from_pdf
from prompts import load_prompt_from_json
from Logger import logger


# ---------------------------------------------------------------------------
# Paths — anchored to project root so the module works regardless of the
# working directory from which the process is launched.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROMPT_FILE_DEFAULT = os.path.join(_PROJECT_ROOT, "src", "prompts", "rag.json")
REDIS_URL = "redis://localhost:6379"

MAX_CONCURRENCY = int(os.environ.get("RAG_MAX_CONCURRENCY", "1"))
DEFAULT_CHROMA_ROOT = os.environ.get(
    "CHROMA_ROOT",
    os.path.join(_PROJECT_ROOT, "cache", "chroma_db"),
)
DEFAULT_CACHE_ROOT = os.environ.get(
    "RAG_CACHE_ROOT",
    os.path.join(_PROJECT_ROOT, "cache", "processing_cache"),
)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
_MODEL_ID_TO_TAG: dict[str, str] = {
    "meta-llama/Llama-3.3-70B-Instruct":        "llama3_3_70b",
    "openai/gpt-oss-120b":                       "gpt-oss-120b",
    "Qwen/Qwen3-Next-80B-A3B-Instruct":          "qwen3-next80b-a3b-instruct",
    "mistralai/Mixtral-8x7B-Instruct-v0.1":      "mixtral-8x7b",
    "google/gemma-3-12b-it":                     "gemma_3_12b",
    "Qwen/Qwen3-Embedding-0.6B":                 "qwen3_embedding_0_6b",
}


def _safe_tag(s: str, max_len: int = 60) -> str:
    """Normalise a model ID or Databricks endpoint name to a safe filename tag."""
    s = s.strip()
    if s.startswith("databricks-"):
        s = s[len("databricks-"):]
    s = _MODEL_ID_TO_TAG.get(s, s)
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", s)
    return s[:max_len].strip("-") or "model"


def _stable_hash(*parts: str, length: int = 12) -> str:
    """Deterministic short hash of one or more string parts."""
    raw = "||".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:length]


def _make_collection_name(namespace: str, prefix_len: int = 40) -> str:
    """
    Build a short, collision-resistant Chroma collection name from a namespace.
    Appends a SHA-1 digest to avoid silent collisions from blind truncation.
    """
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", namespace.lower()).strip("-")
    digest = _stable_hash(namespace, length=12)
    prefix = safe[:prefix_len].strip("-") or "collection"
    return f"{prefix}-{digest}"


def _make_doc_id(namespace: str, kind: str, index: int) -> str:
    """Deterministic Redis key so the docstore can be rebuilt on cache hit."""
    return f"{kind}-{_stable_hash(namespace, kind, str(index), length=24)}"


def _b64_to_pil(b64_str: str) -> Image.Image:
    img_bytes = base64.b64decode(b64_str)
    return Image.open(BytesIO(img_bytes)).convert("RGB")


def _is_valid_image_summary(summary: str) -> bool:
    """Return True if the image summary is usable (not an error placeholder)."""
    return "[IMAGE_SUMMARY_ERROR]" not in summary


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------
def _render_chatprompt_to_system_user(prompt_tpl, variables: dict) -> Tuple[str, str]:
    """Convert a ChatPromptTemplate into (system_text, user_text) strings."""
    msgs = prompt_tpl.format_messages(**variables)
    system_parts: List[str] = []
    user_parts: List[str] = []

    for m in msgs:
        m_type = getattr(m, "type", "")
        content = getattr(m, "content", "")
        if not isinstance(content, str):
            content = str(content)
        if m_type == "system":
            system_parts.append(content)
        elif m_type in ("human", "user"):
            user_parts.append(content)

    return ("\n\n".join(system_parts).strip(), "\n\n".join(user_parts).strip())


# ---------------------------------------------------------------------------
# Vision summarization
# ---------------------------------------------------------------------------
def _summarize_images_with_vision(
    vision_model_id: str,
    image_b64: List[str],
    image_refs: List[str],
    prompt_file: str,
) -> List[str]:
    """
    Describe each image using the configured vision model.
    Returns one Markdown string per image; errors are returned as placeholder
    strings prefixed with [IMAGE_SUMMARY_ERROR] so they can be filtered out.
    """
    if not image_b64:
        logger.debug("No images found for vision summarization.")
        return []

    logger.info(
        f"Starting image summarization with vision model '{vision_model_id}' "
        f"for {len(image_b64)} image(s)."
    )

    prompt_vl = load_prompt_from_json(prompt_file, "image_summarization_vl")
    backend = get_vision_backend(vision_model_id)
    model = backend["model"]
    tokenizer = backend["tokenizer"]

    generation_config = dict(max_new_tokens=IMG_MAX_NEW_TOKENS)
    outputs: List[str] = []

    for i, b64 in enumerate(image_b64):
        image_ref = image_refs[i] if i < len(image_refs) else ""
        logger.debug(
            f"Summarizing image {i + 1}/{len(image_b64)}"
            + (f" from '{image_ref}'." if image_ref else ".")
        )

        try:
            img = _b64_to_pil(b64)

            system_text, user_text = _render_chatprompt_to_system_user(
                prompt_vl,
                {"image_ref": image_ref},
            )

            question = "<image>\n"
            if system_text:
                question += system_text + "\n\n"
            question += user_text

            pixel_values = internvl_preprocess(img)

            out = model.chat(
                tokenizer=tokenizer,
                pixel_values=pixel_values,
                question=question,
                generation_config=generation_config,
            )
            outputs.append(str(out).strip())
            logger.debug(f"Image {i + 1}/{len(image_b64)} summarized successfully.")

        except Exception as e:
            logger.warn(
                f"Vision summarization failed for image {i + 1}/{len(image_b64)}"
                + (f" ('{image_ref}')" if image_ref else "")
                + f": {e}"
            )
            outputs.append(
                "IMAGE_TYPE: OTHER\n"
                "TITLE:\n"
                "## DETAILED_DESCRIPTION\n"
                f"[IMAGE_SUMMARY_ERROR] {e}\n"
                "## OCR_TEXT\n\n"
                "## CONSTRAINT_HINTS\n- (none)\n"
            )

    valid_outputs = sum(_is_valid_image_summary(s) for s in outputs)
    logger.info(
        f"Finished image summarization: {valid_outputs}/{len(outputs)} valid summary(ies)."
    )
    return outputs


# ---------------------------------------------------------------------------
# Summarization orchestration
# ---------------------------------------------------------------------------
def _summarize(
    prompt_file: str,
    text_model_id: str,
    vision_model_id: str,
    model_temperature: float,
    texts: List[str],
    tables: List[str],
    image_b64: List[str],
    image_refs: List[str],
) -> Tuple[List[str], List[str], List[str]]:
    """
    Summarize text chunks, tables, and images.
    Returns (text_summaries, table_summaries, image_summaries).
    """
    logger.info("Starting summarization stage.")
    logger.debug(
        f"Summarization inputs -> texts: {len(texts)}, tables: {len(tables)}, "
        f"images: {len(image_b64)}."
    )
    logger.debug(
        f"Using text model '{text_model_id}', vision model '{vision_model_id}', "
        f"temperature={model_temperature:.2f}, max_concurrency={MAX_CONCURRENCY}."
    )

    text_model = get_text_model(text_model_id, model_temperature)
    batch_cfg = {"max_concurrency": MAX_CONCURRENCY}

    prompt_text = load_prompt_from_json(prompt_file, "text_summarization")
    chain = prompt_text | text_model | StrOutputParser()

    if texts:
        logger.info(f"Summarizing {len(texts)} text chunk(s).")
        text_summaries = chain.batch([{"content": t} for t in texts], batch_cfg)
        logger.debug(f"Generated {len(text_summaries)} text summary(ies).")
    else:
        logger.debug("No text chunks to summarize.")
        text_summaries = []

    prompt_table = load_prompt_from_json(prompt_file, "table_summarization")
    chain = prompt_table | text_model | StrOutputParser()

    if tables:
        logger.info(f"Summarizing {len(tables)} standalone table(s).")
        table_summaries = chain.batch([{"content": t} for t in tables], batch_cfg)
        logger.debug(f"Generated {len(table_summaries)} table summary(ies).")
    else:
        logger.debug("No standalone tables to summarize.")
        table_summaries = []

    image_summaries = (
        _summarize_images_with_vision(vision_model_id, image_b64, image_refs, prompt_file)
        if image_b64
        else []
    )

    logger.info(
        "Summarization stage finished "
        f"(text={len(text_summaries)}, tables={len(table_summaries)}, "
        f"images={len(image_summaries)})."
    )
    return text_summaries, table_summaries, image_summaries


# ---------------------------------------------------------------------------
# Redis repopulation helper
# ---------------------------------------------------------------------------
def _repopulate_docstore_from_cache(
    redis_url: str,
    namespace: str,
    texts: List[str],
    tables: List[str],
    image_refs: List[str],
    image_summaries: List[str],
) -> RedisStore:
    """
    Rebuild the Redis docstore from cached summaries without re-invoking any LLM.
    Called on cache hits where Redis was emptied after the initial indexing run.
    """
    logger.info("Repopulating Redis docstore from cache.")
    logger.debug(
        f"Redis URL: {redis_url} | namespace: {namespace} | "
        f"texts={len(texts)}, tables={len(tables)}, "
        f"image_summaries={len(image_summaries)}."
    )

    store = RedisStore(redis_url=redis_url)
    items: List[Tuple[str, str]] = []

    for i, text in enumerate(texts):
        items.append((_make_doc_id(namespace, "text", i), text))

    for i, table in enumerate(tables):
        items.append((_make_doc_id(namespace, "table", i), table))

    valid_image_items = [
        (image_refs[i] if i < len(image_refs) else "", image_summaries[i], i)
        for i in range(len(image_summaries))
        if _is_valid_image_summary(image_summaries[i])
    ]

    for ref, summary, original_idx in valid_image_items:
        payload = f"[IMAGE_EXTRACT]\nSOURCE_REF: {ref}\n\n{summary}\n"
        items.append((_make_doc_id(namespace, "image", original_idx), payload))

    if items:
        store.mset(items)
        logger.info(f"Inserted {len(items)} item(s) into Redis docstore.")
    else:
        logger.warn("No cached items found to repopulate Redis docstore.")

    return store


# ---------------------------------------------------------------------------
# Embedding and storage
# ---------------------------------------------------------------------------
def _embedding(
    embedding_model_id: str,
    redis_url: str,
    namespace: str,
    chroma_index: str,
    collection_name: str,
    texts: List[str],
    text_summaries: List[str],
    tables: List[str],
    table_summaries: List[str],
    image_refs: List[str],
    image_summaries: List[str],
) -> MultiVectorRetriever:
    """
    Embed summaries into Chroma and store original chunks in Redis.
    Returns the configured MultiVectorRetriever.
    """
    logger.info("Starting embedding and storage stage.")
    logger.debug(
        f"Embedding model='{embedding_model_id}', redis_url='{redis_url}', "
        f"collection='{collection_name}', chroma_index='{chroma_index}'."
    )

    embedding_function = get_embedding_function(embedding_model_id)

    vectorstore = Chroma(
        collection_name=collection_name,
        embedding_function=embedding_function,
        persist_directory=chroma_index,
    )

    store = RedisStore(redis_url=redis_url)
    id_key = "doc_id"

    retriever = MultiVectorRetriever(
        vectorstore=vectorstore,
        docstore=store,
        id_key=id_key,
    )

    if text_summaries:
        logger.info(f"Indexing {len(text_summaries)} text summary vector(s).")
        summary_docs = [
            Document(
                page_content=summary,
                metadata={id_key: _make_doc_id(namespace, "text", i)},
            )
            for i, summary in enumerate(text_summaries)
        ]
        retriever.vectorstore.add_documents(summary_docs)
        retriever.docstore.mset(
            [(_make_doc_id(namespace, "text", i), text) for i, text in enumerate(texts)]
        )
        logger.debug(f"Stored {len(texts)} original text chunk(s) in Redis.")
    else:
        logger.debug("No text summaries to index.")

    if table_summaries:
        logger.info(f"Indexing {len(table_summaries)} table summary vector(s).")
        summary_docs = [
            Document(
                page_content=summary,
                metadata={id_key: _make_doc_id(namespace, "table", i)},
            )
            for i, summary in enumerate(table_summaries)
        ]
        retriever.vectorstore.add_documents(summary_docs)
        retriever.docstore.mset(
            [(_make_doc_id(namespace, "table", i), table) for i, table in enumerate(tables)]
        )
        logger.debug(f"Stored {len(tables)} original table(s) in Redis.")
    else:
        logger.debug("No table summaries to index.")

    valid_image_items = [
        (image_refs[i] if i < len(image_refs) else "", image_summaries[i], i)
        for i in range(len(image_summaries))
        if _is_valid_image_summary(image_summaries[i])
    ]

    if valid_image_items:
        logger.info(f"Indexing {len(valid_image_items)} valid image summary vector(s).")
        summary_docs = [
            Document(
                page_content=summary,
                metadata={id_key: _make_doc_id(namespace, "image", original_idx)},
            )
            for _, summary, original_idx in valid_image_items
        ]
        retriever.vectorstore.add_documents(summary_docs)

        originals = [
            (
                _make_doc_id(namespace, "image", original_idx),
                f"[IMAGE_EXTRACT]\nSOURCE_REF: {ref}\n\n{summary}\n",
            )
            for ref, summary, original_idx in valid_image_items
        ]
        retriever.docstore.mset(originals)
        logger.debug(f"Stored {len(originals)} original image payload(s) in Redis.")
    else:
        logger.debug("No valid image summaries to index.")

    logger.info("Embedding and storage stage finished.")
    return retriever


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _save_cache(
    file_path: str,
    texts: List[str],
    tables: List[str],
    image_refs: List[str],
    text_summaries: List[str],
    table_summaries: List[str],
    image_summaries: List[str],
):
    logger.debug(f"Saving cache to '{file_path}'.")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    payload = {
        "version": 2,
        "texts": texts,
        "tables": tables,
        "image_refs": image_refs,
        "text_summaries": text_summaries,
        "table_summaries": table_summaries,
        "image_summaries": image_summaries,
    }

    with open(file_path, "wb") as f:
        pickle.dump(payload, f)

    logger.info(
        f"Cache saved: texts={len(texts)}, tables={len(tables)}, "
        f"image_refs={len(image_refs)}, image_summaries={len(image_summaries)}."
    )


def _load_cache(file_path: str) -> Dict[str, Any]:
    logger.debug(f"Loading cache from '{file_path}'.")
    with open(file_path, "rb") as f:
        data = pickle.load(f)

    if isinstance(data, tuple):
        logger.warn("Legacy tuple-based cache format detected. Converting on load.")
        texts, tables, image_refs, text_summaries, table_summaries, image_summaries = data
        return {
            "version": 1,
            "texts": texts,
            "tables": tables,
            "image_refs": image_refs,
            "text_summaries": text_summaries,
            "table_summaries": table_summaries,
            "image_summaries": image_summaries,
        }

    logger.debug("Cache loaded successfully.")
    return data


def _clear_cache(file_path: str):
    if os.path.exists(file_path):
        logger.debug(f"Removing existing cache file '{file_path}'.")
        os.remove(file_path)
    else:
        logger.debug(f"No cache file found at '{file_path}'.")


def _clear_chroma_index(chroma_index: str):
    if os.path.exists(chroma_index):
        logger.debug(f"Removing existing Chroma index directory '{chroma_index}'.")
        shutil.rmtree(chroma_index)
    else:
        logger.debug(f"No Chroma index directory found at '{chroma_index}'.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_retriever(
    file: str,
    force_process: bool,
    html_version: str,
    text_model_id: str = DEFAULT_TEXT_MODEL_ID,
    vision_model_id: str = DEFAULT_VISION_MODEL_ID,
    embedding_model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
    model_temperature: float = DEFAULT_TEMPERATURE,
    prompt_file: str = PROMPT_FILE_DEFAULT,
) -> MultiVectorRetriever:
    """
    Build or restore a MultiVectorRetriever for the given HTML guide.

    On the first call (or with force_process=True), the HTML is split,
    summarized, embedded, and cached. On subsequent calls, the cached
    summaries and Chroma index are reused, with Redis repopulated if needed.

    Parameters
    ----------
    file            : Path to the RINF Application Guide HTML file.
    force_process   : If True, ignore existing cache and rebuild everything.
    html_version    : '3.2.1' (native HTML) or '1.6.1' (PDF-converted HTML).
    text_model_id   : Model used for text and table summarization.
    vision_model_id : Model used for image description.
    embedding_model_id : Embedding model for Chroma indexing.
    model_temperature  : Sampling temperature for summarization.
    prompt_file     : Path to the RAG prompts JSON file.

    Returns
    -------
    MultiVectorRetriever connected to Chroma (vectors) and Redis (originals).
    """
    logger.info("Initializing RAG retriever.")
    logger.debug(
        f"file='{file}', force_process={force_process}, html_version='{html_version}', "
        f"temperature={model_temperature:.2f}, text_model='{text_model_id}', "
        f"vision_model='{vision_model_id}', embedding_model='{embedding_model_id}', "
        f"redis_url='{REDIS_URL}', prompt_file='{prompt_file}'."
    )

    base = os.path.splitext(os.path.basename(file))[0]
    base = re.sub(r"[^a-z0-9-]", "-", base.lower())

    model_tag = _safe_tag(text_model_id)
    namespace = f"{base}_{model_tag}"
    cache_file = os.path.join(DEFAULT_CACHE_ROOT, f"{namespace}.pkl")
    chroma_index = os.path.join(DEFAULT_CHROMA_ROOT, namespace)
    collection_name = _make_collection_name(namespace)

    logger.debug(
        f"Derived namespace='{namespace}', cache_file='{cache_file}', "
        f"chroma_index='{chroma_index}', collection_name='{collection_name}'."
    )

    if not force_process and os.path.exists(cache_file) and os.path.exists(chroma_index):
        logger.info("Loading retriever from existing cache and persistent vectorstore.")

        vectorstore = Chroma(
            collection_name=collection_name,
            embedding_function=get_embedding_function(embedding_model_id),
            persist_directory=chroma_index,
        )

        try:
            collection_size = vectorstore._collection.count()
        except Exception as e:
            logger.warn(f"Could not read Chroma collection size: {e}")
            collection_size = 0

        if collection_size == 0:
            logger.warn(
                f"Chroma index directory exists but collection '{collection_name}' "
                f"is empty. Forcing full reprocess of cache and vectorstore."
            )
            _clear_cache(cache_file)
            _clear_chroma_index(chroma_index)
        else:
            logger.debug(
                f"Chroma collection '{collection_name}' has {collection_size} documents."
            )

            cache = _load_cache(cache_file)
            texts = cache["texts"]
            tables = cache["tables"]
            image_refs = cache["image_refs"]
            image_summaries = cache["image_summaries"]

            logger.debug(
                f"Cached data -> texts={len(texts)}, tables={len(tables)}, "
                f"image_refs={len(image_refs)}, image_summaries={len(image_summaries)}."
            )

            store = _repopulate_docstore_from_cache(
                redis_url=REDIS_URL,
                namespace=namespace,
                texts=texts,
                tables=tables,
                image_refs=image_refs,
                image_summaries=image_summaries,
            )

            logger.info("Retriever loaded from cache successfully.")
            return MultiVectorRetriever(
                vectorstore=vectorstore,
                docstore=store,
                id_key="doc_id",
            )

    logger.info("Processing source HTML and rebuilding retriever artifacts.")
    _clear_cache(cache_file)
    _clear_chroma_index(chroma_index)

    if html_version == "3.2.1":
        logger.info("Using preprocess_html.split_html() for HTML version 3.2.1.")
        texts, tables, image_refs, image_b64 = split_html(
            file_path=file,
            cache_root=DEFAULT_CACHE_ROOT,
        )
    elif html_version == "1.6.1":
        logger.info("Using preprocess_html_from_pdf.split_html_from_pdf() for HTML version 1.6.1.")
        texts, tables, image_refs, image_b64 = split_html_from_pdf(
            file_path=file,
            cache_root=DEFAULT_CACHE_ROOT,
        )
    else:
        raise ValueError(
            f"Unsupported html_version '{html_version}'. Expected '3.2.1' or '1.6.1'."
        )

    logger.info(
        f"Split HTML into {len(texts)} text chunk(s), {len(tables)} table(s), "
        f"{len(image_refs)} image ref(s), and {len(image_b64)} raw image payload(s)."
    )

    text_summaries, table_summaries, image_summaries = _summarize(
        prompt_file=prompt_file,
        text_model_id=text_model_id,
        vision_model_id=vision_model_id,
        model_temperature=model_temperature,
        texts=texts,
        tables=tables,
        image_b64=image_b64,
        image_refs=image_refs,
    )

    _save_cache(
        cache_file,
        texts,
        tables,
        image_refs,
        text_summaries,
        table_summaries,
        image_summaries,
    )

    retriever = _embedding(
        embedding_model_id=embedding_model_id,
        redis_url=REDIS_URL,
        namespace=namespace,
        chroma_index=chroma_index,
        collection_name=collection_name,
        texts=texts,
        text_summaries=text_summaries,
        tables=tables,
        table_summaries=table_summaries,
        image_refs=image_refs,
        image_summaries=image_summaries,
    )

    logger.info("Data processed and stored successfully.")
    return retriever