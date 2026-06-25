"""
preprocess_html.py

HTML preprocessing and splitting for the RINF Application Guide v3.2.1.

This module is specific to the ERA/RINF HTML format, which structures all
ontology properties and classes as <div class="entity"> blocks.

Public API
----------
    split_html(file_path) -> Tuple[texts, tables, image_refs, image_b64]

Pipeline
--------
    1. _preprocess_html()          — parse + remove JS/CSS/nav noise
    2. _extract_entity_blocks()    — one chunk per OWL property / class
    3. _extract_standalone_tables() — tables outside entity blocks
    4. _extract_base64_images()    — images embedded as base64 in the HTML
    5. _extract_external_images()  — images referenced as external files
                                     (e.g. ERA-ontology-rinf-overview.png)
"""

from __future__ import annotations

import base64
import os
import re
import shutil
from io import BytesIO
from typing import List, Tuple

from bs4 import BeautifulSoup
from PIL import Image

from Logger import logger


# ---------------------------------------------------------------------------
# Paths — anchored to project root so the module works regardless of the
# working directory from which the process is launched.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CACHE_ROOT = os.path.join(_PROJECT_ROOT, "cache", "processing_cache")


# ---------------------------------------------------------------------------
# IDs / class names to remove during preprocessing
# ---------------------------------------------------------------------------

_REMOVE_IDS = {
    "toc",
    "treeSection",
    "fixed-search-container",
    "legend",
}

_REMOVE_CLASSES = {
    "head",
    "status",
    "darkmode",
}

_SKIP_IMAGE_FILENAMES = {
    "era-logo.png",
    "era-logo.svg",
    "favicon.png",
    "favicon.ico",
}

_COMPANION_SUBPATHS = [
    "",
    "_files",
    "static/img",
    "static",
]


# ---------------------------------------------------------------------------
# Step 1 — Parse and clean the HTML
# ---------------------------------------------------------------------------

