"""
Structure-preserving rewrite/improve for /api/ai/action (TipTap JSON patches).

Scoped actions (section_only): one primary block, one LLM call, no recursive chunk splitting.
Full-document actions: optional batched patches only when edit_scope is full_document.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from chatbot.schemas.sop_actions import ActionRequest
from fastapi import HTTPException

from ..schemas import AIActionRequest, AIActionResponse
from ..utils.tiptap_ai_patch import (
    TiptapStructureError,
    apply_text_patches,
    collapse_nodes_to_primary_block,
    extract_editable_text_nodes,
    extract_patches_from_llm_raw,
    expand_nodes_to_work_items,
    collapse_work_item_patches,
    filter_nodes_for_scope,
    validate_structure_preserved,
)
from ..utils.tiptap_text import extract_plain_text_from_tiptap

logger = logging.getLogger(__name__)

_SCOPE_SEGMENT_CAP = 10_000_000


@dataclass
class PatchRunReport:
    patches: dict[str, str] = field(default_factory=dict)
    unchanged_work_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    llm_calls: int = 0
    batches_run: int = 0
    selected_node_count: int = 0
    work_item_count: int = 0
    patch_count: int = 0
    plain_text_converted: bool = False
    patches_from_json: int = 0


def _brief_context(context: str, nlp_block: str, *, max_len: int = 900) -> str:
    parts = [p.strip() for p in (context, nlp_block) if p and str(p).strip()]
    if not parts:
        return ""
    merged = "\n".join(parts)
    if len(merged) <= max_len:
        return merged
    return merged[: max_len - 20].rstrip() + "\n[...context trimmed...]"


def _strip_code_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_scope_prompt(
    request: ActionRequest,
    nodes_payload: list[dict[str, str]],
    action: str,
    *,
    brief_context: str = "",
    is_full_document: bool = False,
) -> str:
    nodes_json = json.dumps(nodes_payload, ensure_ascii=False)
    task = (
        "Rewrite the text for clarity, GMP compliance, and professional SOP tone."
        if action == "rewrite"
        else "Improve the text with minimal edits for clarity and compliance."
    )
    ctx_block = f"\nREFERENCE (do not copy verbatim):\n{brief_context}\n" if brief_context else ""
    scope_note = (
        "Full-document edit: patch every INPUT NODE id."
        if is_full_document
        else "Scoped block edit: patch only the INPUT NODE ids listed (same block)."
    )
    return f"""You edit a TipTap SOP. {scope_note}

OUTPUT FORMAT (mandatory — nothing else):
{{"patches":[{{"id":"t1","text":"rewritten text"}}]}}

RULES:
- Return ONLY valid JSON with a "patches" array. No markdown, no HTML, no prose outside JSON.
- Use EXACTLY the ids from INPUT NODES. One patch per id. Never empty text.
- Do not change table structure, headings, or lists — only replace text values.

{task}

