"""Persist /api/ai/query exchanges into existing chat_sessions / chat_messages tables."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ChatMessage, ChatSession

logger = logging.getLogger(__name__)

MAX_TITLE = 500


def _parse_session_uuid(raw: object) -> UUID | None:
    if raw is None or raw == "":
        return None
    try:
        return UUID(str(raw).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _sop_snapshot(assistant_context: dict | None) -> dict | None:
    if not isinstance(assistant_context, dict):
        return None
    cur = assistant_context.get("current_sop")
    if not isinstance(cur, dict):
        return None
    snap: dict = {}
    id_val = cur.get("id") or cur.get("documentId")
    if id_val is not None and str(id_val).strip():
        snap["sop_id"] = str(id_val).strip()
    ver = cur.get("current_version_id") or cur.get("version_id") or cur.get("versionId")
    if ver is not None and str(ver).strip():
        snap["sop_version_id"] = str(ver).strip()
    sn = cur.get("sop_number") or cur.get("sopNumber")
    if sn:
        snap["sop_number"] = str(sn)
    title = cur.get("title")
    if title:
        snap["title"] = str(title)[:300]
    return snap or None


def _build_retrieval_metadata(response: dict, llm_provider: str, llm_model: str) -> dict:
    stats = response.get("retrieval_stats")
    if not isinstance(stats, dict):
        stats = {}
    return {
        "retrieval_stats": stats,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
    }


def persist_chat_query_exchange(
    *,
    user_id: str,
    client_session_id: str | None,
    collection_name: str,
    category: str | None,
    question: str,
    response: dict,
    assistant_context: dict | None,
    llm_provider: str,
    llm_model: str,
) -> dict:
    """
    Insert one user row and one assistant row. Swallows all errors (logs only).

    Returns optional keys to merge into the API JSON: session_id, message_id
    (assistant message id).
    """
    try:
        uid = UUID(str(user_id).strip())
    except (ValueError, AttributeError, TypeError):
        logger.warning("chat persistence skipped: invalid user_id")
        return {}

    db: Session = SessionLocal()
    try:
        coll = (collection_name or "").strip() or "docs_sops"
        title_hint = (question or "").strip().replace("\n", " ")[:MAX_TITLE] or "Chat"
        cat_filter = (str(category).strip()[:100] if category else None) or None

        sid = _parse_session_uuid(client_session_id)
        session_row = None
        if sid is not None:
            session_row = (
                db.query(ChatSession)
                .filter(
                    ChatSession.id == sid,
                    ChatSession.user_id == uid,
                    ChatSession.is_active == True,  # noqa: E712
                )
                .first()
            )
        if session_row is None:
            session_row = ChatSession(
                user_id=uid,
                title=title_hint,
                collection_name=coll,
                is_active=True,
            )
            db.add(session_row)
            db.flush()

        if not session_row.title or not str(session_row.title).strip():
            session_row.title = title_hint

        meta_snap = _sop_snapshot(assistant_context)
        retrieval_meta = _build_retrieval_metadata(response, llm_provider, llm_model)
        answer = str(response.get("answer") or "")
        citations = response.get("citations")

        user_msg = ChatMessage(
            session_id=session_row.id,
            role="user",
            content=(question or "").strip() or "(empty)",
            citations=None,
            retrieval_metadata=None,
            metadata_snapshot=meta_snap,
            category_filter=cat_filter,
        )
        asst_msg = ChatMessage(
            session_id=session_row.id,
            role="assistant",
            content=answer if answer.strip() else "(empty)",
            citations=citations if citations is not None else None,
            retrieval_metadata=retrieval_meta,
            metadata_snapshot=meta_snap,
            category_filter=cat_filter,
        )
        db.add(user_msg)
        db.add(asst_msg)
        db.commit()
        db.refresh(asst_msg)

        return {
            "session_id": str(session_row.id),
            "message_id": str(asst_msg.id),
        }
    except Exception as exc:
        db.rollback()
        logger.warning("chat query persistence failed (non-fatal): %s", exc, exc_info=True)
        return {}
    finally:
        db.close()
