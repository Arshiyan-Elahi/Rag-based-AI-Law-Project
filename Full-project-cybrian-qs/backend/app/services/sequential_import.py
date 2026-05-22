"""
Ultra-fast reading-order extraction for SOP upload (PDF / DOCX / TXT).

Returns unified chronological elements for instant UI rendering; converts to
legacy typed blocks for metadata heuristics and TipTap mapping.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from .pdf_extractor import (
    _clean_line,
    _is_key_value_line,
    _is_likely_heading,
    _pdf_is_scanned,
    sanitize_extracted_text,
)
from .sop_metadata_extractor import strip_invalid_control_chars

logger = logging.getLogger(__name__)

EXTRACTION_ENGINE_SEQUENTIAL = "sequential"


def elements_to_plain_text(elements: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        if el.get("type") == "text":
            text = str(el.get("content") or "").strip()
            if text:
                parts.append(text)
        elif el.get("type") == "table":
            for row in el.get("content") or []:
                cells = [str(c).strip() for c in row or [] if str(c).strip()]
                if cells:
                    parts.append(" | ".join(cells))
    return "\n\n".join(parts).strip()


def elements_to_blocks(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map unified elements → typed blocks used by refine_blocks / TipTap builder."""
    blocks: List[Dict[str, Any]] = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        etype = str(el.get("type") or "").lower()
        if etype == "text":
            text = _clean_line(str(el.get("content") or ""))
            if not text:
                continue
            style = str(el.get("style") or "paragraph").lower()
            if style == "heading" or _is_likely_heading(text):
                level = 2 if _is_likely_heading(text) else 2
                block_type = "section_heading" if level <= 2 else "heading"
                blocks.append({"type": block_type, "text": text, "level": min(3, level)})
            elif _is_key_value_line(text):
                key, _, value = text.partition(":")
                blocks.append(
                    {
                        "type": "two_column_row",
                        "left": _clean_line(key),
                        "right": _clean_line(value),
                    }
                )
            else:
                blocks.append({"type": "paragraph", "text": text})
        elif etype == "table":
            rows = el.get("content") or []
            if isinstance(rows, list) and rows:
                blocks.append({"type": "table", "rows": rows})
    return blocks


def extract_sequential_upload(
    raw: bytes,
    filename: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str, bool]:
    """
    Extract file in strict reading order (no chunking, embeddings, or DB).

    Returns:
        elements — chronological API JSON for frontend instant load
        blocks   — legacy typed blocks for metadata + TipTap
        text     — flattened plain text
        scanned_pdf — True when OCR/scanned path was used
    """
    name = (filename or "").lower()
    elements: List[Dict[str, Any]] = []

    if name.endswith(".pdf"):
        from .pdf_extractor import extract_sequential_elements

        scanned = _pdf_is_scanned(raw)
        elements = extract_sequential_elements(raw)
        blocks = elements_to_blocks(elements)
        from .document_structure import refine_blocks

        text = sanitize_extracted_text(elements_to_plain_text(elements))
        blocks = refine_blocks(blocks, text)
        return elements, blocks, strip_invalid_control_chars(text), scanned

    if name.endswith(".docx"):
        from .docx_extractor import extract_docx_elements

        elements = extract_docx_elements(raw)
        blocks = elements_to_blocks(elements)
        from .document_structure import refine_blocks

        text = sanitize_extracted_text(elements_to_plain_text(elements))
        blocks = refine_blocks(blocks, text)
        return elements, blocks, strip_invalid_control_chars(text), False

    if name.endswith(".txt"):
        text = strip_invalid_control_chars(raw.decode("utf-8", errors="replace"))
        elements = []
        for raw_line in text.splitlines():
            line = _clean_line(raw_line)
            if not line:
                continue
            style = "heading" if _is_likely_heading(line) else "paragraph"
            elements.append({"type": "text", "style": style, "content": line})
        blocks = elements_to_blocks(elements)
        from .document_structure import refine_blocks

        blocks = refine_blocks(blocks, text)
        return elements, blocks, text, False

    raise ValueError("Unsupported file type")