SOP: {request.sop_title} | Section: {request.section_title} | Scope: {request.edit_scope or "section_only"}
{ctx_block}
INPUT NODES:
{nodes_json}
"""


def _invoke_patch_llm(
    call_llm: Callable[..., str],
    *,
    prompt: str,
    input_char_budget: int,
    action: str,
    edit_scope: str,
) -> str:
    try:
        return call_llm(
            prompt,
            input_char_budget=input_char_budget,
            action=action,
            soft_fail=True,
        )
    except TypeError:
        return call_llm(
            prompt,
            input_char_budget=input_char_budget,
            action=action,
        )


def _coerce_llm_output_to_patches(
    raw: str,
    items: list[dict[str, Any]],
    report: PatchRunReport,
) -> dict[str, str]:
    expected_work = {str(w["work_id"]) for w in items}
    expected_targets = {str(w["target_id"]) for w in items}

    patches = extract_patches_from_llm_raw(raw, expected_work)
    if not patches:
        patches = extract_patches_from_llm_raw(raw, expected_targets)

    if patches:
        report.patches_from_json = len(patches)
        return patches

    plain = _strip_code_fence(raw)
    if not plain:
        raise TiptapStructureError("LLM returned empty response")

    if plain.startswith("{") and "patches" in plain:
        raise TiptapStructureError("Could not parse patches JSON from LLM response")

    unique_targets = {str(w["target_id"]) for w in items}
    if len(unique_targets) == 1:
        tid = next(iter(unique_targets))
        report.plain_text_converted = True
        logger.info(
            "[tiptap-ai-action] plain_text_converted=yes target_id=%s plain_chars=%s work_items=%s",
            tid,
            len(plain),
            len(items),
        )
        return {tid: plain}

    if len(items) == 1:
        wid = str(items[0]["work_id"])
        report.plain_text_converted = True
        logger.info(
            "[tiptap-ai-action] plain_text_converted=yes work_id=%s plain_chars=%s",
            wid,
            len(plain),
        )
        return {wid: plain}

    raise TiptapStructureError(
        "LLM returned plain text instead of patches JSON for multi-node scope"
    )


def _llm_patches_for_work_items(
    items: list[dict[str, Any]],
    *,
    request: ActionRequest,
    action: str,
    call_llm: Callable[..., str],
    brief_context: str,
    edit_scope: str,
    report: PatchRunReport,
) -> dict[str, str]:
    if not items:
        return {}

    payload = [
        {
            "id": w["work_id"],
            "block": str(w.get("block") or "paragraph"),
            "text": str(w.get("text") or ""),
        }
        for w in items
    ]
    expected_ids = {w["work_id"] for w in items}
    batch_chars = sum(len(str(w.get("text") or "")) for w in items)
    is_full = str(edit_scope or "").lower() == "full_document"
    prompt = _build_scope_prompt(
        request,
        payload,
        action,
        brief_context=brief_context,
        is_full_document=is_full,
    )
    llm_action = f"{action}_tiptap_scope" if not is_full else f"{action}_tiptap_batch"

    report.llm_calls += 1
    raw = _invoke_patch_llm(
        call_llm,
        prompt=prompt,
        input_char_budget=batch_chars,
        action=llm_action,
        edit_scope=edit_scope,
    )

    patches = _coerce_llm_output_to_patches(raw, items, report)

    missing = expected_ids - set(patches.keys()) - {str(w["target_id"]) for w in items}
    resolved_targets = {str(w["target_id"]) for w in items}
    covered = set(patches.keys()) & (expected_ids | resolved_targets)
    if len(covered) < max(1, len(expected_ids) * 0.4) and not report.plain_text_converted:
        raise TiptapStructureError(
            f"Too few patches returned ({len(covered)}/{len(expected_ids)})"
        )
    return patches


def _split_full_document_batches(
    work_items: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    try:
        max_nodes = max(8, int(os.getenv("ACTION_TIPTAP_BATCH_START_NODES", "24")))
    except (TypeError, ValueError):
        max_nodes = 24
    try:
        max_chars = max(4000, int(os.getenv("ACTION_TIPTAP_BATCH_START_CHARS", "14000")))
    except (TypeError, ValueError):
        max_chars = 14000

    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for item in work_items:
        text_len = len(str(item.get("text") or ""))
        overflow = current and (
            len(current) >= max_nodes or current_chars + text_len > max_chars
        )
        if overflow:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += text_len
    if current:
        batches.append(current)
    return batches or [[]]


def _collect_patches(
    scoped_nodes: list[dict[str, Any]],
    *,
    request: ActionRequest,
    action: str,
    call_llm: Callable[..., str],
    context: str,
    nlp_block: str,
    edit_scope: str = "section_only",
) -> PatchRunReport:
    is_full = str(edit_scope or "").lower() == "full_document"
    segment_cap = None if is_full else _SCOPE_SEGMENT_CAP
    work_items = expand_nodes_to_work_items(scoped_nodes, segment_chars=segment_cap)
    if not work_items:
        raise TiptapStructureError("No editable text nodes in scope")

    if is_full:
        batches = _split_full_document_batches(work_items)
    else:
        batches = [work_items]

    brief = _brief_context(context, nlp_block)
    report = PatchRunReport(
        selected_node_count=len(scoped_nodes),
        work_item_count=len(work_items),
    )
    work_patches: dict[str, str] = {}

    for batch_idx, batch_items in enumerate(batches):
        report.batches_run += 1
        batch_patches: dict[str, str] = {}
        try:
            batch_patches = _llm_patches_for_work_items(
                batch_items,
                request=request,
                action=action,
                call_llm=call_llm,
                brief_context=brief if batch_idx == 0 else "",
                edit_scope=edit_scope,
                report=report,
            )
        except (TiptapStructureError, HTTPException) as exc:
            logger.warning(
                "[tiptap-ai-action] batch failed scope=%s nodes=%s work_items=%s err=%s; keeping originals",
                edit_scope,
                len(scoped_nodes),
                len(batch_items),
                exc,
            )
            batch_patches = {
                str(item.get("work_id") or ""): str(item.get("text") or "")
                for item in batch_items
                if item.get("work_id")
            }
            for item in batch_items:
                wid = str(item.get("work_id") or "")
                if wid:
                    report.unchanged_work_ids.append(wid)
                    report.warnings.append(f"Kept original ({wid}): {str(exc)[:80]}")

        for item in batch_items:
            wid = str(item.get("work_id") or "")
            if wid and wid not in batch_patches and str(item.get("target_id") or "") not in batch_patches:
                tid = str(item.get("target_id") or "")
                if tid in batch_patches:
                    continue
                batch_patches[wid] = str(item.get("text") or "")
                report.unchanged_work_ids.append(wid)

        work_patches.update(batch_patches)
        logger.info(
            "[tiptap-ai-action] batch %s/%s work_items=%s patches=%s json_patches=%s plain_converted=%s",
            batch_idx + 1,
            len(batches),
            len(batch_items),
            len(batch_patches),
            report.patches_from_json,
            report.plain_text_converted,
        )

    report.patches = collapse_work_item_patches(work_patches, work_items)
    report.patch_count = len(report.patches)

    for node in scoped_nodes:
        nid = str(node.get("id") or "")
        if nid and nid not in report.patches:
            report.patches[nid] = str(node.get("text") or "")
            report.unchanged_work_ids.append(nid)
            report.warnings.append(f"Node {nid} unchanged: no patch returned")

    return report


def _build_explanation(
    action: str,
    report: PatchRunReport,
    *,
    applied: int,
) -> str:
    verb = "rewritten" if action == "rewrite" else "improved"
    base = (
        f"SOP {verb} with TipTap structure preserved "
        f"({applied} text node{'s' if applied != 1 else ''} updated)."
    )
    if report.plain_text_converted:
        base += " Model plain-text reply was converted into a patch."
    unchanged = len(set(report.unchanged_work_ids))
    if unchanged:
        base += f" {unchanged} node{'s' if unchanged != 1 else ''} kept original text."
    return base


def run_tiptap_structured_action(
    *,
    payload: AIActionRequest,
    action: str,
    request: ActionRequest,
    content_json: dict,
    context: str,
    nlp_block: str,
    style_profile: dict[str, Any],
    nlp_summary: dict[str, Any],
    call_llm: Callable[..., str],
    ch_budget: int,
) -> AIActionResponse:
    """
    Rewrite/improve by patching text nodes in content_json.
    Scoped edits use one block and one LLM call; never flatten to plain-text document replacement.
    """
    from chatbot.actions.prompts import resolve_edit_scope

    del ch_budget

    edit_scope = resolve_edit_scope(request)
    patch_ids = getattr(payload, "patch_node_ids", None)
    if patch_ids is not None and not isinstance(patch_ids, list):
        patch_ids = None

    all_nodes, _ = extract_editable_text_nodes(content_json)
    if not all_nodes:
        raise TiptapStructureError("No editable text nodes in TipTap document")

    scoped_nodes = filter_nodes_for_scope(
        all_nodes,
        request.section_text,
        edit_scope,
        patch_node_ids=patch_ids,
    )
    if not scoped_nodes:
        raise TiptapStructureError("No editable nodes in scope")

    is_full = str(edit_scope or "").lower() == "full_document"
    if not is_full and len(scoped_nodes) > 1:
        scoped_nodes = collapse_nodes_to_primary_block(
            scoped_nodes,
            request.section_text,
            patch_node_ids=patch_ids,
        )

    logger.info(
        "[tiptap-ai-action] scope=%s selected_nodes=%s patch_node_ids=%s",
        edit_scope,
        len(scoped_nodes),
        len(patch_ids or []),
    )

    report = _collect_patches(
        scoped_nodes,
        request=request,
        action=action,
        call_llm=call_llm,
        context=context,
        nlp_block=nlp_block,
        edit_scope=edit_scope,
    )

    patched, applied, sanitize_stats = apply_text_patches(
        content_json,
        report.patches,
        source=f"ai_action_{action}",
    )
    validate_structure_preserved(
        content_json,
        patched,
        expected_patch_count=max(1, applied),
    )

    plain = extract_plain_text_from_tiptap(patched)
    unchanged_unique = sorted(set(report.unchanged_work_ids))
    logger.info(
        "[tiptap-ai-action] action=%s scope=%s selected_nodes=%s work_items=%s "
        "patch_count=%s applied=%s llm_calls=%s plain_text_converted=%s json_patches=%s "
        "unchanged=%s sanitize=%s",
        action,
        edit_scope,
        report.selected_node_count,
        report.work_item_count,
        report.patch_count,
        applied,
        report.llm_calls,
        report.plain_text_converted,
        report.patches_from_json,
        len(unchanged_unique),
        sanitize_stats,
    )

    structured: dict[str, Any] = {
        "tiptap_preserved": True,
        "patches_applied": applied,
        "nodes_sent": report.selected_node_count,
        "work_items_sent": report.work_item_count,
        "patch_count": report.patch_count,
        "llm_calls": report.llm_calls,
        "plain_text_converted": report.plain_text_converted,
        "patches_from_json": report.patches_from_json,
        "unchanged_chunks": unchanged_unique,
        "chunk_warnings": report.warnings[:50],
        "style_profile": style_profile,
        "nlp_action_summary": nlp_summary,
        "edit_scope": edit_scope,
        "sanitize_stats": sanitize_stats,
    }

    explanation = _build_explanation(action, report, applied=applied)

    if action == "rewrite":
        structured["rewritten_text"] = plain
        return AIActionResponse(
            action="rewrite",
            original_text=request.section_text,
            suggested_text=plain[:12000] + ("…" if len(plain) > 12000 else ""),
            explanation=explanation,
            suggested_content_json=patched,
            structured_data=structured,
        )

    structured["improved_text"] = plain
    structured["improved_version"] = plain
    return AIActionResponse(
        action="improve",
        original_text=request.section_text,
        suggested_text=plain[:12000] + ("…" if len(plain) > 12000 else ""),
        explanation=explanation,
        suggested_content_json=patched,
        structured_data=structured,
    )
