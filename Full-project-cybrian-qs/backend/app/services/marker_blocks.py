"""
Convert local Marker PDF markdown output into normalized SOP extraction blocks.
Preserves headings, lists, tables, and page order (no plain-text flattening).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ..utils.table_blocks import normalize_table_rows
from .pdf_extractor import _flatten_blocks_text, sanitize_extracted_text

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_SETEXT_UNDERLINE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_BULLET_LINE = re.compile(r"^(\s*)[-*+]\s+(.+)$")
_NUMBERED_LINE = re.compile(r"^(\s*)\d+\.\s+(.+)$")
_IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_PAGE_BREAK = re.compile(r"^-{20,}$")
_PAGE_NUMBER_ONLY = re.compile(r"^\d{1,4}$")
_TABLE_SEP = re.compile(r"^\|?[\s:|-]+\|?$")


def _strip_md_inline(text: str) -> str:
    s = str(text or "").strip()
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s.strip()


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and stripped.count("|") >= 2


def _parse_table_rows(lines: List[str]) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in lines:
        stripped = line.strip().strip("|")
        if not stripped or _TABLE_SEP.match(line.strip()):
            continue
        cells = [_strip_md_inline(c) for c in stripped.split("|")]
        if any(cells):
            rows.append(cells)
    return normalize_table_rows(rows)


def _flush_paragraph(buffer: List[str], blocks: List[Dict[str, Any]]) -> None:
    if not buffer:
        return
    text = "\n".join(buffer).strip()
    buffer.clear()
    if text:
        blocks.append({"type": "paragraph", "text": text})


def _flush_list(
    items: List[str],
    *,
    ordered: bool,
    blocks: List[Dict[str, Any]],
) -> None:
    if not items:
        return
    typ = "numbered_list" if ordered else "bullet_list"
    blocks.append({"type": typ, "items": [_strip_md_inline(it) for it in items if str(it).strip()]})
    items.clear()


def markdown_to_extraction_blocks(markdown: str) -> Tuple[List[Dict[str, Any]], str]:
    raw = sanitize_extracted_text(markdown or "")
    if not raw.strip():
        return [], ""

    lines = raw.splitlines()
    blocks: List[Dict[str, Any]] = []
    para_buffer: List[str] = []
    bullet_items: List[str] = []
    numbered_items: List[str] = []
    table_buffer: List[str] = []
    i = 0

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        rows = _parse_table_rows(table_buffer)
        table_buffer = []
        if rows:
            blocks.append({"type": "table", "rows": rows})

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if table_buffer:
            if _is_table_row(line):
                table_buffer.append(line)
                i += 1
                continue
            flush_table()

        if not stripped:
            _flush_paragraph(para_buffer, blocks)
            _flush_list(bullet_items, ordered=False, blocks=blocks)
            _flush_list(numbered_items, ordered=True, blocks=blocks)
            i += 1
            continue

        if _PAGE_BREAK.match(stripped) or _PAGE_NUMBER_ONLY.match(stripped):
            i += 1
            continue

        img = _IMAGE_LINE.match(stripped)
        if img:
            _flush_paragraph(para_buffer, blocks)
            _flush_list(bullet_items, ordered=False, blocks=blocks)
            _flush_list(numbered_items, ordered=True, blocks=blocks)
            alt = _strip_md_inline(img.group(1)) or "Image"
            blocks.append({"type": "paragraph", "text": f"[{alt}]"})
            i += 1
            continue

        heading = _ATX_HEADING.match(stripped)
        if heading:
            _flush_paragraph(para_buffer, blocks)
            _flush_list(bullet_items, ordered=False, blocks=blocks)
            _flush_list(numbered_items, ordered=True, blocks=blocks)
            level = min(3, max(1, len(heading.group(1))))
            title = _strip_md_inline(heading.group(2))
            block_type = "section_heading" if level <= 2 else "heading"
            blocks.append({"type": block_type, "text": title, "level": level})
            i += 1
            continue

        if i + 1 < len(lines) and _SETEXT_UNDERLINE.match(lines[i + 1].strip()):
            _flush_paragraph(para_buffer, blocks)
            _flush_list(bullet_items, ordered=False, blocks=blocks)
            _flush_list(numbered_items, ordered=True, blocks=blocks)
            blocks.append({"type": "section_heading", "text": _strip_md_inline(stripped), "level": 1})
            i += 2
            continue

        if _is_table_row(line):
            _flush_paragraph(para_buffer, blocks)
            _flush_list(bullet_items, ordered=False, blocks=blocks)
            _flush_list(numbered_items, ordered=True, blocks=blocks)
            table_buffer = [line]
            i += 1
            while i < len(lines) and (_is_table_row(lines[i]) or not lines[i].strip()):
                if lines[i].strip():
                    table_buffer.append(lines[i])
                i += 1
            flush_table()
            continue

        bullet = _BULLET_LINE.match(line)
        if bullet:
            _flush_paragraph(para_buffer, blocks)
            _flush_list(numbered_items, ordered=True, blocks=blocks)
            bullet_items.append(bullet.group(2))
            i += 1
            continue

        numbered = _NUMBERED_LINE.match(line)
        if numbered:
            _flush_paragraph(para_buffer, blocks)
            _flush_list(bullet_items, ordered=False, blocks=blocks)
            numbered_items.append(numbered.group(2))
            i += 1
            continue

        _flush_list(bullet_items, ordered=False, blocks=blocks)
        _flush_list(numbered_items, ordered=True, blocks=blocks)
        para_buffer.append(_strip_md_inline(stripped))
        i += 1

    flush_table()
    _flush_paragraph(para_buffer, blocks)
    _flush_list(bullet_items, ordered=False, blocks=blocks)
    _flush_list(numbered_items, ordered=True, blocks=blocks)

    text = _flatten_blocks_text(blocks) if blocks else raw
    return blocks, text