def _preprocess_html(file_path: str) -> BeautifulSoup:
    """
    Parse the HTML file and remove all non-semantic content:
      - <script> and <style> tags
      - Navigation-only sections (TOC, tree hierarchy, header, status bar)
      - The legend block (contains a spurious div.entity)

    Returns a cleaned BeautifulSoup tree ready for content extraction.
    """
    logger.info(f"Preprocessing HTML file: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        html = f.read()

    logger.debug(f"Loaded HTML content ({len(html)} chars).")

    soup = BeautifulSoup(html, "html.parser")

    removable_tags = soup.find_all(["script", "style"])
    logger.debug(f"Found {len(removable_tags)} <script>/<style> tag(s) to remove.")
    for tag in removable_tags:
        tag.decompose()

    removed_by_id = 0
    for section_id in _REMOVE_IDS:
        tag = soup.find(id=section_id)
        if tag:
            tag.decompose()
            removed_by_id += 1
            logger.debug(f"Removed section by id: {section_id}")

    removed_by_class = 0
    for css_class in _REMOVE_CLASSES:
        tags = soup.find_all(class_=css_class)
        for tag in tags:
            tag.decompose()
            removed_by_class += 1
        if tags:
            logger.debug(f"Removed {len(tags)} tag(s) with class '{css_class}'.")

    logger.debug(
        f"HTML preprocessing complete. Removed {len(removable_tags)} script/style tag(s), "
        f"{removed_by_id} id-based section(s), and {removed_by_class} class-based section(s)."
    )

    return soup


# ---------------------------------------------------------------------------
# Step 2 — Extract entity blocks (text chunks)
# ---------------------------------------------------------------------------

def _extract_entity_blocks(soup: BeautifulSoup) -> List[str]:
    """
    Extract one text chunk per <div class="entity"> block.

    Each block corresponds to one OWL property or class and contains
    its name, IRI, description, domain, data format, validation rules,
    examples and references.

    Blocks shorter than 50 characters are skipped (residual navigation
    artifacts or empty placeholders).
    """
    logger.info("Extracting entity blocks from HTML.")
    texts: List[str] = []

    entity_divs = soup.find_all("div", class_="entity")
    logger.debug(f"Found {len(entity_divs)} raw entity block(s).")

    skipped_short = 0
    for entity_div in entity_divs:
        text = entity_div.get_text(separator="\n", strip=True)
        if text and len(text) > 50:
            texts.append(text)
        else:
            skipped_short += 1

    logger.info(f"Extracted {len(texts)} valid entity chunk(s).")
    logger.debug(f"Skipped {skipped_short} short/empty entity block(s).")
    return texts


# ---------------------------------------------------------------------------
# Step 3 — Extract standalone tables
# ---------------------------------------------------------------------------

def _extract_standalone_tables(soup: BeautifulSoup) -> List[str]:
    """
    Extract HTML tables that are NOT inside a div.entity block.

    These are document-level tables (e.g. revision history, namespace prefix
    table) that provide useful background context for the RAG pipeline.
    Tables inside entity blocks are already captured by _extract_entity_blocks.
    """
    logger.info("Extracting standalone tables from HTML.")
    tables: List[str] = []

    all_tables = soup.find_all("table")
    logger.debug(f"Found {len(all_tables)} total table(s) in HTML.")

    skipped_embedded = 0
    for table in all_tables:
        if table.find_parent("div", class_="entity"):
            skipped_embedded += 1
            continue
        table_html = str(table)
        if table_html:
            tables.append(table_html)

    logger.info(f"Extracted {len(tables)} standalone table(s).")
    logger.debug(f"Skipped {skipped_embedded} table(s) embedded inside entity blocks.")
    return tables


# ---------------------------------------------------------------------------
# Step 4 — Extract base64-embedded images
# ---------------------------------------------------------------------------

def _extract_base64_images(html_path: str) -> List[str]:
    """
    Extract raw base64 strings from <img src="data:image/...;base64,..."> tags.
    Returns a list of base64-encoded strings (without the data URI prefix).
    """
    logger.info("Extracting base64-embedded images from HTML.")

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    base64_images: List[str] = []

    img_tags = soup.find_all("img")
    logger.debug(f"Found {len(img_tags)} total <img> tag(s) while scanning for base64 images.")

    failed_extracts = 0
    for img in img_tags:
        src = img.get("src", "")
        if src.startswith("data:image/") and "base64," in src:
            try:
                base64_images.append(src.split("base64,", 1)[1])
            except Exception:
                failed_extracts += 1
                logger.warn("Failed to extract one base64 image payload from an <img> tag.")

    logger.info(f"Extracted {len(base64_images)} base64 image(s).")
    if failed_extracts:
        logger.warn(f"Failed to parse {failed_extracts} base64 image(s).")

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
    logger.debug(f"Base64 image cache directory: {images_dir}")

    image_refs: List[str] = []
    failed_saves = 0

    for i, b64 in enumerate(image_b64):
        try:
            img_bytes = base64.b64decode(b64)
            img = Image.open(BytesIO(img_bytes)).convert("RGB")
            img_path = os.path.join(images_dir, f"image_{i:04d}.png")
            img.save(img_path, format="PNG")
            image_refs.append(img_path)
            logger.debug(f"Saved base64 image {i + 1}/{len(image_b64)} -> {img_path}")
        except Exception as e:
            image_refs.append("")
            failed_saves += 1
            logger.warn(f"Failed to decode/save base64 image {i + 1}/{len(image_b64)}: {e}")

    logger.info(
        f"Saved {len(image_b64) - failed_saves}/{len(image_b64)} base64 image(s) successfully."
    )
    return image_refs


# ---------------------------------------------------------------------------
# Step 5 — Extract external image references
# ---------------------------------------------------------------------------

def _resolve_external_image(src: str, html_path: str) -> str | None:
    """
    Try to resolve a relative image src to an absolute local filesystem path.

    Searches in:
      - Same directory as the HTML file
      - {html_basename}_files/  (browser "Save Page As" convention)
      - static/img/  and  static/  subdirectories
      - The raw relative path from html_dir
    """
    html_dir      = os.path.dirname(os.path.abspath(html_path))
    html_basename = os.path.splitext(os.path.basename(html_path))[0]
    filename      = os.path.basename(src.split("?")[0])

    search_dirs = [
        os.path.join(html_dir, subpath.replace("_files", f"{html_basename}_files"))
        if subpath == "_files"
        else os.path.join(html_dir, subpath)
        for subpath in _COMPANION_SUBPATHS
    ]

    logger.debug(f"Resolving external image '{src}' (filename='{filename}').")
    logger.debug(f"Search directories: {search_dirs}")

    for directory in search_dirs:
        candidate = os.path.join(directory, filename)
        if os.path.isfile(candidate):
            logger.debug(f"Resolved external image '{src}' -> '{candidate}'")
            return candidate

    relative = os.path.join(html_dir, src.lstrip("./\\"))
    if os.path.isfile(relative):
        logger.debug(f"Resolved external image '{src}' via raw relative path -> '{relative}'")
        return relative

    logger.debug(f"Could not resolve external image '{src}'.")
    return None


def _extract_external_images(
    html_path: str,
    images_dir: str,
) -> Tuple[List[str], List[str]]:
    """
    Extract external image files referenced in <img src="..."> tags.

    Skips base64 data URIs, logo/icon files, non-image extensions, and
    duplicates. Copies found images into images_dir for a consistent cache
    layout.

    Returns:
        image_refs: list of local paths where images are stored
        image_b64:  list of base64-encoded strings for the vision model
    """
    logger.info("Extracting external image references from HTML.")
    os.makedirs(images_dir, exist_ok=True)
    logger.debug(f"External image cache directory: {images_dir}")

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    image_refs: List[str] = []
    image_b64:  List[str] = []
    seen:       set        = set()

    valid_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

    img_tags = soup.find_all("img")
    logger.debug(f"Found {len(img_tags)} total <img> tag(s) while scanning for external images.")

    skipped_base64_or_empty = 0
    skipped_non_semantic    = 0
    skipped_duplicates      = 0
    skipped_non_image       = 0
    unresolved              = 0

    for img_tag in img_tags:
        src = img_tag.get("src", "")

        if not src or src.startswith("data:"):
            skipped_base64_or_empty += 1
            continue

        filename = os.path.basename(src.split("?")[0])

        if filename.lower() in _SKIP_IMAGE_FILENAMES:
            skipped_non_semantic += 1
            logger.debug(f"Skipping non-semantic image: {filename}")
            continue
        if filename in seen:
            skipped_duplicates += 1
            logger.debug(f"Skipping duplicate external image reference: {filename}")
            continue

        ext = os.path.splitext(filename)[1].lower()
        if ext not in valid_extensions:
            skipped_non_image += 1
            logger.debug(f"Skipping non-image external resource: {src}")
            continue

        resolved = _resolve_external_image(src, html_path)
        if resolved is None:
            unresolved += 1
            logger.warn(f"External image not found: {src}")
            continue

        seen.add(filename)

        dest_path = os.path.join(images_dir, f"ext_{filename}")
        if not os.path.exists(dest_path):
            shutil.copy2(resolved, dest_path)
            logger.debug(f"Copied external image '{filename}' to cache: {dest_path}")
        else:
            logger.debug(f"External image already cached: {dest_path}")

        with open(resolved, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        image_refs.append(dest_path)
        image_b64.append(b64)
        logger.info(f"External image found: {filename}")

    logger.info(f"Extracted {len(image_refs)} external image(s).")
    logger.debug(
        "External image scan summary: "
        f"skipped_base64_or_empty={skipped_base64_or_empty}, "
        f"skipped_non_semantic={skipped_non_semantic}, "
        f"skipped_duplicates={skipped_duplicates}, "
        f"skipped_non_image={skipped_non_image}, "
        f"unresolved={unresolved}"
    )

    return image_refs, image_b64


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def split_html(
    file_path: str,
    cache_root: str = None,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """
    Full preprocessing and splitting pipeline for the RINF Application Guide
    v3.2.1 HTML.

    Steps:
      1. Parse and clean the HTML (remove JS, CSS, nav sections)
      2. Extract one text chunk per div.entity (property / class block)
      3. Extract standalone tables (revision history, namespace prefixes)
      4. Extract base64-embedded images
      5. Extract external image files from the companion directory

    Parameters
    ----------
    file_path  : path to the HTML file
    cache_root : root directory for caching extracted images.
                 Defaults to cache/processing_cache/ under the project root.

    Returns
    -------
    texts      : list[str]  — one entry per entity block
    tables     : list[str]  — HTML string per standalone table
    image_refs : list[str]  — local filesystem paths to all images
    image_b64  : list[str]  — base64-encoded strings for the vision model
    """
    if cache_root is None:
        cache_root = _DEFAULT_CACHE_ROOT

    logger.info(f"Starting HTML split pipeline for file: {file_path}")
    logger.debug(f"Using cache root: {cache_root}")

    base = os.path.splitext(os.path.basename(file_path))[0]
    base = re.sub(r"[^a-z0-9-]", "-", base.lower())
    images_dir = os.path.join(cache_root, "extracted_images", base)

    logger.debug(f"Derived normalized base name: {base}")
    logger.debug(f"Image output directory: {images_dir}")

    logger.debug("Step 1/5: preprocessing HTML.")
    soup = _preprocess_html(file_path)

    logger.debug("Step 2/5: extracting entity blocks.")
    texts = _extract_entity_blocks(soup)

    logger.debug("Step 3/5: extracting standalone tables.")
    tables = _extract_standalone_tables(soup)

    logger.debug("Step 4/5: extracting and saving base64 images.")
    b64_raw  = _extract_base64_images(file_path)
    b64_refs = _save_base64_images(file_path, b64_raw, cache_root)

    logger.debug("Step 5/5: extracting external image files.")
    ext_refs, ext_b64 = _extract_external_images(file_path, images_dir)

    image_refs = b64_refs + ext_refs
    image_b64  = b64_raw  + ext_b64

    logger.info(
        f"[split_html] {len(texts)} entity chunks | "
        f"{len(tables)} tables | "
        f"{len(b64_raw)} embedded images | "
        f"{len(ext_b64)} external images"
    )
    logger.debug(
        f"Final split results -> text_chunks={len(texts)}, tables={len(tables)}, "
        f"image_refs={len(image_refs)}, image_b64={len(image_b64)}"
    )

    return texts, tables, image_refs, image_b64