"""
Background SOP file import: extraction → save content → editor-ready → semantic indexing.
"""
from __future__ import annotations

import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import SOP, SOPVersion
from ..utils.tiptap_builder import map_blocks_to_tiptap_doc
from .semantic_jobs import schedule_import_semantic_followup
from .sop_import_job_registry import get_registry_record, register_import_job, update_import_job
from .sop_metadata_extractor import (
    extract_sop_metadata_from_text,
    is_generated_import_sop_number,
    normalize_document_status,
    normalize_sop_id_token,
    strip_invalid_control_chars,
    strip_sop_id_from_title,
    to_frontend_sop_metadata,
)

logger = logging.getLogger(__name__)

IMPORT_WORKER_THREADS = max(1, int(os.getenv("SOP_IMPORT_WORKER_THREADS", "2")))
IMPORT_UPLOAD_DIR = Path(os.getenv("SOP_IMPORT_UPLOAD_DIR", "data/sop_imports"))
_executor = ThreadPoolExecutor(max_workers=IMPORT_WORKER_THREADS, thread_name_prefix="sop-import")

# Pre-load local Marker models once per worker process (optional, non-blocking).
try:
    from .local_marker_extractor import warmup_local_marker

    warmup_local_marker()
except Exception:
    pass

IMPORT_STATUS_UPLOADING = "uploading"
IMPORT_STATUS_QUEUED = "queued"
IMPORT_STATUS_PROCESSING_MARKER = "processing_marker"
IMPORT_STATUS_CONVERTING_BLOCKS = "converting_blocks"
IMPORT_STATUS_SAVING_EDITOR_CONTENT = "saving_editor_content"
IMPORT_STATUS_RENDERING_READY = "rendering_ready"
IMPORT_STATUS_SEMANTIC = "semantic_processing"
IMPORT_STATUS_COMPLETED = "completed"
IMPORT_STATUS_FAILED = "failed"

# Legacy aliases (older clients / logs)
IMPORT_STATUS_PROCESSING = IMPORT_STATUS_PROCESSING_MARKER
IMPORT_STATUS_EXTRACTING = IMPORT_STATUS_PROCESSING_MARKER
IMPORT_STATUS_OCR = IMPORT_STATUS_PROCESSING_MARKER
IMPORT_STATUS_CREATING_SOP = IMPORT_STATUS_SAVING_EDITOR_CONTENT
IMPORT_STATUS_INDEXING = IMPORT_STATUS_SEMANTIC

TERMINAL_STATUSES = {IMPORT_STATUS_COMPLETED, IMPORT_STATUS_FAILED}
# Editor can load as soon as structured content is saved (before semantic/RAG jobs).
CONTENT_READY_STATUSES = {
    IMPORT_STATUS_RENDERING_READY,
    IMPORT_STATUS_COMPLETED,
}

_PROCESSING_PLACEHOLDER_DOC = {
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": "Your document is being processed. Content will appear here when extraction finishes.",
                }
            ],
        }
    ],
}


