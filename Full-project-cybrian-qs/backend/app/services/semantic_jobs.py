"""
Shared async semantic reindex scheduler (CRUD, webhooks, imports, links).
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..database import SessionLocal
from ..models import SOP, SOPVersion
from .semantic_pipeline import SemanticPipelineService

logger = logging.getLogger(__name__)

SEMANTIC_WORKER_THREADS = max(1, int(os.getenv("SEMANTIC_WORKER_THREADS", "2")))
_executor = ThreadPoolExecutor(max_workers=SEMANTIC_WORKER_THREADS, thread_name_prefix="semantic-job")

_schedule_lock = threading.Lock()
_import_followup_inflight: set[str] = set()


def schedule_semantic_reindex(
    entity_type: str,
    entity_id: uuid.UUID,
    version_id: uuid.UUID | None = None,
    job_type: str = "entity_reindex",
    *,
    skip_unchanged_import: bool = True,
) -> str | None:
    """
    Enqueue and process a semantic job in the background thread pool.
    Returns job_id when queued, else None.
    """
    if entity_type == "sop" and version_id and skip_unchanged_import:
        db = SessionLocal()
        try:
            version = (
                db.query(SOPVersion)
                .filter(SOPVersion.id == version_id, SOPVersion.sop_id == entity_id)
                .first()
            )
            if version and isinstance(version.metadata_json, dict):
                from ..utils.tiptap_text import extract_plain_text_from_tiptap

                plain_text = extract_plain_text_from_tiptap(version.content_json)
                if plain_text:
                    import hashlib

                    content_hash = hashlib.sha256(
                        plain_text.encode("utf-8", errors="ignore")
                    ).hexdigest()
                    import_hash = version.metadata_json.get("_import_context_hash")
                    if import_hash == content_hash:
                        logger.info(
                            "[semantic-job] skipped unchanged sop %s v=%s",
                            entity_id,
                            version_id,
                        )
                        return None
        finally:
            db.close()

    dedupe_key = f"{entity_type}:{entity_id}:{version_id or ''}:{job_type}"
    with _schedule_lock:
        job_id = SemanticPipelineService.enqueue_reindex(
            entity_type=entity_type,
            entity_id=entity_id,
            version_id=version_id,
            job_type=job_type,
        )
    if not job_id:
        return None

    def _run() -> None:
        try:
            SemanticPipelineService.process_job(job_id)
        except Exception as exc:
            logger.exception("[semantic-job] job %s failed: %s", job_id, exc)

    _executor.submit(_run)
    logger.info(
        "[semantic-job] scheduled %s for %s:%s job=%s",
        job_type,
        entity_type,
        entity_id,
        job_id,
    )
    return str(job_id)


def schedule_entities(entities: list[tuple[str, uuid.UUID, uuid.UUID | None]], job_type: str = "webhook_sync") -> list[str]:
    """Queue reindex for multiple entities; returns job ids."""
    job_ids: list[str] = []
    for entity_type, entity_id, version_id in entities:
        jid = schedule_semantic_reindex(
            entity_type,
            entity_id,
            version_id,
            job_type=job_type,
            skip_unchanged_import=False,
        )
        if jid:
            job_ids.append(jid)
    return job_ids


def schedule_import_semantic_followup(
    sop_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    import_job_id: str | None = None,
) -> None:
    """
    Run post-import semantic work in the background (never blocks editor content).

    Order: entity extraction/linking → schedule per-entity reindex jobs (chunks,
    embeddings, Qdrant, suggestions) via the semantic worker pool.
    """
    followup_key = f"sop:{sop_id}:{version_id}"
    with _schedule_lock:
        if followup_key in _import_followup_inflight:
            logger.info(
                "[semantic-job] skip duplicate import followup sop=%s version=%s",
                sop_id,
                version_id,
            )
            return
        _import_followup_inflight.add(followup_key)

    def _run() -> None:
        db = SessionLocal()
        try:
            sop = db.query(SOP).filter(SOP.id == sop_id).first()
            version = (
                db.query(SOPVersion)
                .filter(SOPVersion.id == version_id, SOPVersion.sop_id == sop_id)
                .first()
            )
            if not sop or not version:
                logger.warning(
                    "[semantic-job] import followup missing entities sop=%s version=%s",
                    sop_id,
                    version_id,
                )
                return

            from ..routes import _upsert_import_context_entities

            logger.info(
                "[semantic-job] import followup started job_id=%s sop=%s version=%s",
                import_job_id,
                sop_id,
                version_id,
            )
            _upsert_import_context_entities(db, sop, version, None)
            logger.info(
                "[semantic-job] import followup finished job_id=%s sop=%s",
                import_job_id,
                sop_id,
            )
        except Exception as exc:
            logger.exception(
                "[semantic-job] import followup failed job_id=%s sop=%s: %s",
                import_job_id,
                sop_id,
                exc,
            )
            try:
                schedule_semantic_reindex(
                    "sop",
                    sop_id,
                    version_id,
                    job_type="import_reindex",
                    skip_unchanged_import=False,
                )
            except Exception as sched_exc:
                logger.warning(
                    "[semantic-job] fallback sop reindex schedule failed: %s",
                    sched_exc,
                )
        finally:
            db.close()
            with _schedule_lock:
                _import_followup_inflight.discard(followup_key)

    _executor.submit(_run)
    logger.info(
        "[semantic-job] queued import followup sop=%s version=%s import_job_id=%s",
        sop_id,
        version_id,
        import_job_id,
    )
