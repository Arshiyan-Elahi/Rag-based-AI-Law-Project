"""In-process registry for async /api/ai/action jobs (rewrite, improve, gap_check)."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_ai_action_job(
    *,
    job_id: str,
    action: str,
    status: str = "queued",
    message: str = "",
) -> dict[str, Any]:
    record = {
        "job_id": str(job_id),
        "action": str(action),
        "status": status,
        "message": message,
        "error": None,
        "result": None,
        "progress": {},
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
    }
    with _lock:
        _jobs[str(job_id)] = record
    logger.info("[ai-action-job] registered job_id=%s action=%s status=%s", job_id, action, status)
    return dict(record)


def update_ai_action_job(
    job_id: str,
    *,
    status: str | None = None,
    message: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with _lock:
        row = _jobs.get(str(job_id))
        if not row:
            return None
        if status is not None:
            row["status"] = status
        if message is not None:
            row["message"] = message
        if error is not None:
            row["error"] = error
        if result is not None:
            row["result"] = result
        if progress is not None:
            row["progress"] = {**(row.get("progress") or {}), **progress}
        row["updated_at"] = _utc_now_iso()
        return dict(row)


def get_ai_action_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _jobs.get(str(job_id))
        return dict(row) if row else None
