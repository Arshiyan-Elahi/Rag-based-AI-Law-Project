"""
Thread-safe in-process registry for async SOP import jobs.

Polling uses this first so job status survives metadata overwrites and JSONB lookup issues.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_import_job(
    *,
    job_id: str,
    sop_id: UUID,
    version_id: UUID,
    status: str,
    message: str,
    filename: str = "",
    scanned_pdf: bool = False,
    error: str | None = None,
    semantic_error: str | None = None,
) -> dict[str, Any]:
    record = {
        "job_id": str(job_id),
        "sop_id": str(sop_id),
        "version_id": str(version_id),
        "status": status,
        "message": message,
        "filename": filename,
        "scanned_pdf": bool(scanned_pdf),
        "error": error,
        "semantic_error": semantic_error,
        "updated_at": _utc_now_iso(),
    }
    with _lock:
        _jobs[str(job_id)] = record
    logger.info(
        "[sop-import-registry] registered job_id=%s status=%s sop_id=%s version_id=%s",
        job_id,
        status,
        sop_id,
        version_id,
    )
    return record


def update_import_job(
    job_id: str,
    *,
    status: str | None = None,
    message: str | None = None,
    scanned_pdf: bool | None = None,
    error: str | None = None,
    semantic_error: str | None = None,
) -> dict[str, Any] | None:
    with _lock:
        record = _jobs.get(str(job_id))
        if not record:
            return None
        if status is not None:
            record["status"] = status
        if message is not None:
            record["message"] = message
        if scanned_pdf is not None:
            record["scanned_pdf"] = scanned_pdf
        if error is not None:
            record["error"] = error
        if semantic_error is not None:
            record["semantic_error"] = semantic_error
        record["updated_at"] = _utc_now_iso()
        return dict(record)


def get_registry_record(job_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _jobs.get(str(job_id))
        return dict(row) if row else None


def resolve_ids(job_id: str) -> tuple[UUID | None, UUID | None]:
    row = get_registry_record(job_id)
    if not row:
        return None, None
    try:
        return UUID(str(row["sop_id"])), UUID(str(row["version_id"]))
    except ValueError:
        return None, None
