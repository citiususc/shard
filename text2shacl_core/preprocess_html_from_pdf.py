"""
preprocess_html_from_pdf.py

HTML preprocessing and splitting for the RINF Application Guide v1.6.1
that was converted from PDF to HTML by an online tool.

Unlike the v3.2.1 HTML (handled by preprocess_html.py), this version:
  - Has NO <div class="entity"> blocks — content is a flat sequence of <p>
    tags with style classes (s1, s2, ..., sN) describing fonts, colors and
    sizes. We must rely on a chunker like unstructured.partition_html.
  - Embeds ALL images as base64 data URIs inside <img> tags
    (no companion _files/ directory, no external image references).
  - Carries large amounts of inline CSS, font/color metadata and decorative
    1x1 spacer images that add no semantic value for the LLM.

Public API
----------
    split_html_from_pdf(file_path) -> Tuple[texts, tables, image_refs, image_b64]

Pipeline
--------
    1. _preprocess_html()        — load + strip CSS, scripts, inline style
                                   attributes, empty <p> tags, and decorative
                                   img tags. Writes a cleaned HTML to a temp
                                   file so unstructured can consume it.
    2. _extract_text_chunks()    — partition_html() splits the cleaned HTML
                                   into semantic chunks; consecutive text
                                   chunks under a minimum length are merged
                                   so the RAG retriever indexes meaningful
                                   passages.
    3. _extract_tables()         — partition_html() returns Table elements
                                   with HTML serialisation in metadata.
    4. _extract_base64_images()  — base64 images embedded in <img src="data:...">.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from io import BytesIO
from typing import List, Tuple

from bs4 import BeautifulSoup
from PIL import Image
from unstructured.partition.html import partition_html

from Logger import logger


# ---------------------------------------------------------------------------
# Paths — anchored to project root so the module works regardless of the
# working directory from which the process is launched.
# ---------------------------------------------------------------------------
_PROJECT_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CACHE_ROOT = os.path.join(_PROJECT_ROOT, "cache", "processing_cache")


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

_MIN_IMAGE_SIZE_PX = 64
_MIN_IMAGE_BYTES   = 2048
_MIN_CHUNK_LENGTH  = 50
_MAX_CHUNK_LENGTH  = 2000


# ---------------------------------------------------------------------------
# Step 1 — Clean the HTML before partitioning
# ---------------------------------------------------------------------------

def _preprocess_html(file_path: str) -> str:
    """
    Read the source HTML, strip noise that would confuse partition_html
    or bloat the LLM context, and write the cleaned content to a temp file.

    Returns the path to the cleaned file.

    Removed:
      - <style> and <script> blocks
      - All HTML attributes except 'src' (on <img>) and 'href' (on <a>)
      - Empty <p> tags and tags containing only whitespace / <br/>
      - Decorative <img> tags pointing at very small base64 payloads
    """
    logger.info(f"Preprocessing PDF-converted HTML file: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        html = f.read()

    logger.debug(f"Loaded HTML content ({len(html)} chars).")

    soup = BeautifulSoup(html, "html.parser")

    style_script_tags = soup.find_all(["style", "script"])
    logger.debug(f"Removing {len(style_script_tags)} <style>/<script> tag(s).")
    for tag in style_script_tags:
        tag.decompose()

    keep_attrs = {
        "img":   {"src", "width", "height"},
        "a":     {"href"},
        "table": {"border"},
    }
    stripped = 0
    for tag in soup.find_all(True):
        allowed = keep_attrs.get(tag.name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
                stripped += 1
    logger.debug(f"Stripped {stripped} HTML attribute(s).")

    decorative_imgs = 0
    for img in soup.find_all("img"):
        try:
            w = int(img.get("width", "0"))
            h = int(img.get("height", "0"))
        except ValueError:
            w = h = 0
        if 0 < w < _MIN_IMAGE_SIZE_PX and 0 < h < _MIN_IMAGE_SIZE_PX:
            img.decompose()
            decorative_imgs += 1
    logger.debug(f"Removed {decorative_imgs} decorative <img> tag(s).")

    empty_p = 0
    for p in soup.find_all("p"):
        text    = p.get_text(strip=True)
        has_img = p.find("img") is not None
        if not text and not has_img:
            p.decompose()
            empty_p += 1
    logger.debug(f"Removed {empty_p} empty <p> tag(s).")

    cleaned_path = os.path.join(
        tempfile.gettempdir(),
        f"rinf_cleaned_{os.getpid()}.html",
    )
    with open(cleaned_path, "w", encoding="utf-8") as f:
        f.write(str(soup))

    logger.info(f"Cleaned HTML written to temp file: {cleaned_path}")
    return cleaned_path


# ---------------------------------------------------------------------------
# Step 2 — Extract text chunks via unstructured + merging pass
# ---------------------------------------------------------------------------

def _extract_text_chunks(cleaned_path: str) -> Tuple[List[str], List[str]]:
    """
    Partition the cleaned HTML with unstructured and split into:
      - texts: list of textual chunks (paragraphs, headers, lists)
      - tables: list of HTML strings for each detected table

    Short consecutive text chunks are merged so each entry passed to the
    embedding step contains enough context for the retriever to be useful.
    """
    logger.info("Partitioning cleaned HTML with unstructured.partition_html.")

    chunks = partition_html(filename=cleaned_path, skip_headers_and_footers=True)
    logger.debug(f"partition_html returned {len(chunks)} raw element(s).")

    raw_texts: List[str] = []
    tables:    List[str] = []

    for ch in chunks:
        category = getattr(ch, "category", "") or ""
        if "Table" in category:
            table_html = getattr(getattr(ch, "metadata", None), "text_as_html", None)
            if table_html:
                tables.append(table_html)
        else:
            text = getattr(ch, "text", None)
            if text and text.strip():
                raw_texts.append(text.strip())

    logger.debug(
        f"Pre-merge: {len(raw_texts)} text element(s), {len(tables)} table(s)."
    )

    texts:  List[str] = []
    buffer: str       = ""

    for chunk in raw_texts:
        if len(buffer) == 0:
            buffer = chunk
            continue

        if len(buffer) < _MIN_CHUNK_LENGTH:
            buffer = f"{buffer}\n{chunk}"
        elif len(buffer) + len(chunk) + 1 > _MAX_CHUNK_LENGTH:
            texts.append(buffer)
            buffer = chunk
        else:
            buffer = f"{buffer}\n{chunk}"

    if buffer:
        texts.append(buffer)

    texts = [t for t in texts if len(t) >= _MIN_CHUNK_LENGTH]

    logger.info(
        f"Extracted {len(texts)} merged text chunk(s) and {len(tables)} table(s)."
    )
    return texts, tables


# ---------------------------------------------------------------------------
# Step 3 — Extract base64-embedded images
# ---------------------------------------------------------------------------

def _extract_base64_images(html_path: str) -> List[str]:
    """
    Extract base64 strings from <img src="data:image/...;base64,..."> tags.
    Filters out tiny payloads (likely decorative) using both byte size and
    declared image dimensions.
    """
    logger.info("Extracting base64-embedded images from PDF-converted HTML.")

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    base64_images: List[str] = []

    img_tags = soup.find_all("img")
    logger.debug(f"Found {len(img_tags)} total <img> tag(s).")

    skipped_small_dims  = 0
    skipped_small_bytes = 0
    failed_extracts     = 0

    for img in img_tags:
        src = img.get("src", "")
        if not src.startswith("data:image/") or "base64," not in src:
            continue

        try:
            w = int(img.get("width", "0"))
            h = int(img.get("height", "0"))
        except ValueError:
            w = h = 0

        if 0 < w < _MIN_IMAGE_SIZE_PX and 0 < h < _MIN_IMAGE_SIZE_PX:
            skipped_small_dims += 1
            continue

        try:
            b64 = src.split("base64,", 1)[1]
        except Exception:
            failed_extracts += 1
            logger.warn("Failed to extract base64 payload from an <img> tag.")
            continue

        try:
            decoded_size = len(base64.b64decode(b64))
        except Exception:
            failed_extracts += 1
            continue

        if decoded_size < _MIN_IMAGE_BYTES:
            skipped_small_bytes += 1
            continue

        base64_images.append(b64)

    logger.info(f"Extracted {len(base64_images)} base64 image(s).")
    logger.debug(
        "Image scan summary: "
        f"skipped_small_dims={skipped_small_dims}, "
        f"skipped_small_bytes={skipped_small_bytes}, "
        f"failed_extracts={failed_extracts}"
    )

    return base64_images


def _save_base64_images(file_path: str, image_b64: List[str], cache_root: str) -> List[str]:
    """
    Decode base64 image strings, save them to disk as PNG files, and return
    their local filesystem paths.
    """
    logger.info(f"Saving {len(image_b64)} base64 image(s) to cache.")
    base = os.path.splitext(os.path.basename(file_path))[0]
    base = re.sub(r"[^a-z0-9-]", "-", base.lower())

    images_dir = os.path.join(cache_root, "extracted_images", base)
    os.makedirs(images_dir, exist_ok=True)
    logger.debug(f"Image cache directory: {images_dir}")

    image_refs: List[str] = []
    failed_saves = 0

    for i, b64 in enumerate(image_b64):
        try:
            img_bytes = base64.b64decode(b64)
            img       = Image.open(BytesIO(img_bytes)).convert("RGB")
            img_path  = os.path.join(images_dir, f"image_{i:04d}.png")
            img.save(img_path, format="PNG")
            image_refs.append(img_path)
            logger.debug(f"Saved base64 image {i + 1}/{len(image_b64)} -> {img_path}")
        except Exception as e:
            image_refs.append("")
            failed_saves += 1
            logger.warn(
                f"Failed to decode/save base64 image {i + 1}/{len(image_b64)}: {e}"
            )

    logger.info(
        f"Saved {len(image_b64) - failed_saves}/{len(image_b64)} base64 image(s) successfully."
    )
    return image_refs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def split_html_from_pdf(
    file_path: str,
    cache_root: str = None,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """
    Full preprocessing and splitting pipeline for the RINF Application Guide
    v1.6.1 HTML (converted from the original PDF).

    Steps:
      1. Clean the HTML (remove styles, scripts, decorative images, etc.)
      2. Partition the cleaned HTML with unstructured.partition_html and
         merge short text chunks into useful retrieval passages.
      3. Extract standalone tables produced by partition_html.
      4. Extract base64-embedded images, filtering out decorative spacers.

    Parameters
    ----------
    file_path  : path to the source HTML file
    cache_root : root directory for caching extracted images and temp files.
                 Defaults to cache/processing_cache/ under the project root.

    Returns
    -------
    texts      : list[str]  — merged textual chunks
    tables     : list[str]  — HTML string per detected table
    image_refs : list[str]  — local filesystem paths to saved images
    image_b64  : list[str]  — base64-encoded strings for the vision model

    The return signature matches preprocess_html.split_html() so this module
    is a drop-in replacement when handling the v1.6.1 HTML.
    """
    if cache_root is None:
        cache_root = _DEFAULT_CACHE_ROOT

    logger.info(f"Starting HTML split pipeline (v1.6.1 / from-PDF) for: {file_path}")
    logger.debug(f"Using cache root: {cache_root}")

    cleaned_path = _preprocess_html(file_path)

    try:
        texts, tables = _extract_text_chunks(cleaned_path)

        image_b64  = _extract_base64_images(file_path)
        image_refs = _save_base64_images(file_path, image_b64, cache_root)

    finally:
        try:
            os.remove(cleaned_path)
            logger.debug(f"Removed temp cleaned HTML file: {cleaned_path}")
        except OSError:
            pass

    logger.info(
        f"[split_html_from_pdf] {len(texts)} text chunks | "
        f"{len(tables)} tables | "
        f"{len(image_b64)} embedded images"
    )
    logger.debug(
        f"Final split results -> text_chunks={len(texts)}, tables={len(tables)}, "
        f"image_refs={len(image_refs)}, image_b64={len(image_b64)}"
    )

    return texts, tables, image_refs, image_b64