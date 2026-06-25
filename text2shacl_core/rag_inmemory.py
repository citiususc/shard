"""
rag_inmemory.py  (br2shacl-ui)

Self-contained-lite RAG builder for the "upload a guide" mode (Mode B).

text2shacl/rag.py builds a MultiVectorRetriever backed by a *persistent* Chroma
index and a *Redis* docstore, with a pickle cache keyed by namespace. That needs
a running Redis server and writes to disk — at odds with a one-command demo.

This module reuses the real assets verbatim:
  * preprocess_html.split_html / preprocess_html_from_pdf.split_html_from_pdf
  * prompts/rag.json via prompts.load_prompt_from_json
  * model_loader.get_text_model / get_vision_backend / get_embedding_function
  * the exact summarization chains and vision flow from rag.py

…but stores summaries in an *ephemeral, in-memory* Chroma collection and the
original chunks in a LangChain InMemoryStore. No Redis, no persistence. The
returned object is the same MultiVectorRetriever the multi-agent pipeline expects,
so downstream code (_rag_agent) is unchanged.

Inference is routed entirely through model_loader, so passing Databricks endpoint
names keeps everything remote (no GPU required).
"""

from __future__ import annotations

import base64
import os
import uuid
from io import BytesIO
from typing import Callable, List, Optional, Tuple

from PIL import Image

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_chroma import Chroma

try:
    from langchain.storage import InMemoryStore
except Exception:  # pragma: no cover
    from langchain_core.stores import InMemoryStore

try:
    from langchain.retrievers.multi_vector import MultiVectorRetriever
except ModuleNotFoundError:
    from langchain_classic.retrievers.multi_vector import MultiVectorRetriever

from preprocess_html import split_html
from preprocess_html_from_pdf import split_html_from_pdf
from prompts import load_prompt_from_json
from model_loader import (
    get_text_model,
    get_vision_backend,
    get_embedding_function,
    internvl_preprocess,
    IMG_MAX_NEW_TOKENS,
)
from Logger import logger


_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE_DEFAULT = os.path.join(_PROJECT_ROOT, "prompts", "rag.json")

ID_KEY = "doc_id"

# Optional progress hook: called with (stage: str, current: int, total: int).
ProgressFn = Optional[Callable[[str, int, int], None]]


def _b64_to_pil(b64_str: str) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(b64_str))).convert("RGB")


def _is_valid_image_summary(summary: str) -> bool:
    return "[IMAGE_SUMMARY_ERROR]" not in summary


def _render_chatprompt_to_system_user(prompt_tpl, variables: dict) -> Tuple[str, str]:
    """Convert a ChatPromptTemplate into (system_text, user_text) — from rag.py."""
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


def _summarize_images_with_vision(
    vision_model_id: str,
    image_b64: List[str],
    image_refs: List[str],
    prompt_file: str,
    progress: ProgressFn = None,
) -> List[str]:
    """Describe each image with the configured vision model — faithful to rag.py."""
    if not image_b64:
        return []

    logger.info(f"Vision summarization for {len(image_b64)} image(s) via '{vision_model_id}'.")
    prompt_vl = load_prompt_from_json(prompt_file, "image_summarization_vl")
    backend = get_vision_backend(vision_model_id)
    model = backend["model"]
    tokenizer = backend["tokenizer"]

    generation_config = dict(max_new_tokens=IMG_MAX_NEW_TOKENS)
    outputs: List[str] = []

    for i, b64 in enumerate(image_b64):
        if progress:
            progress("images", i, len(image_b64))
        image_ref = image_refs[i] if i < len(image_refs) else ""
        try:
            img = _b64_to_pil(b64)
            system_text, user_text = _render_chatprompt_to_system_user(prompt_vl, {"image_ref": image_ref})
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
        except Exception as e:
            logger.warn(f"Vision summarization failed for image {i + 1}: {e}")
            outputs.append(
                "IMAGE_TYPE: OTHER\nTITLE:\n## DETAILED_DESCRIPTION\n"
                f"[IMAGE_SUMMARY_ERROR] {e}\n## OCR_TEXT\n\n## CONSTRAINT_HINTS\n- (none)\n"
            )
    return outputs


