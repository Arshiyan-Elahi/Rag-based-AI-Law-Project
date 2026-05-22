"""
Background execution for long TipTap AI actions (rewrite / improve / gap_check).
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from ..schemas import AIActionRequest, AIActionResponse
from .ai_action_job_registry import get_ai_action_job, register_ai_action_job, update_ai_action_job

logger = logging.getLogger(__name__)

ASYNC_ACTIONS = frozenset({"rewrite", "improve", "gap_check"})

_STATUS_MESSAGES = {
    "queued": "Queued for processing…",
    "processing": "Processing…",
    "completed": "Completed.",
    "failed": "Action failed.",
}


def _normalize_action(action: str) -> str:
    return str(action or "").strip().lower().replace("-", "_")


def action_should_run_async(action: str, payload: AIActionRequest | None = None) -> bool:
    """All editor actions use synchronous POST /api/ai/action (job queue disabled)."""
    del action, payload
    return False


def _serialize_result(out: AIActionResponse) -> dict[str, Any]:
    if hasattr(out, "model_dump"):
        return out.model_dump(mode="json")
    return out.dict()


def _run_job(job_id: str, payload: AIActionRequest, action: str) -> None:
    from ..ai_routes import _run_dynamic_ai_action

    update_ai_action_job(
        job_id,
        status="processing",
        message=_STATUS_MESSAGES["processing"],
    )
    try:
        out = _run_dynamic_ai_action(payload, action)
        update_ai_action_job(
            job_id,
            status="completed",
            message=_STATUS_MESSAGES["completed"],
            result=_serialize_result(out),
        )
        logger.info(
            "[ai-action-job] completed job_id=%s action=%s suggested_chars=%s",
            job_id,
            action,
            len(out.suggested_text or ""),
        )
    except Exception as exc:
        logger.exception("[ai-action-job] failed job_id=%s action=%s", job_id, action)
        update_ai_action_job(
            job_id,
            status="failed",
            message=_STATUS_MESSAGES["failed"],
            error=str(exc)[:500],
        )


def enqueue_ai_action_job(payload: AIActionRequest, action: str) -> str:
    """Start background worker; returns job_id."""
    job_id = str(uuid.uuid4())
    register_ai_action_job(
        job_id=job_id,
        action=action,
        status="queued",
        message=_STATUS_MESSAGES["queued"],
    )

    payload_copy = payload.model_copy(deep=True) if hasattr(payload, "model_copy") else payload

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, payload_copy, action),
        name=f"ai-action-{action}-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return job_id


def build_job_status_response(job_id: str) -> dict[str, Any] | None:
    row = get_ai_action_job(job_id)
    if not row:
        return None
    return {
        "job_id": row["job_id"],
        "action": row.get("action"),
        "status": row.get("status"),
        "message": row.get("message") or _STATUS_MESSAGES.get(row.get("status"), ""),
        "error": row.get("error"),
        "progress": row.get("progress") or {},
        "result": row.get("result"),
        "updated_at": row.get("updated_at"),
    }
