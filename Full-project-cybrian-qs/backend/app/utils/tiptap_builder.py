"""
Map SOP extraction blocks to TipTap JSON (StarterKit + table extensions).
Mirrors frontend src/utils/editorUtils.js mapBlocksToTipTapDoc.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from .table_blocks import (
    infer_header_row_count,
    normalize_table_rows,
    paragraph_text_looks_like_table,
    table_block_from_paragraph_text,
    table_block_from_rows,
)

logger = logging.getLogger(__name__)

_ALLOWED_BLOCK_TYPES = frozenset(
    {
        "paragraph",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "table",
        "tableRow",
        "tableCell",
        "tableHeader",
        "codeBlock",
        "blockquote",
        "horizontalRule",
    }
)


def _has_visible_text(value: Any) -> bool:
    return bool(str(value if value is not None else "").strip())


def _text_node(value: Any) -> dict | None:
    """TipTap rejects empty text nodes — never emit text: \"\" or whitespace-only."""
    text = str(value if value is not None else "")
    if not text.strip():
        return None
    return {"type": "text", "text": text}


def _strong_text_node(value: Any) -> dict | None:
    text = str(value if value is not None else "")
    if not text.strip():
        return None
    return {"type": "text", "text": text, "marks": [{"type": "bold"}]}


def _empty_paragraph() -> dict:
    return {"type": "paragraph"}


def _split_paragraph_lines(text: str = "") -> List[str]:
    return [line.strip() for line in re.split(r"\r?\n", str(text or "")) if line.strip()]


def _paragraph_node(text: str = "") -> dict:
    node = _text_node(str(text or "").strip())
    if node:
        return {"type": "paragraph", "content": [node]}
    return _empty_paragraph()


def _heading_node(text: str = "", level: int = 2) -> dict | None:
    node = _text_node(str(text or "").strip())
    if not node:
        return None
    lvl = min(3, max(1, int(level or 2)))
    return {"type": "heading", "attrs": {"level": lvl}, "content": [node]}


def _list_item_node(text: str = "") -> dict | None:
    para = _paragraph_node(str(text or "").strip())
    return {"type": "listItem", "content": [para]}


def _paragraph_from_multiline(text: str = "") -> dict:
    raw = str(text or "")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) <= 1:
        return _paragraph_node(raw.strip())
    content: List[dict] = []
    for idx, line in enumerate(lines):
        node = _text_node(line)
        if node:
            content.append(node)
        if idx < len(lines) - 1 and content and content[-1].get("type") == "text":
            content.append({"type": "hardBreak"})
    while content and content[-1].get("type") == "hardBreak":
        content.pop()
    if not content:
        return _empty_paragraph()
    return {"type": "paragraph", "content": content}


def _sanitize_inline_content(
    content: Any,
    removed: Dict[str, int],
) -> List[dict]:
    if not isinstance(content, list):
        return []
    out: List[dict] = []
    for node in content:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype == "text":
            text = str(node.get("text") if node.get("text") is not None else "")
            if not text.strip():
                removed["empty_text"] += 1
                continue
            cleaned: dict = {"type": "text", "text": text}
            marks = node.get("marks")
            if isinstance(marks, list) and marks:
                cleaned["marks"] = [
                    m for m in marks if isinstance(m, dict) and m.get("type")
                ]
            out.append(cleaned)
        elif ntype == "hardBreak":
            if out and out[-1].get("type") == "text":
                out.append({"type": "hardBreak"})
    while out and out[-1].get("type") == "hardBreak":
        out.pop()
    return out


def _sanitize_textblock(
    node: dict,
    *,
    default_type: str,
    removed: Dict[str, int],
) -> dict | None:
    ntype = node.get("type") if node.get("type") in ("paragraph", "heading") else default_type
    inline = _sanitize_inline_content(node.get("content"), removed)
    if not inline:
        removed["empty_paragraphs"] += 1
        if ntype == "heading":
            return None
        return _empty_paragraph()
    if ntype == "heading":
        level = min(6, max(1, int((node.get("attrs") or {}).get("level") or 2)))
        return {"type": "heading", "attrs": {"level": level}, "content": inline}
    return {"type": "paragraph", "content": inline}


