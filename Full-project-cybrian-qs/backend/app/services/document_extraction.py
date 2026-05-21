"""
Persist document extraction status for SOP versions (local Marker PDF pipeline).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..models import SOPVersion


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def apply_extraction_fields(
    version: SOPVersion,
    *,
    job_id: str | None = None,
    status: str | None = None,
    engine: str | None = None,
    error: str | None = None,
    cache_key: str | None = None,
    markdown: str | None = None,
    meta_json: Dict[str, Any] | None = None,
    mark_started: bool = False,
    mark_completed: bool = False,
) -> None:
    if job_id is not None:
        version.extraction_job_id = job_id
    if status is not None:
        version.extraction_status = status
    if engine is not None:
        version.extraction_engine = engine
    if error is not None:
        version.extraction_error = error[:4000] if error else None
    if cache_key is not None:
        version.extraction_cache_key = cache_key
    if markdown is not None:
        cap = int(os.getenv("SOP_MARKER_MARKDOWN_DB_CAP", "500000"))
        version.extracted_markdown = markdown if len(markdown) <= cap else markdown[:cap]
    if meta_json is not None:
        version.extracted_json = meta_json
    if mark_started:
        version.extraction_started_at = _utc_now()
        version.extraction_completed_at = None
    if mark_completed:
        version.extraction_completed_at = _utc_now()

    meta = version.metadata_json if isinstance(version.metadata_json, dict) else {}
    doc_ext = meta.get("_document_extraction")
    if not isinstance(doc_ext, dict):
        doc_ext = {}
    if job_id is not None:
        doc_ext["extraction_job_id"] = job_id
    if status is not None:
        doc_ext["extraction_status"] = status
    if engine is not None:
        doc_ext["extraction_engine"] = engine
    if error is not None:
        doc_ext["extraction_error"] = error
    if cache_key is not None:
        doc_ext["extraction_cache_key"] = cache_key
    meta["_document_extraction"] = doc_ext
    version.metadata_json = meta


def extraction_snapshot(version: SOPVersion | None) -> Optional[Dict[str, Any]]:
    if version is None:
        return None
    return {
        "extraction_status": version.extraction_status,
        "extraction_engine": version.extraction_engine,
        "extraction_job_id": version.extraction_job_id,
        "extraction_error": version.extraction_error,
        "extraction_cache_key": version.extraction_cache_key,
        "extraction_started_at": (
            version.extraction_started_at.isoformat() if version.extraction_started_at else None
        ),
        "extraction_completed_at": (
            version.extraction_completed_at.isoformat() if version.extraction_completed_at else None
        ),
        "from_cache": bool(version.extraction_cache_key),
    }