def _summarize(
    prompt_file: str,
    text_model_id: str,
    vision_model_id: str,
    temperature: float,
    texts: List[str],
    tables: List[str],
    image_b64: List[str],
    image_refs: List[str],
    progress: ProgressFn = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Summarize texts, tables and images — faithful to rag.py._summarize."""
    text_model = get_text_model(text_model_id, temperature)
    max_conc = int(os.environ.get("RAG_MAX_CONCURRENCY", "1"))
    batch_cfg = {"max_concurrency": max_conc}

    text_summaries: List[str] = []
    if texts:
        if progress:
            progress("texts", 0, len(texts))
        chain = load_prompt_from_json(prompt_file, "text_summarization") | text_model | StrOutputParser()
        text_summaries = chain.batch([{"content": t} for t in texts], batch_cfg)
        if progress:
            progress("texts", len(texts), len(texts))

    table_summaries: List[str] = []
    if tables:
        if progress:
            progress("tables", 0, len(tables))
        chain = load_prompt_from_json(prompt_file, "table_summarization") | text_model | StrOutputParser()
        table_summaries = chain.batch([{"content": t} for t in tables], batch_cfg)
        if progress:
            progress("tables", len(tables), len(tables))

    image_summaries = (
        _summarize_images_with_vision(vision_model_id, image_b64, image_refs, prompt_file, progress)
        if image_b64 else []
    )
    return text_summaries, table_summaries, image_summaries


def build_inmemory_retriever(
    file: str,
    html_version: str,
    text_model_id: str,
    vision_model_id: str,
    embedding_model_id: str,
    temperature: float = 0.5,
    prompt_file: str = PROMPT_FILE_DEFAULT,
    progress: ProgressFn = None,
) -> MultiVectorRetriever:
    """
    Split → summarize → embed (in-memory) and return a MultiVectorRetriever.

    Parameters mirror rag.load_retriever, minus the Redis/cache machinery.
    `progress(stage, current, total)` is called during summarization so the UI
    can show preprocessing progress before generation starts.
    """
    logger.info(f"[rag_inmemory] Building in-memory retriever for: {file} (v{html_version})")

    if html_version == "3.2.1":
        texts, tables, image_refs, image_b64 = split_html(file_path=file)
    elif html_version == "1.6.1":
        texts, tables, image_refs, image_b64 = split_html_from_pdf(file_path=file)
    else:
        raise ValueError(f"Unsupported html_version '{html_version}'. Expected '3.2.1' or '1.6.1'.")

    logger.info(
        f"[rag_inmemory] Split: {len(texts)} texts, {len(tables)} tables, {len(image_b64)} images."
    )

    text_summaries, table_summaries, image_summaries = _summarize(
        prompt_file, text_model_id, vision_model_id, temperature,
        texts, tables, image_b64, image_refs, progress,
    )

    vectorstore = Chroma(
        collection_name=f"br2shacl-{uuid.uuid4().hex[:8]}",
        embedding_function=get_embedding_function(embedding_model_id),
        # No persist_directory → ephemeral, in-memory collection.
    )
    docstore = InMemoryStore()
    retriever = MultiVectorRetriever(vectorstore=vectorstore, docstore=docstore, id_key=ID_KEY)

    def _index(originals: List[str], summaries: List[str], kind: str):
        docs, kv = [], []
        for i, summary in enumerate(summaries):
            if kind == "image" and not _is_valid_image_summary(summary):
                continue
            did = f"{kind}-{uuid.uuid4().hex}"
            docs.append(Document(page_content=summary, metadata={ID_KEY: did}))
            if kind == "image":
                ref = image_refs[i] if i < len(image_refs) else ""
                kv.append((did, f"[IMAGE_EXTRACT]\nSOURCE_REF: {ref}\n\n{summary}\n"))
            else:
                kv.append((did, originals[i]))
        if docs:
            retriever.vectorstore.add_documents(docs)
            retriever.docstore.mset(kv)

    _index(texts, text_summaries, "text")
    _index(tables, table_summaries, "table")
    _index(image_refs, image_summaries, "image")

    logger.info("[rag_inmemory] In-memory retriever ready.")
    return retriever