def _sanitize_list_item(node: dict, removed: Dict[str, int]) -> dict | None:
    children = node.get("content") if isinstance(node.get("content"), list) else []
    paragraph = next(
        (c for c in children if isinstance(c, dict) and c.get("type") == "paragraph"),
        children[0] if children else None,
    )
    block = _sanitize_textblock(
        paragraph if isinstance(paragraph, dict) else {"type": "paragraph", "content": []},
        default_type="paragraph",
        removed=removed,
    )
    if not block:
        removed["empty_blocks"] += 1
        return None
    return {"type": "listItem", "content": [block]}


def _sanitize_table(node: dict, removed: Dict[str, int]) -> dict | None:
    rows_out: List[dict] = []
    for row in node.get("content") or []:
        if not isinstance(row, dict) or row.get("type") != "tableRow":
            continue
        cells_out: List[dict] = []
        for cell in row.get("content") or []:
            if not isinstance(cell, dict):
                continue
            cell_type = "tableHeader" if cell.get("type") == "tableHeader" else "tableCell"
            inner = cell.get("content") if isinstance(cell.get("content"), list) else []
            paragraph = next(
                (c for c in inner if isinstance(c, dict) and c.get("type") == "paragraph"),
                {"type": "paragraph", "content": []},
            )
            block = _sanitize_textblock(
                paragraph if isinstance(paragraph, dict) else {"type": "paragraph"},
                default_type="paragraph",
                removed=removed,
            )
            if not block:
                block = _empty_paragraph()
                removed["empty_table_cells"] += 1
            cells_out.append({"type": cell_type, "content": [block]})
        if cells_out:
            rows_out.append({"type": "tableRow", "content": cells_out})
    if not rows_out:
        removed["empty_blocks"] += 1
        return None
    return {"type": "table", "content": rows_out}


def _sanitize_block(node: Any, removed: Dict[str, int]) -> dict | None:
    if not isinstance(node, dict):
        return None
    ntype = str(node.get("type") or "")
    if ntype not in _ALLOWED_BLOCK_TYPES:
        removed["unsupported_blocks"] += 1
        return None
    if ntype in ("paragraph", "heading"):
        return _sanitize_textblock(node, default_type=ntype, removed=removed)
    if ntype in ("bulletList", "orderedList"):
        items = [
            _sanitize_list_item(item, removed)
            for item in (node.get("content") or [])
            if isinstance(item, dict)
        ]
        items = [item for item in items if item]
        if not items:
            removed["empty_blocks"] += 1
            return None
        return {"type": ntype, "content": items}
    if ntype == "table":
        return _sanitize_table(node, removed)
    if ntype == "listItem":
        return _sanitize_list_item(node, removed)
    removed["unsupported_blocks"] += 1
    return None


def sanitize_tiptap_doc(
    doc_json: dict | None,
    *,
    source: str = "",
) -> Tuple[dict, Dict[str, int]]:
    """
    Remove invalid TipTap nodes (empty text, whitespace-only text, empty inline paragraphs).
    Empty table cells become {type: paragraph} without empty text children.
    """
    removed: Dict[str, int] = {
        "empty_text": 0,
        "empty_paragraphs": 0,
        "empty_table_cells": 0,
        "empty_blocks": 0,
        "unsupported_blocks": 0,
    }
    if not doc_json or not isinstance(doc_json, dict):
        return {"type": "doc", "content": []}, removed

    content = [
        block
        for block in (
            _sanitize_block(node, removed) for node in (doc_json.get("content") or [])
        )
        if block
    ]
    sanitized = {"type": "doc", "content": content}
    if source and any(removed.values()):
        logger.info(
            "[tiptap-sanitize] source=%s removed=%s top_level_blocks=%s",
            source,
            removed,
            len(content),
        )
    return sanitized, removed


def _finalize_doc(content: List[dict], fallback_text: str = "", *, source: str) -> dict:
    if not content:
        t = str(fallback_text or "").strip()
        if t:
            content = [_paragraph_node(t)]
    doc, _ = sanitize_tiptap_doc({"type": "doc", "content": content}, source=source)
    return doc