def import_upload_dir() -> Path:
    path = IMPORT_UPLOAD_DIR
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_import_job_payload(
    *,
    job_id: str,
    status: str,
    message: str,
    filename: str,
    scanned_pdf: bool = False,
    error: str | None = None,
    semantic_error: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> dict:
    payload = {
        "job_id": job_id,
        "status": status,
        "message": message,
        "filename": filename,
        "scanned_pdf": scanned_pdf,
        "error": error,
        "semantic_error": semantic_error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    return payload


def merge_import_job_metadata(metadata_json: dict | None, job_payload: dict) -> dict:
    meta = dict(metadata_json) if isinstance(metadata_json, dict) else {}
    meta["_import_job"] = job_payload
    return meta


def import_job_pending(metadata_json: dict | None) -> bool:
    if not isinstance(metadata_json, dict):
        return False
    job = metadata_json.get("_import_job")
    if not isinstance(job, dict):
        return False
    return str(job.get("status") or "") not in TERMINAL_STATUSES


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_import_entities(
    db: Session,
    sop_id: uuid.UUID,
    version_id: uuid.UUID,
) -> tuple[SOP | None, SOPVersion | None]:
    """Reload SOP rows by id (safe after long-running extraction in background threads)."""
    sop = db.query(SOP).filter(SOP.id == sop_id).first()
    version = (
        db.query(SOPVersion)
        .filter(SOPVersion.id == version_id, SOPVersion.sop_id == sop_id)
        .first()
    )
    return sop, version


def _update_job(
    db: Session,
    version: SOPVersion | None,
    *,
    status: str,
    message: str,
    scanned_pdf: bool | None = None,
    error: str | None = None,
    semantic_error: str | None = None,
    sop_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    job_id: str | None = None,
    filename: str | None = None,
) -> None:
    if version is None and sop_id is not None and version_id is not None:
        _, version = _load_import_entities(db, sop_id, version_id)
    if version is None:
        logger.warning("[sop-import] _update_job skipped: version not found job_id=%s", job_id)
        return

    meta = version.metadata_json if isinstance(version.metadata_json, dict) else {}
    job = dict(meta.get("_import_job") or {})
    resolved_job_id = str(job_id or job.get("job_id") or "")
    resolved_filename = str(filename or job.get("filename") or "")
    scanned = bool(job.get("scanned_pdf")) if scanned_pdf is None else scanned_pdf
    prev_semantic = job.get("semantic_error")
    resolved_semantic = semantic_error if semantic_error is not None else prev_semantic

    meta = merge_import_job_metadata(
        meta,
        build_import_job_payload(
            job_id=resolved_job_id,
            status=status,
            message=message,
            filename=resolved_filename,
            scanned_pdf=scanned,
            error=error,
            semantic_error=resolved_semantic,
        ),
    )
    version.metadata_json = meta
    db.add(version)
    db.commit()

    if resolved_job_id:
        update_import_job(
            resolved_job_id,
            status=status,
            message=message,
            scanned_pdf=scanned,
            error=error,
            semantic_error=resolved_semantic,
        )
    logger.info(
        "[sop-import] stage job_id=%s status=%s sop_id=%s version_id=%s message=%s",
        resolved_job_id,
        status,
        version.sop_id,
        version.id,
        message,
    )


SOP_IMPORT_STATUS_MESSAGES = {
    IMPORT_STATUS_UPLOADING: "Upload received. Processing in background…",
    IMPORT_STATUS_QUEUED: "Queued for local Marker PDF extraction…",
    IMPORT_STATUS_PROCESSING_MARKER: "Running local Marker PDF extraction…",
    IMPORT_STATUS_CONVERTING_BLOCKS: "Converting Marker output to structured blocks…",
    IMPORT_STATUS_SAVING_EDITOR_CONTENT: "Saving structured content to the editor…",
    IMPORT_STATUS_RENDERING_READY: "Document ready in the editor.",
    IMPORT_STATUS_SEMANTIC: "Linking entities and building search index…",
    IMPORT_STATUS_COMPLETED: "Import completed.",
    IMPORT_STATUS_FAILED: "Import failed.",
}

# Map Marker internal phases → persisted import job status
_MARKER_PHASE_TO_STATUS = {
    "cache_hit": IMPORT_STATUS_PROCESSING_MARKER,
    "processing_marker": IMPORT_STATUS_PROCESSING_MARKER,
    "converting_blocks": IMPORT_STATUS_CONVERTING_BLOCKS,
}


def build_import_job_status_dict(
    *,
    job_id: str,
    sop: SOP | None,
    version: SOPVersion | None,
    registry_row: dict | None = None,
) -> dict | None:
    """Build API status payload from ORM rows and optional in-memory registry."""
    from .document_extraction import extraction_snapshot

    reg = registry_row or get_registry_record(job_id)
    if not version:
        return None

    meta = version.metadata_json if isinstance(version.metadata_json, dict) else {}
    job = meta.get("_import_job") if isinstance(meta, dict) else {}
    if not isinstance(job, dict):
        job = {}

    status = str(reg.get("status") if reg and reg.get("status") else job.get("status") or IMPORT_STATUS_UPLOADING)
    message = (
        (reg.get("message") if reg else None)
        or job.get("message")
        or SOP_IMPORT_STATUS_MESSAGES.get(status, "")
    )
    semantic_error = (reg.get("semantic_error") if reg else None) or job.get("semantic_error")

    return {
        "job_id": str(job.get("job_id") or (reg or {}).get("job_id") or job_id),
        "sop_id": str(version.sop_id),
        "version_id": str(version.id),
        "status": status,
        "message": message,
        "filename": job.get("filename") or (reg or {}).get("filename"),
        "scanned_pdf": bool(job.get("scanned_pdf") if job.get("scanned_pdf") is not None else (reg or {}).get("scanned_pdf")),
        "error": job.get("error") or (reg or {}).get("error"),
        "semantic_error": semantic_error,
        "updated_at": job.get("updated_at") or (reg or {}).get("updated_at"),
        "sop_number": sop.sop_number if sop else None,
        "title": sop.title if sop else None,
        "doc_json_ready": status in CONTENT_READY_STATUSES,
        "extraction": extraction_snapshot(version),
    }


def _extract_file(
    raw: bytes,
    filename: str,
    on_stage,
    *,
    job_id: str | None = None,
    version: SOPVersion | None = None,
    db: Session | None = None,
) -> Tuple[list, str, bool]:
    """Run extraction; on_stage(status, message) for progress."""
    name = (filename or "").lower()
    scanned_pdf = False

    if name.endswith(".pdf"):
        from .document_extraction import apply_extraction_fields
        from .local_marker_extractor import EXTRACTION_ENGINE
        from .pdf_extractor import _pdf_is_scanned, extract_pdf_bytes_robust

        scanned_pdf = _pdf_is_scanned(raw)

        if version is not None and db is not None:
            apply_extraction_fields(
                version,
                job_id=job_id,
                status=IMPORT_STATUS_PROCESSING_MARKER,
                engine=EXTRACTION_ENGINE,
                mark_started=True,
            )
            db.add(version)
            db.commit()

        on_stage(
            IMPORT_STATUS_PROCESSING_MARKER,
            SOP_IMPORT_STATUS_MESSAGES[IMPORT_STATUS_PROCESSING_MARKER],
        )

        def _on_marker_phase(phase: str, message: str) -> None:
            status = _MARKER_PHASE_TO_STATUS.get(phase, IMPORT_STATUS_PROCESSING_MARKER)
            on_stage(status, message)

        blocks, text = extract_pdf_bytes_robust(
            raw,
            job_id=job_id,
            on_phase=_on_marker_phase,
        )
        text = strip_invalid_control_chars(text)
        return blocks, text, scanned_pdf

    if name.endswith(".docx"):
        on_stage(IMPORT_STATUS_CONVERTING_BLOCKS, "Extracting DOCX structure…")
        from .pdf_extractor import extract_docx_bytes

        blocks, text = extract_docx_bytes(raw)
        text = strip_invalid_control_chars(text)
        return blocks, text, False

    if name.endswith(".txt"):
        on_stage(IMPORT_STATUS_CONVERTING_BLOCKS, "Parsing text file…")
        from .document_structure import structure_blocks_from_text

        text = strip_invalid_control_chars(raw.decode("utf-8", errors="replace"))
        blocks = structure_blocks_from_text(text)
        return blocks, text, False

    raise ValueError("Unsupported file type")


def _apply_extracted_identity(
    db: Session,
    *,
    sop: SOP,
    sop_ui: dict,
    fallback_title: str,
) -> tuple[str, str]:
    """
    Resolve documentId/title from extraction; update sop.sop_number when a real ID was found.
    Returns (document_id, display_title).
    """
    extracted_id = normalize_sop_id_token(sop_ui.get("documentId") or "")
    extracted_title = strip_sop_id_from_title(
        str(sop_ui.get("title") or "").strip(),
        extracted_id,
    )
    fallback_title = (fallback_title or "Imported SOP").strip()

    document_id = extracted_id
    display_title = extracted_title or fallback_title

    if extracted_id:
        conflict = (
            db.query(SOP)
            .filter(SOP.sop_number == extracted_id, SOP.id != sop.id)
            .first()
        )
        if conflict:
            logger.warning(
                "[sop-import] extracted sop_number=%s already used by sop_id=%s; keeping placeholder=%s",
                extracted_id,
                conflict.id,
                sop.sop_number,
            )
        else:
            sop.sop_number = extracted_id
            document_id = extracted_id
    elif is_generated_import_sop_number(sop.sop_number):
        document_id = sop.sop_number
    else:
        document_id = sop.sop_number

    sop.title = display_title[:255] if display_title else sop.title
    return document_id, display_title


def _save_extracted_sop_content(
    db: Session,
    *,
    sop: SOP,
    version: SOPVersion,
    job_id: str,
    filename: str,
    blocks: list,
    text: str,
    scanned_pdf: bool,
    sop_ui: dict,
) -> None:
    doc_json = map_blocks_to_tiptap_doc(blocks, text)
    fallback_title = Path(filename).stem or "Imported SOP"
    document_id, resolved_title = _apply_extracted_identity(
        db,
        sop=sop,
        sop_ui=sop_ui,
        fallback_title=fallback_title,
    )

    meta = version.metadata_json if isinstance(version.metadata_json, dict) else {}
    meta = merge_import_job_metadata(
        meta,
        build_import_job_payload(
            job_id=job_id,
            status=IMPORT_STATUS_SAVING_EDITOR_CONTENT,
            message=SOP_IMPORT_STATUS_MESSAGES[IMPORT_STATUS_SAVING_EDITOR_CONTENT],
            filename=filename,
            scanned_pdf=scanned_pdf,
        ),
    )
    sm = meta.get("sopMetadata") if isinstance(meta.get("sopMetadata"), dict) else {}
    sm.update(
        {
            "title": resolved_title,
            "author": sm.get("author") or "System (Import)",
            "reviewer": sm.get("reviewer") or "",
            "documentId": document_id,
            "docType": sop_ui.get("docType") or sm.get("docType") or "SOP",
            "category": sop_ui.get("category") or sm.get("category") or "",
            "department": sop_ui.get("department") or sm.get("department") or sop.department or "Quality",
            "sopVersion": sop_ui.get("sopVersion") or sm.get("sopVersion") or "1",
            "effectiveDate": sop_ui.get("effectiveDate") or sm.get("effectiveDate") or "",
            "reviewDate": sop_ui.get("reviewDate") or sm.get("reviewDate") or "",
            "riskLevel": sop_ui.get("riskLevel") or sm.get("riskLevel") or "Low",
            "regulatoryReferences": sop_ui.get("regulatoryReferences")
            or sm.get("regulatoryReferences")
            or [],
        }
    )
    for key, value in sop_ui.items():
        if value is None or value == "":
            continue
        if key in sm:
            sm[key] = value

    extracted_status = normalize_document_status(
        sop_ui.get("sopStatus") or sop_ui.get("status") or ""
    )
    if extracted_status:
        meta["sopStatus"] = extracted_status
        sm["sopStatus"] = extracted_status
        sm["status"] = extracted_status
        version.external_status = extracted_status
    else:
        meta["sopStatus"] = meta.get("sopStatus") or "draft"

    meta["sopMetadata"] = sm

    version.content_json = doc_json
    version.metadata_json = meta
    db.add(version)
    db.add(sop)
    db.commit()
    db.refresh(version)
    logger.info(
        "[sop-import] content saved job_id=%s sop_number=%s title=%s version_id=%s blocks=%s chars=%s",
        job_id,
        sop.sop_number,
        resolved_title[:80],
        version.id,
        len(blocks),
        len(text or ""),
    )


def _run_import_job(
    job_id: str,
    sop_id: uuid.UUID,
    version_id: uuid.UUID,
    file_path: Path,
    filename: str,
) -> None:
    db = SessionLocal()
    try:
        sop, version = _load_import_entities(db, sop_id, version_id)
        if not sop or not version:
            logger.error("[sop-import] missing sop=%s version=%s job_id=%s", sop_id, version_id, job_id)
            update_import_job(
                job_id,
                status=IMPORT_STATUS_FAILED,
                message="Import failed.",
                error="SOP shell not found.",
            )
            return

        def on_stage(status: str, message: str, **kwargs) -> None:
            _update_job(
                db,
                None,
                status=status,
                message=message,
                sop_id=sop_id,
                version_id=version_id,
                job_id=job_id,
                filename=filename,
                **kwargs,
            )

        logger.info("[sop-import] worker started job_id=%s file=%s", job_id, filename)
        on_stage(IMPORT_STATUS_QUEUED, SOP_IMPORT_STATUS_MESSAGES[IMPORT_STATUS_QUEUED])
        raw = file_path.read_bytes()
        logger.info("[sop-import] file read job_id=%s bytes=%s", job_id, len(raw))

        db.expire_all()
        sop, version = _load_import_entities(db, sop_id, version_id)

        blocks, text, scanned_pdf = _extract_file(
            raw,
            filename,
            on_stage,
            job_id=job_id,
            version=version,
            db=db,
        )
        if not (text or "").strip() and not blocks:
            raise ValueError("No text content could be extracted from the file.")

        if version and filename.lower().endswith(".pdf"):
            try:
                from .document_extraction import apply_extraction_fields
                from .local_marker_extractor import (
                    EXTRACTION_ENGINE,
                    cache_key_for_pdf,
                    load_cached_marker_result,
                )

                cache_key = cache_key_for_pdf(raw, scanned=scanned_pdf)
                cached = load_cached_marker_result(cache_key)
                apply_extraction_fields(
                    version,
                    job_id=job_id,
                    status="completed",
                    engine=EXTRACTION_ENGINE,
                    cache_key=cache_key,
                    markdown=cached.markdown if cached else text,
                    meta_json=cached.metadata if cached else None,
                    mark_completed=True,
                )
                db.add(version)
                db.commit()
            except Exception as exc:
                logger.warning("[sop-import] extraction persist skipped: %s", exc)

        from .document_structure import enrich_metadata_text

        on_stage(
            IMPORT_STATUS_SAVING_EDITOR_CONTENT,
            SOP_IMPORT_STATUS_MESSAGES[IMPORT_STATUS_SAVING_EDITOR_CONTENT],
        )
        meta_text = enrich_metadata_text(text, blocks)
        _meta_cap = int(os.getenv("SOP_IMPORT_METADATA_TEXT_CAP", "120000"))
        clip = meta_text if len(meta_text) <= _meta_cap else meta_text[:_meta_cap]
        structured = extract_sop_metadata_from_text(
            clip,
            blocks,
            use_llm_fallback=len(text) < 250000,
        )
        sop_ui = to_frontend_sop_metadata(structured)
        logger.info(
            "[sop-import] metadata extracted job_id=%s documentId=%s title=%s",
            job_id,
            sop_ui.get("documentId") or "(none)",
            (sop_ui.get("title") or "")[:120],
        )

        db.expire_all()
        sop, version = _load_import_entities(db, sop_id, version_id)
        if not sop or not version:
            raise RuntimeError("SOP shell disappeared during background import.")

        _save_extracted_sop_content(
            db,
            sop=sop,
            version=version,
            job_id=job_id,
            filename=filename,
            blocks=blocks,
            text=text,
            scanned_pdf=scanned_pdf,
            sop_ui=sop_ui,
        )

        on_stage(
            IMPORT_STATUS_RENDERING_READY,
            SOP_IMPORT_STATUS_MESSAGES[IMPORT_STATUS_RENDERING_READY],
            scanned_pdf=scanned_pdf,
        )
        logger.info("[sop-import] rendering_ready job_id=%s sop_id=%s version_id=%s", job_id, sop_id, version_id)

        completion_message = (
            "SOP ready in the editor. Search indexing and linking continue in the background."
        )
        _update_job(
            db,
            None,
            status=IMPORT_STATUS_COMPLETED,
            message=completion_message,
            scanned_pdf=scanned_pdf,
            sop_id=sop_id,
            version_id=version_id,
            job_id=job_id,
            filename=filename,
        )
        logger.info(
            "[sop-import] content_complete job_id=%s sop_id=%s version_id=%s — scheduling semantic followup",
            job_id,
            sop_id,
            version_id,
        )

        schedule_import_semantic_followup(
            sop_id,
            version_id,
            import_job_id=job_id,
        )
    except Exception as exc:
        logger.exception("[sop-import] job %s failed", job_id)
        try:
            from .document_extraction import apply_extraction_fields

            sop_fail, version_fail = _load_import_entities(db, sop_id, version_id)
            if version_fail:
                apply_extraction_fields(
                    version_fail,
                    job_id=job_id,
                    status="failed",
                    engine="local_marker",
                    error=str(exc)[:500],
                    mark_completed=True,
                )
                db.add(version_fail)
                db.commit()
            _update_job(
                db,
                None,
                status=IMPORT_STATUS_FAILED,
                message=str(exc)[:300] or SOP_IMPORT_STATUS_MESSAGES[IMPORT_STATUS_FAILED],
                error=str(exc)[:500],
                sop_id=sop_id,
                version_id=version_id,
                job_id=job_id,
                filename=filename,
            )
        except Exception:
            logger.exception("[sop-import] failed to persist job failure state job=%s", job_id)
        update_import_job(
            job_id,
            status=IMPORT_STATUS_FAILED,
            message="Import failed.",
            error=str(exc)[:500],
        )
    finally:
        db.close()
        try:
            if file_path.exists():
                file_path.unlink()
        except OSError:
            pass


def enqueue_sop_import_job(
    *,
    job_id: str,
    sop_id: uuid.UUID,
    version_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
) -> None:
    ext = Path(filename or "upload.bin").suffix.lower() or ".bin"
    dest = import_upload_dir() / f"{job_id}{ext}"
    dest.write_bytes(file_bytes)

    def _run() -> None:
        _run_import_job(job_id, sop_id, version_id, dest, filename)

    _executor.submit(_run)
    logger.info(
        "[sop-import] queued job=%s sop=%s version=%s file=%s",
        job_id,
        sop_id,
        version_id,
        filename,
    )


def get_import_job_status(
    db: Session,
    job_id: str,
    *,
    version_id: uuid.UUID | None = None,
    sop_id: uuid.UUID | None = None,
) -> dict | None:
    reg = get_registry_record(job_id)
    if reg:
        try:
            reg_sop_id = uuid.UUID(str(reg["sop_id"]))
            reg_version_id = uuid.UUID(str(reg["version_id"]))
        except ValueError:
            reg_sop_id = reg_version_id = None
        if reg_sop_id and reg_version_id:
            sop, version = _load_import_entities(db, reg_sop_id, reg_version_id)
            if version:
                row = build_import_job_status_dict(
                    job_id=job_id,
                    sop=sop,
                    version=version,
                    registry_row=reg,
                )
                if row:
                    return row
            return {
                "job_id": job_id,
                "sop_id": str(reg["sop_id"]),
                "version_id": str(reg["version_id"]),
                "status": reg.get("status") or IMPORT_STATUS_UPLOADING,
                "message": reg.get("message") or SOP_IMPORT_STATUS_MESSAGES.get(reg.get("status"), ""),
                "filename": reg.get("filename"),
                "scanned_pdf": bool(reg.get("scanned_pdf")),
                "error": reg.get("error"),
                "semantic_error": reg.get("semantic_error"),
                "updated_at": reg.get("updated_at"),
                "sop_number": None,
                "title": None,
                "doc_json_ready": str(reg.get("status") or "") in CONTENT_READY_STATUSES,
            }

    if version_id is not None:
        sid = sop_id
        version = None
        sop = None
        if sid is None:
            version = db.query(SOPVersion).filter(SOPVersion.id == version_id).first()
            if version:
                sop = db.query(SOP).filter(SOP.id == version.sop_id).first()
        else:
            sop, version = _load_import_entities(db, sid, version_id)
        if version:
            row = build_import_job_status_dict(job_id=job_id, sop=sop, version=version)
            if row:
                return row

    try:
        from sqlalchemy import String, cast

        version = (
            db.query(SOPVersion)
            .filter(
                cast(SOPVersion.metadata_json["_import_job"]["job_id"], String) == str(job_id)
            )
            .order_by(SOPVersion.updated_at.desc())
            .first()
        )
    except Exception as exc:
        logger.warning("[sop-import] JSONB job lookup failed for %s: %s", job_id, exc)
        version = None

    if version is None:
        for row in (
            db.query(SOPVersion)
            .order_by(SOPVersion.updated_at.desc())
            .limit(500)
            .all()
        ):
            meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            job = meta.get("_import_job") if isinstance(meta, dict) else {}
            if isinstance(job, dict) and str(job.get("job_id")) == str(job_id):
                version = row
                break

    if not version:
        return None

    sop = db.query(SOP).filter(SOP.id == version.sop_id).first()
    return build_import_job_status_dict(job_id=job_id, sop=sop, version=version, registry_row=reg)


def processing_placeholder_doc() -> dict:
    return dict(_PROCESSING_PLACEHOLDER_DOC)
