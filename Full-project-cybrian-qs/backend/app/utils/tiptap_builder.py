"""
Map SOP extraction blocks to TipTap JSON (StarterKit + table extensions).
Mirrors frontend src/utils/editorUtils.js mapBlocksToTipTapDoc.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .table_blocks import (
    infer_header_row_count,
    normalize_table_rows,
    paragraph_text_looks_like_table,
    table_block_from_paragraph_text,
    table_block_from_rows,
)


def _text(s: Any) -> dict:
    return {"type": "text", "text": str(s if s is not None else "")}


def _strong_text(s: Any) -> dict:
    return {"type": "text", "text": str(s if s is not None else ""), "marks": [{"type": "bold"}]}


def _split_paragraph_lines(text: str = "") -> List[str]:
    return [line.strip() for line in re.split(r"\r?\n", str(text or "")) if line.strip()]


def _paragraph_node(text: str = "") -> dict:
    return {"type": "paragraph", "content": [_text(text)]}


def _heading_node(text: str = "", level: int = 2) -> dict:
    lvl = min(3, max(1, int(level or 2)))
    return {"type": "heading", "attrs": {"level": lvl}, "content": [_text(text)]}


def _list_item_node(text: str = "") -> dict:
    return {"type": "listItem", "content": [_paragraph_node(text)]}


def _paragraph_from_multiline(text: str = "") -> dict:
    raw = str(text or "")
    lines = [line.strip() for line in raw.splitlines()]
    if len(lines) <= 1:
        return _paragraph_node(raw.strip())
    content: List[dict] = []
    for idx, line in enumerate(lines):
        if line:
            content.append(_text(line))
        if idx < len(lines) - 1:
            content.append({"type": "hardBreak"})
    return {"type": "paragraph", "content": content or [_text("")]}


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
            cells.append(
                {
                    "type": cell_type,
                    "content": [_paragraph_from_multiline(str(cell or ""))],
                }
            )
        if cells:
            table_rows.append({"type": "tableRow", "content": cells})
    return {"type": "table", "content": table_rows} if table_rows else None


_BULLET_LINE = re.compile(r"^[-*•]\s+")
_NUMBERED_LINE = re.compile(r"^\(?[A-Za-z0-9]+\)?[.)]\s+")
_KEY_VALUE_LINE = re.compile(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s/&()\-]{1,40}:\s+\S+")


def map_blocks_to_tiptap_doc(blocks: List[Dict[str, Any]], fallback_text: str = "") -> dict:
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
                content.append(
                    {"type": "paragraph", "content": [_strong_text(f"{left}: "), _text(right)]}
                )
            else:
                content.append(_paragraph_node(left or right))
        elif typ in ("bullet_list", "numbered_list", "ordered_list", "list") and isinstance(
            block.get("items"), list
        ):
            list_type = "orderedList" if typ in ("numbered_list", "ordered_list") else "bulletList"
            items = [
                _list_item_node(str(it))
                for it in block["items"]
                if str(it if it is not None else "").strip()
            ]
            if items:
                content.append({"type": list_type, "content": items})
        elif typ == "table" and isinstance(block.get("rows"), list) and block.get("rows"):
            node = _table_node_from_block(block)
            if node:
                content.append(node)

    if not content:
        t = str(fallback_text or "").strip()
        if t:
            content.append(_paragraph_node(t))

    return {"type": "doc", "content": content}


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
        {"type": "paragraph", "content": [{"type": "text", "text": ln[:1200]}]}
        for ln in incoming_lines[:120]
    ]

    if mode == "append":
        content = list(base.get("content") or []) + new_paragraphs
    elif preserved and doc_has_tables(base):
        content = preserved + new_paragraphs
    else:
        content = new_paragraphs

    return {"type": "doc", "content": content or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}
