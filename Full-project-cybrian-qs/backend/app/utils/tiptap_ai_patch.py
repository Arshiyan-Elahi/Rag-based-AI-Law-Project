"""
TipTap structure-preserving AI actions: extract text nodes, patch by stable id, validate layout.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any, Dict, List, Tuple

from chatbot.actions.utils import (
    _json_slice_heuristic,
    _load_first_json_object_from_text,
    _prepare_for_json_parse,
    clean_json,
)

from .tiptap_builder import sanitize_tiptap_doc

logger = logging.getLogger(__name__)


class TiptapStructureError(ValueError):
    """Raised when patched output would break TipTap/table layout."""


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def table_fingerprints(doc_json: dict | None) -> List[Tuple[int, ...]]:
    """Per-table tuple of cell counts per row (order preserved)."""
    fps: List[Tuple[int, ...]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "table":
            rows: List[int] = []
            for row in node.get("content") or []:
                if isinstance(row, dict) and row.get("type") == "tableRow":
                    cells = row.get("content") or []
                    rows.append(len([c for c in cells if isinstance(c, dict)]))
            if rows:
                fps.append(tuple(rows))
        for child in node.get("content") or []:
            walk(child)

    if isinstance(doc_json, dict):
        walk(doc_json)
    return fps


def count_text_nodes(doc_json: dict | None) -> int:
    count = 0

    def walk(node: Any) -> None:
        nonlocal count
        if not isinstance(node, dict):
            return
        if node.get("type") == "text":
            count += 1
        for child in node.get("content") or []:
            walk(child)

    if isinstance(doc_json, dict):
        walk(doc_json)
    return count


def extract_editable_text_nodes(doc_json: dict | None) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Walk TipTap JSON and collect editable text nodes with stable ids and JSON paths.

    Each entry: {id, path, text, block}
    path is a list of keys/indices ending at the text node index inside a content array.
    """
    nodes: List[Dict[str, Any]] = []

    def walk(node: Any, path: List[Any], block_type: str) -> None:
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == "text":
            raw = node.get("text")
            if raw is None:
                return
            text = str(raw)
            if not text.strip():
                return
            nid = f"t{len(nodes) + 1}"
            nodes.append(
                {
                    "id": nid,
                    "path": path.copy(),
                    "text": text,
                    "block": block_type,
                }
            )
            return
        children = node.get("content")
        if not isinstance(children, list):
            return
        child_block = block_type
        if ntype in (
            "heading",
            "paragraph",
            "table",
            "tableRow",
            "tableCell",
            "tableHeader",
            "bulletList",
            "orderedList",
            "listItem",
            "doc",
        ):
            child_block = str(ntype)
        for idx, child in enumerate(children):
            walk(child, path + ["content", idx], child_block)

    if isinstance(doc_json, dict):
        for idx, block in enumerate(doc_json.get("content") or []):
            if isinstance(block, dict):
                walk(block, ["content", idx], str(block.get("type") or "paragraph"))

    id_map = {n["id"]: n for n in nodes}
    return nodes, id_map


def filter_nodes_for_scope(
    nodes: List[Dict[str, Any]],
    section_text: str,
    edit_scope: str,
    *,
    patch_node_ids: List[str] | None = None,
) -> List[Dict[str, Any]]:
    if str(edit_scope or "").lower() == "full_document":
        return nodes

    if patch_node_ids:
        id_set = {str(x) for x in patch_node_ids if x}
        matched = [n for n in nodes if str(n.get("id") or "") in id_set]
        if matched:
            return collapse_nodes_to_primary_block(
                matched,
                section_text,
                patch_node_ids=patch_node_ids,
            )
        logger.warning(
            "[tiptap-ai-patch] patch_node_ids=%s matched 0 of %s nodes",
            len(id_set),
            len(nodes),
        )

    section_norm = _normalize_ws(section_text)
    if not section_norm:
        return []

    matched: List[Dict[str, Any]] = []
    for node in nodes:
        node_norm = _normalize_ws(node.get("text") or "")
        if not node_norm:
            continue
        if node_norm in section_norm or section_norm in node_norm:
            matched.append(node)
            continue
        if len(node_norm) >= 12 and node_norm[: min(48, len(node_norm))] in section_norm:
            matched.append(node)
    if matched:
        return collapse_nodes_to_primary_block(
            matched,
            section_text,
            patch_node_ids=patch_node_ids,
        )
    logger.warning(
        "[tiptap-ai-patch] section filter matched 0 nodes (scope=%s); not using full document",
        edit_scope,
    )
    return []