def _table_node_from_block(block: Dict[str, Any]) -> dict | None:
    raw_rows = block.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        return None
    normalized = normalize_table_rows(raw_rows)
    if not normalized:
        return None
    header_rows = block.get("header_rows")
    if header_rows is None:
        header_rows = infer_header_row_count(normalized, None)
    else:
        header_rows = infer_header_row_count(normalized, int(header_rows))

    table_rows: List[dict] = []
    for row_index, row in enumerate(normalized):
        cells = []
        for cell in row:
            cell_type = "tableHeader" if row_index < header_rows else "tableCell"
            cell_text = str(cell if cell is not None else "")
            cells.append(
                {
                    "type": cell_type,
                    "content": [
                        _paragraph_from_multiline(cell_text)
                        if _has_visible_text(cell_text)
                        else _empty_paragraph()
                    ],
                }
            )
        if cells:
            table_rows.append({"type": "tableRow", "content": cells})
    return {"type": "table", "content": table_rows} if table_rows else None


_BULLET_LINE = re.compile(r"^[-*•]\s+")
_NUMBERED_LINE = re.compile(r"^\(?[A-Za-z0-9]+\)?[.)]\s+")
_KEY_VALUE_LINE = re.compile(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s/&()\-]{1,40}:\s+\S+")


def _is_sequential_elements(blocks: List[Dict[str, Any]]) -> bool:
    return any(
        isinstance(b, dict)
        and b.get("type") == "text"
        and isinstance(b.get("style"), str)
        and "content" in b
        for b in blocks or []
    )


def _map_sequential_elements_to_tiptap(
    elements: List[Dict[str, Any]], fallback_text: str = ""
) -> dict:
    """Map reading-order elements (text/table) to TipTap doc JSON."""
    content: List[dict] = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        etype = str(el.get("type") or "").lower()
        if etype == "text":
            text = str(el.get("content") or "").strip()
            if not text:
                continue
            style = str(el.get("style") or "paragraph").lower()
            if style == "heading":
                heading = _heading_node(text, 2)
                if heading:
                    content.append(heading)
            else:
                if paragraph_text_looks_like_table(text):
                    table_block = table_block_from_paragraph_text(text)
                    if table_block:
                        node = _table_node_from_block(table_block)
                        if node:
                            content.append(node)
                            continue
                lines = (
                    [line.strip() for line in text.splitlines() if line.strip()]
                    if "\n" in text
                    else _split_paragraph_lines(text)
                )
                if not lines:
                    continue
                if all(_BULLET_LINE.match(line) for line in lines):
                    items = [
                        item
                        for line in lines
                        for item in [_list_item_node(_BULLET_LINE.sub("", line).strip())]
                        if item
                    ]
                    if items:
                        content.append({"type": "bulletList", "content": items})
                    continue
                if all(_NUMBERED_LINE.match(line) for line in lines):
                    items = [
                        item
                        for line in lines
                        for item in [_list_item_node(_NUMBERED_LINE.sub("", line).strip())]
                        if item
                    ]
                    if items:
                        content.append({"type": "orderedList", "content": items})
                    continue
                content.append(_paragraph_from_multiline(text))
        elif etype == "table":
            rows = el.get("content")
            if isinstance(rows, list) and rows:
                node = _table_node_from_block({"rows": rows})
                if node:
                    content.append(node)

    if not content:
        t = str(fallback_text or "").strip()
        if t:
            content.append(_paragraph_node(t))
    return {"type": "doc", "content": content}