def block_group_key(node: Dict[str, Any]) -> str:
    """Stable key for the TipTap block (paragraph, cell, list item) owning a text node."""
    path = node.get("path") or []
    if isinstance(path, list) and len(path) >= 2:
        return json.dumps(path[:-2], ensure_ascii=False)
    return json.dumps(path, ensure_ascii=False)


def collapse_nodes_to_primary_block(
    nodes: List[Dict[str, Any]],
    section_text: str,
    *,
    patch_node_ids: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Reduce a scope match to a single editor block (paragraph / table cell / list item).
    Avoids sending an entire table row or multi-cell selection as separate patch targets.
    """
    if not nodes:
        return []
    if len(nodes) == 1:
        return nodes

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes:
        key = block_group_key(node)
        groups.setdefault(key, []).append(node)

    id_set = {str(x) for x in (patch_node_ids or []) if x}
    if id_set:
        ranked: List[Tuple[int, int, List[Dict[str, Any]]]] = []
        for grp in groups.values():
            hits = sum(1 for n in grp if str(n.get("id") or "") in id_set)
            ranked.append((hits, len(grp), grp))
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        if ranked[0][0] > 0:
            chosen = ranked[0][2]
            logger.info(
                "[tiptap-ai-patch] collapsed scope %s nodes -> %s in primary block (patch_id_hits=%s)",
                len(nodes),
                len(chosen),
                ranked[0][0],
            )
            return chosen

    section_norm = _normalize_ws(section_text)
    best_grp = nodes
    best_score = -1
    for grp in groups.values():
        concat = _normalize_ws(" ".join(str(n.get("text") or "") for n in grp))
        if not concat:
            continue
        if section_norm and (section_norm in concat or concat in section_norm):
            score = len(concat) + 1000
        elif section_norm:
            tokens = [t for t in section_norm.split() if len(t) >= 4]
            score = sum(1 for t in tokens if t in concat)
        else:
            score = len(concat)
        if score > best_score:
            best_score = score
            best_grp = grp

    logger.info(
        "[tiptap-ai-patch] collapsed scope %s nodes -> %s in primary block (score=%s)",
        len(nodes),
        len(best_grp),
        best_score,
    )
    return best_grp


def _node_at_path(root: dict, path: List[Any]) -> dict:
    cur: Any = root
    for key in path:
        if isinstance(key, int):
            cur = cur[key]
        else:
            cur = cur[key]
    if not isinstance(cur, dict):
        raise TiptapStructureError(f"Invalid path target type at {path}")
    return cur


def apply_text_patches(
    doc_json: dict,
    patches: Dict[str, str],
    *,
    source: str = "tiptap_ai_patch",
) -> Tuple[dict, int, Dict[str, int]]:
    """Apply id→text patches in-place on a deep copy; sanitize before return."""
    doc = copy.deepcopy(doc_json)
    _, id_map = extract_editable_text_nodes(doc)
    applied = 0
    skipped_empty = 0

    for nid, new_text in (patches or {}).items():
        entry = id_map.get(str(nid))
        if not entry:
            logger.debug("[tiptap-ai-patch] unknown patch id=%s", nid)
            continue
        text_val = str(new_text if new_text is not None else "")
        if not text_val.strip():
            skipped_empty += 1
            continue
        path = entry["path"]
        text_node = _node_at_path(doc, path)
        if text_node.get("type") != "text":
            raise TiptapStructureError(f"Path does not point to text node: {path}")
        text_node["text"] = text_val
        applied += 1

    sanitized, stats = sanitize_tiptap_doc(doc, source=source)
    if skipped_empty:
        logger.info(
            "[tiptap-ai-patch] skipped_empty_patches=%s applied=%s sanitize=%s",
            skipped_empty,
            applied,
            stats,
        )
    return sanitized, applied, stats


def validate_structure_preserved(
    original: dict,
    patched: dict,
    *,
    expected_patch_count: int | None = None,
) -> None:
    """Raise TiptapStructureError if tables or text-node topology were destroyed."""
    orig_tables = table_fingerprints(original)
    new_tables = table_fingerprints(patched)
    if orig_tables != new_tables:
        raise TiptapStructureError(
            f"Table layout changed (before={orig_tables!r} after={new_tables!r})"
        )

    orig_blocks = len(original.get("content") or []) if isinstance(original, dict) else 0
    new_blocks = len(patched.get("content") or []) if isinstance(patched, dict) else 0
    if orig_blocks and new_blocks < max(1, int(orig_blocks * 0.5)):
        raise TiptapStructureError(
            f"Top-level block count collapsed ({orig_blocks} -> {new_blocks})"
        )

    if orig_tables and not new_tables:
        raise TiptapStructureError("Tables were removed from the document")

    orig_text_nodes = count_text_nodes(original)
    new_text_nodes = count_text_nodes(patched)
    if orig_text_nodes and new_text_nodes < max(1, int(orig_text_nodes * 0.4)):
        raise TiptapStructureError(
            f"Text node count collapsed ({orig_text_nodes} -> {new_text_nodes})"
        )

    if expected_patch_count and expected_patch_count > 0 and new_text_nodes == 0:
        raise TiptapStructureError("Patched document has no text nodes")


def looks_like_flattened_plaintext(text: str, *, node_count: int) -> bool:
    """
    Heuristic: long plain blob with many newlines but no patch structure when many nodes were sent.
    """
    raw = str(text or "").strip()
    if not raw or node_count < 4:
        return False
    if raw.startswith("{") and "patches" in raw:
        return False
    line_count = raw.count("\n")
    return line_count >= max(8, node_count // 2) and len(raw) > 400


def build_nodes_payload(nodes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    return [
        {
            "id": n["id"],
            "block": str(n.get("block") or "paragraph"),
            "text": str(n.get("text") or ""),
        }
        for n in nodes
    ]


def _segment_target_chars() -> int:
    """Internal LLM chunk size — not a user-facing document limit."""
    try:
        return max(400, int(os.getenv("ACTION_TIPTAP_SEGMENT_CHARS", "1400")))
    except (TypeError, ValueError):
        return 1400


def _hard_split_text(text: str, max_chars: int) -> List[str]:
    parts: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            parts.append(remaining)
            break
        cut = remaining.rfind(" ", 0, max_chars)
        if cut < max_chars // 3:
            cut = max_chars
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    return [p for p in parts if p]


def _split_by_sentences(text: str, max_chars: int) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 1:
        return _hard_split_text(text, max_chars)
    chunks: List[str] = []
    buf = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_hard_split_text(sent, max_chars))
            continue
        candidate = f"{buf} {sent}".strip() if buf else sent
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            buf = sent
    if buf:
        chunks.append(buf)
    return chunks


def split_text_into_segments(text: str, max_chars: int | None = None) -> List[Tuple[str, str]]:
    """
    Split long node text into rewrite-sized segments.

    Returns list of (segment_text, joiner_after) where joiner_after is inserted
    between this segment and the next (e.g. '\\n\\n' for paragraph breaks).
    """
    cap = max_chars if max_chars is not None else _segment_target_chars()
    raw = str(text or "")
    if len(raw) <= cap:
        return [(raw, "")]

    segments: List[Tuple[str, str]] = []
    paragraphs = re.split(r"(\n\s*\n)", raw)
    buf = ""
    buf_joiner = ""

    def flush_buf() -> None:
        nonlocal buf, buf_joiner
        if not buf:
            return
        if len(buf) <= cap:
            segments.append((buf, buf_joiner))
        else:
            for piece in _split_by_sentences(buf, cap):
                segments.append((piece, buf_joiner if not segments else ""))
                buf_joiner = " "
        buf = ""
        buf_joiner = ""

    i = 0
    while i < len(paragraphs):
        part = paragraphs[i]
        if i + 1 < len(paragraphs) and re.fullmatch(r"\n\s*\n", paragraphs[i + 1] or ""):
            sep = paragraphs[i + 1]
            i += 2
        else:
            sep = ""
            i += 1
        piece = part or ""
        if not piece.strip() and not sep:
            continue
        if len(piece) > cap:
            flush_buf()
            subs = _split_by_sentences(piece, cap)
            for j, sub in enumerate(subs):
                segments.append((sub, sep if j == len(subs) - 1 else " "))
            continue
        candidate = f"{buf}{buf_joiner}{piece}" if buf else piece
        if len(candidate) <= cap:
            if buf:
                buf_joiner = sep or buf_joiner
            else:
                buf_joiner = ""
            buf = candidate
        else:
            flush_buf()
            buf = piece
            buf_joiner = sep
    flush_buf()
    return segments if segments else [(raw, "")]


def expand_nodes_to_work_items(
    nodes: List[Dict[str, Any]],
    *,
    segment_chars: int | None = None,
) -> List[Dict[str, Any]]:
    """
    Expand TipTap text nodes into LLM work items. Large nodes become multiple
    segment items (id like t12__0) that merge back into the parent id.
    """
    cap = segment_chars if segment_chars is not None else _segment_target_chars()
    items: List[Dict[str, Any]] = []
    for node in nodes:
        nid = str(node.get("id") or "")
        text = str(node.get("text") or "")
        if not nid or not text.strip():
            continue
        if len(text) <= cap:
            items.append(
                {
                    "work_id": nid,
                    "target_id": nid,
                    "text": text,
                    "block": str(node.get("block") or "paragraph"),
                    "segment_index": None,
                    "segment_joiner": "",
                    "original_text": text,
                }
            )
            continue
        segs = split_text_into_segments(text, cap)
        for idx, (seg_text, joiner) in enumerate(segs):
            items.append(
                {
                    "work_id": f"{nid}__{idx}",
                    "target_id": nid,
                    "text": seg_text,
                    "block": str(node.get("block") or "paragraph"),
                    "segment_index": idx,
                    "segment_joiner": joiner,
                    "original_text": text,
                }
            )
    return items


def collapse_work_item_patches(
    patches: Dict[str, str],
    work_items: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Merge segment patches (t5__0, t5__1) back into parent node ids (t5)."""
    by_target: Dict[str, List[Tuple[int, str, str]]] = {}
    direct: Dict[str, str] = {}

    item_by_work = {str(w["work_id"]): w for w in work_items}

    for work_id, new_text in (patches or {}).items():
        item = item_by_work.get(str(work_id))
        if not item:
            continue
        tid = str(item["target_id"])
        seg_idx = item.get("segment_index")
        if seg_idx is None:
            direct[tid] = str(new_text)
            continue
        by_target.setdefault(tid, []).append(
            (int(seg_idx), str(new_text), str(item.get("segment_joiner") or ""))
        )

    merged = dict(direct)
    for tid, parts in by_target.items():
        parts.sort(key=lambda x: x[0])
        blob = ""
        for i, (_idx, seg_text, joiner) in enumerate(parts):
            if i > 0 and joiner:
                blob += joiner
            elif i > 0:
                blob += " "
            blob += seg_text
        merged[tid] = blob
    return merged


def _extract_patches_from_object(data: Any, expected_ids: set[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    patches = data.get("patches")
    if not isinstance(patches, list):
        return out
    for item in patches:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or "").strip()
        if not iid:
            continue
        if expected_ids and iid not in expected_ids:
            continue
        text_val = item.get("text")
        if text_val is None:
            continue
        out[iid] = str(text_val)
    return out


def _regex_extract_patches(raw: str, expected_ids: set[str]) -> Dict[str, str]:
    """Last-resort extraction of id/text pairs from noisy LLM output."""
    out: Dict[str, str] = {}
    if not raw or not expected_ids:
        return out
    for iid in expected_ids:
        pattern = (
            r'\{\s*"id"\s*:\s*"'
            + re.escape(iid)
            + r'"\s*,\s*"text"\s*:\s*"((?:\\.|[^"\\])*)"\s*\}'
        )
        m = re.search(pattern, raw)
        if m:
            try:
                out[iid] = json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                out[iid] = m.group(1).replace("\\n", "\n").replace('\\"', '"')
    return out


def extract_patches_from_llm_raw(raw: str, expected_ids: set[str]) -> Dict[str, str]:
    """
    Deterministic multi-strategy JSON patch extraction (strict → repair → regex).
    """
    if not (raw and str(raw).strip()):
        return {}

    candidates: List[str] = []
    for blob in (raw, clean_json(raw), _json_slice_heuristic(raw)):
        if blob and blob not in candidates:
            candidates.append(blob)
    prepared, _notes = _prepare_for_json_parse(raw)
    if prepared and prepared not in candidates:
        candidates.append(prepared)

    for cand in candidates:
        try:
            data = _load_first_json_object_from_text(cand)
            found = _extract_patches_from_object(data, expected_ids)
            if found:
                return found
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        try:
            data = json.loads(cand)
            found = _extract_patches_from_object(data, expected_ids)
            if found:
                return found
        except (json.JSONDecodeError, TypeError):
            continue

    regex_found = _regex_extract_patches(raw, expected_ids)
    if regex_found:
        return regex_found
    return {}