def map_blocks_to_tiptap_doc(blocks: List[Dict[str, Any]], fallback_text: str = "") -> dict:
    if _is_sequential_elements(blocks):
        return _map_sequential_elements_to_tiptap(blocks, fallback_text)

    content: List[dict] = []
    if not blocks:
        t = str(fallback_text or "").strip()
        if t:
            content.append(_paragraph_node(t))
        return {"type": "doc", "content": content}

    for block in blocks:
        typ = str(block.get("type") or "").lower()
        if typ in ("section_heading", "heading") and block.get("text"):
            level = min(3, max(1, int(block.get("level") or 2)))
            content.append(_heading_node(str(block["text"]), level))
        elif typ == "paragraph" and block.get("text"):
            raw = str(block.get("text") or "")
            if paragraph_text_looks_like_table(raw):
                table_block = table_block_from_paragraph_text(raw)
                if table_block:
                    node = _table_node_from_block(table_block)
                    if node:
                        content.append(node)
                        continue
            lines = (
                [line.strip() for line in raw.splitlines() if line.strip()]
                if "\n" in raw
                else _split_paragraph_lines(raw)
            )
            if not lines:
                continue
            if all(_BULLET_LINE.match(line) for line in lines):
                content.append(
                    {
                        "type": "bulletList",
                        "content": [
                            _list_item_node(_BULLET_LINE.sub("", line).strip()) for line in lines
                        ],
                    }
                )
                continue
            if all(_NUMBERED_LINE.match(line) for line in lines):
                content.append(
                    {
                        "type": "orderedList",
                        "content": [
                            _list_item_node(_NUMBERED_LINE.sub("", line).strip()) for line in lines
                        ],
                    }
                )
                continue
            if len(lines) > 1 and "\n" not in raw:
                for line in lines:
                    if _KEY_VALUE_LINE.match(line):
                        key, _, rest = line.partition(":")
                        content.append(
                            {
                                "type": "paragraph",
                                "content": [_strong_text(f"{key.strip()}: "), _text(rest.strip())],
                            }
                        )
                    else:
                        content.append(_paragraph_node(line))
                continue
            content.append(_paragraph_from_multiline(raw))
        elif typ in ("two_column_row", "key_value") and (block.get("left") or block.get("right")):
            left = str(block.get("left") or "").strip()
            right = str(block.get("right") or "").strip()
            if left and right:
                inline = []
                strong = _strong_text_node(f"{left}: ")
                if strong:
                    inline.append(strong)
                text_node = _text_node(right)
                if text_node:
                    inline.append(text_node)
                if inline:
                    content.append({"type": "paragraph", "content": inline})
            else:
                content.append(_paragraph_node(left or right))
        elif typ in ("bullet_list", "numbered_list", "ordered_list", "list") and isinstance(
            block.get("items"), list
        ):
            list_type = "orderedList" if typ in ("numbered_list", "ordered_list") else "bulletList"
            items = [
                item
                for it in block["items"]
                if str(it if it is not None else "").strip()
                for item in [_list_item_node(str(it))]
                if item
            ]
            if items:
                content.append({"type": list_type, "content": items})
        elif typ == "table" and isinstance(block.get("rows"), list) and block.get("rows"):
            node = _table_node_from_block(block)
            if node:
                content.append(node)

    return _finalize_doc(content, fallback_text, source="map_blocks_to_tiptap_doc")


def doc_has_tables(doc_json: dict | None) -> bool:
    if not doc_json or not isinstance(doc_json, dict):
        return False

    def walk(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if node.get("type") == "table":
            return True
        for child in node.get("content") or []:
            if walk(child):
                return True
        return False

    return walk(doc_json)


def merge_text_preserving_tables(
    existing_doc: dict | None,
    new_text: str,
    *,
    mode: str = "replace",
) -> dict:
    """
    Apply plain-text LLM output without destroying imported tables/lists/headings.
    Used by assistant SOP update actions in ai_routes.
    """
    base = existing_doc if isinstance(existing_doc, dict) else {"type": "doc", "content": []}
    preserved: List[dict] = []
    for node in base.get("content") or []:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype in {"table", "bulletList", "orderedList", "heading"}:
            preserved.append(node)

    incoming_lines = [ln.strip() for ln in re.split(r"\r?\n+", str(new_text or "")) if ln.strip()]
    if not incoming_lines:
        incoming_lines = [""]

    new_paragraphs = [
        _paragraph_node(ln[:1200])
        for ln in incoming_lines[:120]
        if _has_visible_text(ln)
    ]

    if mode == "append":
        content = list(base.get("content") or []) + new_paragraphs
    elif preserved and doc_has_tables(base):
        content = preserved + new_paragraphs
    else:
        content = new_paragraphs

    doc, _ = sanitize_tiptap_doc(
        {"type": "doc", "content": content or [_empty_paragraph()]},
        source="merge_text_preserving_tables",
    )
    return doc
