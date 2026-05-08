"""
Compatibility shim.

The chatbot code is being consolidated under `backend/chatbot/`.
Keep this module so existing imports (`app.ai_routes`) continue to work.
"""

from chatbot.routes.ai_routes import *  # noqa: F401,F403

from html import escape
import re
import os
import math
import time
import threading
import asyncio
import uuid
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_
from langchain_core.output_parsers import StrOutputParser

from action.prompts import (
    IMPROVE_REWRITE_NO_RAG_CONTEXT,
    build_gap_check_prompt,
    build_improve_prompt,
    build_rewrite_prompt,
)
from action.runtime import create_action_runtime
from action.utils import format_chunks, parse_with_retry
from schemas.sop_actions import ActionRequest, GapCheckResponse, ImproveResponse, RewriteResponse
from .schemas import AIActionRequest, AIActionResponse
from .database import SessionLocal
from .models import SOP, SOPVersion, Deviation, Capa, AuditFinding, Decision
from chatbot.llm.provider import get_local_llm_config, is_local_llm_unreachable_error

# RAG-specific imports are lazy-loaded inside _get_smart_rag_chain()
# to avoid ModuleNotFoundError when running without the RAG chatbot modules.
# Modules: embeddings.embedder, retrieval.*, chain.rag_chain, langchain_qdrant, qdrant_client

ai_router = APIRouter()
_smart_rag_lock = threading.Lock()
_smart_rag_chain = None
_action_runtime_lock = threading.Lock()
_action_runtime = None
CHAT_QUERY_TIMEOUT_SECONDS = int(os.getenv("CHAT_QUERY_TIMEOUT_SECONDS", "60"))
SOP_REF_PATTERN = re.compile(r"\bSOP-[A-Z0-9-]+\b", re.IGNORECASE)
DEV_REF_PATTERN = re.compile(r"\bDEV-[A-Z0-9-]+\b", re.IGNORECASE)
CAPA_REF_PATTERN = re.compile(r"\bCAPA-[A-Z0-9-]+\b", re.IGNORECASE)
AUDIT_REF_PATTERN = re.compile(r"\bAUDIT-[A-Z0-9-]+\b", re.IGNORECASE)
DECISION_REF_PATTERN = re.compile(r"\bDEC-[A-Z0-9-]+\b", re.IGNORECASE)
# Reload server after changing CHATBOT_USE_LOCAL_DB in environment (import-time flag).
# Default is false so semantic RAG/Qdrant is used unless explicitly overridden.
CHATBOT_USE_LOCAL_DB = os.getenv("CHATBOT_USE_LOCAL_DB", "false").strip().lower() == "true"
CHATBOT_ALLOW_LOCAL_DB_PRIMARY = os.getenv("CHATBOT_ALLOW_LOCAL_DB_PRIMARY", "false").strip().lower() == "true"
logger = logging.getLogger(__name__)
ACTION_INTENT_CREATE = re.compile(r"\b(create|new|generate|draft)\b.*\b(sop)\b", re.IGNORECASE)
ACTION_INTENT_DELETE = re.compile(r"\b(delete|remove)\b.*\b(sop|this sop|current sop)\b", re.IGNORECASE)
ACTION_INTENT_UPDATE = re.compile(
    r"\b(update|edit|modify|revise)\b.*\b(sop|this sop|current sop)\b|"
    r"\b(add)\b.*\b(section)\b.*\b(current sop|this sop)\b",
    re.IGNORECASE,
)


def _is_prompt_too_large_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return (
        "context length" in msg
        or "n_keep" in msg
        or "prompt is too long" in msg
        or "maximum context length" in msg
        or "too many tokens" in msg
    )


def _get_smart_rag_chain() -> Any:
    """
    Lazy-load chatbot runtime so the main backend starts even if
    optional RAG env vars are missing.
    """
    global _smart_rag_chain
    if _smart_rag_chain is not None:
        return _smart_rag_chain

    with _smart_rag_lock:
        if _smart_rag_chain is not None:
            return _smart_rag_chain

        from qdrant_client import QdrantClient
        from langchain_qdrant import QdrantVectorStore
        from embeddings.embedder import get_embedder
        from retrieval.federated_retriever import FederatedRetriever
        from retrieval.hybrid_retriever import rag_unified_enabled, unified_semantic_collection
        from retrieval.reranker import CrossEncoderReranker
        from chain.rag_chain import SmartRAGChain

        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        if not qdrant_url:
            raise RuntimeError("QDRANT_URL is not configured")

        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        embedder = get_embedder()
        reranker = None
        try:
            reranker = CrossEncoderReranker(top_n=5)
        except Exception as reranker_exc:
            print(
                f"[startup] Reranker cache missing, continuing without reranker: {reranker_exc}",
                flush=True,
            )

        if rag_unified_enabled():
            ucol = unified_semantic_collection()
            collection_map = {
                "sops": ucol,
                "deviations": ucol,
                "capas": ucol,
                "audits": ucol,
                "decisions": ucol,
            }
        else:
            collection_map = {
                "sops": os.getenv("COLLECTION_SOPS", "docs_sops"),
                "deviations": os.getenv("COLLECTION_DEVIATIONS", "docs_deviations"),
                "capas": os.getenv("COLLECTION_CAPAS", "docs_capas"),
                "audits": os.getenv("COLLECTION_AUDITS", "docs_audits"),
                "decisions": os.getenv("COLLECTION_DECISIONS", "docs_decisions"),
            }
        vectorstores = {
            section: QdrantVectorStore(client=client, collection_name=collection_name, embedding=embedder)
            for section, collection_name in collection_map.items()
        }
        federated = FederatedRetriever(client=client, vectorstores=vectorstores, reranker=reranker)
        for section, collection_name in collection_map.items():
            federated.retrievers[section].collection_name = collection_name

        _smart_rag_chain = SmartRAGChain(federated)
        return _smart_rag_chain


def _normalize_action(action: str) -> str:
    normalized = (action or "").strip().lower().replace("-", "_")
    aliases = {
        "gapcheck": "gap_check",
        "quality_check": "gap_check",
        "support": "improve",
    }
    return aliases.get(normalized, normalized)


def _get_action_runtime() -> Any:
    global _action_runtime
    if _action_runtime is not None:
        return _action_runtime

    with _action_runtime_lock:
        if _action_runtime is not None:
            return _action_runtime
        _action_runtime = create_action_runtime()
        return _action_runtime


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _split_sentences(text: str) -> list[str]:
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [part.strip() for part in parts if part.strip()]


def _extract_text_from_tiptap(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type")
    if node_type == "text":
        return str(node.get("text", ""))
    chunks: list[str] = []
    for child in node.get("content", []) or []:
        child_text = _extract_text_from_tiptap(child)
        if child_text:
            chunks.append(child_text)
    joiner = "\n" if node_type in {"paragraph", "heading", "listItem"} else " "
    return joiner.join(chunks).strip()


def _extract_sop_refs(question: str, chat_history: list[dict]) -> list[str]:
    refs = set(match.upper() for match in SOP_REF_PATTERN.findall(question or ""))
    for message in (chat_history or [])[-6:]:
        content = str(message.get("content", ""))
        for match in SOP_REF_PATTERN.findall(content):
            refs.add(match.upper())
    return sorted(refs)


def _extract_entity_refs(pattern: re.Pattern, question: str, chat_history: list[dict]) -> list[str]:
    refs = set(match.upper() for match in pattern.findall(question or ""))
    q_lower = (question or "").lower()
    list_intent = any(term in q_lower for term in [
        "all", "list", "show", "available", "which sops", "what sops", "sops",
        "which deviations", "what deviations", "deviations",
    ])
    follow_up_intent = any(term in q_lower for term in [
        "that", "same", "previous", "earlier", "this one", "the one",
        "wohi", "same sop", "same deviation",
    ])

    # Only pull refs from history for true follow-up questions.
    include_history = follow_up_intent and not list_intent
    if include_history:
        for message in (chat_history or [])[-6:]:
            content = str(message.get("content", ""))
            for match in pattern.findall(content):
                refs.add(match.upper())
    return sorted(refs)


def _build_local_db_chat_response(question: str, chat_history: list[dict], category: str | None) -> dict:
    q = (question or "").strip()
    q_like = f"%{q}%"
    q_lower = q.lower()
    q_tokens = [token for token in re.findall(r"[a-z0-9]+", q_lower) if len(token) >= 3]
    category = (category or "").strip().lower()
    db = SessionLocal()
    try:
        count_intent = bool(
            re.search(r"\b(how many|count|number of|total)\b", q_lower)
        )
        sop_intent = category in {"sops", "sop"} or "sop" in q_lower
        if count_intent and sop_intent:
            total_sops = db.query(SOP).count()
            return {
                "answer": f"Summary: There are {total_sops} SOP record(s) currently available.",
                "sources": [{
                    "id": f"INDEX-SOP-COUNT({total_sops})",
                    "type": "sop",
                    "label": "Indexed SOP inventory",
                }],
                # "citations": [{
                #     "ref": f"INDEX-SOP-COUNT({total_sops})",
                #     "title": "Indexed SOP inventory",
                #     "type": "sop",
                #     "status": "",
                #     "score": 1.0,
                #     "excerpt": f"Distinct SOPs in primary database: {total_sops}.",
                # }],
                "retrieval_debug": [],
                "suggestions": [
                    "List all SOPs with titles",
                    "Open a specific SOP by number",
                    "Ask for latest SOP changes",
                ],
                "retrieval_stats": {"mode": "local-db", "hits": 1, "count_mode": True},
                "routed_to": "local-db-count",
            }

        citations = []
        sources = []
        answer_parts = []

        def push_source(ref: str, title: str, source_type: str, excerpt: str):
            citations.append({
                "ref": ref,
                "title": title,
                "type": source_type,
                "status": "",
                "score": 1.0,
                "excerpt": excerpt,
            })
            sources.append({
                "id": ref,
                "type": source_type,
                "label": title or ref,
            })

        def _tokenized_clause(columns):
            if not q_tokens:
                return None
            clauses = []
            for token in q_tokens[:8]:
                token_like = f"%{token}%"
                for col in columns:
                    clauses.append(col.ilike(token_like))
            return or_(*clauses) if clauses else None

        wants_sops = category in {"", "sops", "sop", "all"} and (
            category in {"sops", "sop"} or "sop" in q_lower or "procedure" in q_lower or "policy" in q_lower
        )
        wants_deviations = category in {"", "deviations", "deviation", "all"} and (
            category in {"deviations", "deviation"} or "deviation" in q_lower or "deviations" in q_lower or "excursion" in q_lower
        )
        wants_capas = category in {"", "capas", "capa", "all"} and (
            category in {"capas", "capa"} or "capa" in q_lower or "corrective" in q_lower
        )
        wants_audits = category in {"", "audits", "audit", "all"} and (
            category in {"audits", "audit"} or "audit" in q_lower or "finding" in q_lower
        )
        wants_decisions = category in {"", "decisions", "decision", "all"} and (
            category in {"decisions", "decision"} or "decision" in q_lower
        )

        if not any([wants_sops, wants_deviations, wants_capas, wants_audits, wants_decisions]):
            # Broad natural-language query without explicit type: search SOP + deviations first.
            wants_sops = True
            wants_deviations = True

        # SOPs
        if wants_sops:
            sop_refs = _extract_entity_refs(SOP_REF_PATTERN, question, chat_history)
            sops = []
            if sop_refs:
                for ref in sop_refs[:5]:
                    row = db.query(SOP).filter(SOP.sop_number.ilike(ref)).first()
                    if row:
                        sops.append(row)
            else:
                token_clause = _tokenized_clause([SOP.sop_number, SOP.title, SOP.department])
                base = db.query(SOP)
                if token_clause is not None:
                    sops = base.filter(token_clause).limit(5).all()
                else:
                    sops = base.filter(
                        (SOP.sop_number.ilike(q_like)) |
                        (SOP.title.ilike(q_like)) |
                        (SOP.department.ilike(q_like))
                    ).limit(5).all()
                if not sops:
                    sops = base.order_by(SOP.updated_at.desc()).limit(5).all()

            for sop in sops:
                push_source(
                    sop.sop_number,
                    sop.title,
                    "sop",
                    f"SOP in department {sop.department or 'unknown'}."
                )
            if sops:
                answer_parts.append(
                    "SOP matches: " + ", ".join(f"{s.sop_number} ({s.title})" for s in sops)
                )

        # Deviations
        if wants_deviations:
            dev_refs = _extract_entity_refs(DEV_REF_PATTERN, question, chat_history)
            devs = []
            if dev_refs:
                for ref in dev_refs[:5]:
                    row = db.query(Deviation).filter(Deviation.deviation_number.ilike(ref)).first()
                    if row:
                        devs.append(row)
            else:
                token_clause = _tokenized_clause([Deviation.deviation_number, Deviation.title, Deviation.description_text])
                base = db.query(Deviation)
                if token_clause is not None:
                    devs = base.filter(token_clause).limit(5).all()
                else:
                    devs = base.filter(
                        (Deviation.deviation_number.ilike(q_like)) |
                        (Deviation.title.ilike(q_like)) |
                        (Deviation.description_text.ilike(q_like))
                    ).limit(5).all()
                if not devs:
                    devs = base.order_by(Deviation.updated_at.desc()).limit(5).all()
            for dev in devs:
                push_source(
                    dev.deviation_number,
                    dev.title,
                    "deviation",
                    f"Deviation status {dev.external_status or 'unknown'}, impact {dev.impact_level or 'unknown'}."
                )
            if devs:
                answer_parts.append(
                    "Deviation matches: " + ", ".join(f"{d.deviation_number} ({d.title})" for d in devs)
                )

        # CAPAs
        if wants_capas:
            capa_refs = _extract_entity_refs(CAPA_REF_PATTERN, question, chat_history)
            capas = []
            if capa_refs:
                for ref in capa_refs[:5]:
                    row = db.query(Capa).filter(Capa.capa_number.ilike(ref)).first()
                    if row:
                        capas.append(row)
            else:
                token_clause = _tokenized_clause([Capa.capa_number, Capa.title, Capa.action_text])
                base = db.query(Capa)
                if token_clause is not None:
                    capas = base.filter(token_clause).limit(5).all()
                else:
                    capas = base.filter(
                        (Capa.capa_number.ilike(q_like)) |
                        (Capa.title.ilike(q_like)) |
                        (Capa.action_text.ilike(q_like))
                    ).limit(5).all()
                if not capas:
                    capas = base.order_by(Capa.updated_at.desc()).limit(5).all()
            for capa in capas:
                push_source(
                    capa.capa_number,
                    capa.title,
                    "capa",
                    f"CAPA status {capa.external_status or 'unknown'}."
                )
            if capas:
                answer_parts.append(
                    "CAPA matches: " + ", ".join(f"{c.capa_number} ({c.title})" for c in capas)
                )

        # Audits
        if wants_audits:
            audit_refs = _extract_entity_refs(AUDIT_REF_PATTERN, question, chat_history)
            audits = []
            if audit_refs:
                for ref in audit_refs[:5]:
                    row = db.query(AuditFinding).filter(
                        (AuditFinding.audit_number.ilike(ref)) |
                        (AuditFinding.finding_number.ilike(ref))
                    ).first()
                    if row:
                        audits.append(row)
            else:
                token_clause = _tokenized_clause([AuditFinding.audit_number, AuditFinding.finding_number, AuditFinding.finding_text])
                base = db.query(AuditFinding)
                if token_clause is not None:
                    audits = base.filter(token_clause).limit(5).all()
                else:
                    audits = base.filter(
                        (AuditFinding.audit_number.ilike(q_like)) |
                        (AuditFinding.finding_number.ilike(q_like)) |
                        (AuditFinding.finding_text.ilike(q_like))
                    ).limit(5).all()
                if not audits:
                    audits = base.order_by(AuditFinding.updated_at.desc()).limit(5).all()
            for audit in audits:
                ref = audit.finding_number or audit.audit_number or "AUDIT"
                push_source(ref, ref, "audit", f"Audit finding status {audit.acceptance_status or 'unknown'}.")
            if audits:
                answer_parts.append(
                    "Audit matches: " + ", ".join((a.finding_number or a.audit_number or "AUDIT") for a in audits)
                )

        # Decisions
        if wants_decisions:
            dec_refs = _extract_entity_refs(DECISION_REF_PATTERN, question, chat_history)
            decisions = []
            if dec_refs:
                for ref in dec_refs[:5]:
                    row = db.query(Decision).filter(Decision.decision_number.ilike(ref)).first()
                    if row:
                        decisions.append(row)
            else:
                token_clause = _tokenized_clause([Decision.decision_number, Decision.title, Decision.decision_statement])
                base = db.query(Decision)
                if token_clause is not None:
                    decisions = base.filter(token_clause).limit(5).all()
                else:
                    decisions = base.filter(
                        (Decision.decision_number.ilike(q_like)) |
                        (Decision.title.ilike(q_like)) |
                        (Decision.decision_statement.ilike(q_like))
                    ).limit(5).all()
                if not decisions:
                    decisions = base.order_by(Decision.updated_at.desc()).limit(5).all()
            for dec in decisions:
                ref = dec.decision_number or dec.title or "DECISION"
                push_source(ref, dec.title, "decision", "Decision record matched in local database.")
            if decisions:
                answer_parts.append(
                    "Decision matches: " + ", ".join((d.decision_number or d.title or "Decision") for d in decisions)
                )

        if not citations:
            return {
                "answer": "No relevant local database records were found for this query.",
                "sources": [],
                "citations": [],
                "retrieval_debug": [],
                "suggestions": [
                    "Ask with an exact SOP/DEV/CAPA number",
                    "Try a shorter and more specific query",
                    "Use category-specific wording (SOP, deviation, CAPA, audit, decision)",
                ],
                "retrieval_stats": {"mode": "local-db", "hits": 0},
                "routed_to": "local-db",
            }

        return {
            "answer": " ".join(answer_parts),
            "sources": sources,
            "citations": citations,
            "retrieval_debug": [
                {
                    "rank": idx + 1,
                    "source_id": c.get("ref", ""),
                    "ref": c.get("ref", ""),
                    "title": c.get("title", ""),
                    "score": c.get("score", 1.0),
                    "type": c.get("type", ""),
                    "snippet": c.get("excerpt", ""),
                }
                for idx, c in enumerate(citations[:20])
            ],
            "suggestions": [
                "Ask for details of one returned record",
                "Ask for status and ownership of a returned item",
                "Ask for related SOP/deviation/CAPA links",
            ],
            "retrieval_stats": {"mode": "local-db", "hits": len(citations)},
            "routed_to": "local-db",
        }
    finally:
        db.close()


def _build_sop_db_fallback(question: str, chat_history: list[dict]) -> dict | None:
    sop_refs = _extract_sop_refs(question, chat_history)
    if not sop_refs:
        return None

    db = SessionLocal()
    try:
        hits = []
        for sop_ref in sop_refs[:3]:
            sop = db.query(SOP).filter(SOP.sop_number.ilike(sop_ref)).first()
            if not sop:
                continue

            version = None
            if sop.current_version_id:
                version = db.query(SOPVersion).filter(SOPVersion.id == sop.current_version_id).first()
            if not version:
                version = (
                    db.query(SOPVersion)
                    .filter(SOPVersion.sop_id == sop.id)
                    .order_by(SOPVersion.created_at.desc())
                    .first()
                )

            content_text = _clean_text(_extract_text_from_tiptap((version.content_json if version else {}) or {}))
            excerpt = content_text[:500]
            if len(content_text) > 500:
                excerpt += "..."

            hits.append({
                "sop_number": sop.sop_number,
                "title": sop.title,
                "status": (version.external_status if version else "") or "unknown",
                "version_number": (version.version_number if version else "") or "",
                "excerpt": excerpt or "No SOP body text available.",
            })

        if not hits:
            return None

        if len(hits) == 1:
            item = hits[0]
            answer = (
                f"{item['sop_number']} ({item['title']}) was found in the main SOP database. "
                f"Current status: {item['status']}."
            )
        else:
            refs = ", ".join(f"{item['sop_number']} ({item['title']})" for item in hits)
            answer = f"Found these SOP records in the main SOP database: {refs}."

        details = " ".join(
            f"{item['sop_number']}: {item['excerpt']}" for item in hits
        ).strip()
        if details:
            answer = f"{answer}\n\n{details}"

        citations = [
            {
                "ref": item["sop_number"],
                "title": item["title"],
                "type": "sop",
                "status": item["status"],
                "score": 1.0,
            }
            for item in hits
        ]

        sources = [
            {
                "id": item["sop_number"],
                "type": "sop",
                "label": item["title"] or item["sop_number"],
            }
            for item in hits
        ]

        return {
            "answer": answer,
            "sources": sources,
            "citations": citations,
            "retrieval_debug": [
                {
                    "rank": idx + 1,
                    "source_id": item["sop_number"],
                    "ref": item["sop_number"],
                    "title": item["title"],
                    "score": 1.0,
                    "type": "sop",
                    "snippet": item["excerpt"],
                }
                for idx, item in enumerate(hits[:20])
            ],
            "suggestions": [
                f"Summarize {hits[0]['sop_number']} responsibilities",
                f"Show procedure steps from {hits[0]['sop_number']}",
                "Ask for related deviations or CAPAs",
            ],
            "retrieval_stats": {"fallback": "postgres_sop_lookup", "hits": len(hits)},
            "routed_to": "db-fallback-sops",
        }
    finally:
        db.close()


def _build_context(payload: AIActionRequest) -> str:
    bits = []
    if payload.sop_title:
        bits.append(f"SOP title: {payload.sop_title}")
    if payload.section_name:
        bits.append(f"Section name: {payload.section_name}")
    if payload.section_type:
        bits.append(f"Section type: {payload.section_type}")
    return " | ".join(bits) if bits else "SOP context unavailable"


def _paragraph(text: str) -> str:
    return f"<p>{escape(text)}</p>"


def _build_prompt(action: str, payload: AIActionRequest) -> str:
    context = _build_context(payload)
    if action == "gap_check":
        return (
            "You are a Lead GMP/QA Compliance Auditor with expertise in ISO 9001:2015, ISO 13485:2016, "
            "FDA 21 CFR Parts 11 and 820, and EU GMP Annex 11.\n\n"
            f"DOCUMENT CONTEXT: {context}\n\n"
            "YOUR TASK: Perform a thorough compliance gap analysis on the SOP text below. "
            "Check for: (1) missing or incomplete procedure steps, (2) undefined responsibilities \u2014 "
            "roles must be named specifically, (3) undefined frequencies or timelines \u2014 no vague terms like "
            "'regularly' or 'as needed', (4) missing data integrity or access controls, (5) absent "
            "documentation requirements including record names and retention periods, (6) ambiguous language "
            "and undefined technical terms, (7) missing regulatory references where required.\n\n"
            f"TEXT TO ANALYZE:\n{payload.text}\n\n"
            "Return ONLY a valid JSON object structured as: "
            '{"gaps": [{"issue": "short label", "explanation": "why this fails GMP/regulatory requirements", '
            '"recommendation": "exact SOP-ready text to fix the gap"}], '
            '"section_assessed": "section name"}'
        )
    if action == "rewrite":
        return (
            "You are a senior GMP/QA technical writer with expertise in ISO 13485, FDA 21 CFR, and EU GMP Annex 11.\n\n"
            f"DOCUMENT CONTEXT: {context}\n\n"
            "YOUR TASK: Perform a complete, professional rewrite of the SOP text below. Apply these standards: "
            "(1) Use active voice and imperative verbs throughout. (2) Every sentence must name a specific role "
            "as the subject \u2014 never 'someone' or 'the team'. (3) Replace all vague qualifiers with specific "
            "values, frequencies, or defined conditions. (4) Ensure logical, chronological process order. "
            "(5) Use parallel structure in lists. (6) Add critical step callouts where safety or compliance is at risk.\n"
            "RULES: Do NOT add Purpose/Scope/Responsibilities/Procedure headings. Do NOT change the core topic. "
            "You MAY restructure sentences and reorder information for flow.\n\n"
            f"TEXT TO REWRITE:\n{payload.text}\n\n"
            "Return ONLY a valid JSON object: "
            '{"rewritten_text": "full rewritten text", '
            '"structural_changes": ["change 1", "change 2"], '
            '"rationale": "2-sentence explanation of compliance and clarity improvements"}'
        )
    if action == "improve":
        return (
            "You are a senior GMP/QA technical writer specializing in regulatory SOP documentation.\n\n"
            f"DOCUMENT CONTEXT: {context}\n\n"
            "YOUR TASK: Make targeted, high-quality improvements to the SOP text below. Apply these criteria: "
            "(1) Fix all grammar, punctuation, and spelling errors. (2) Replace passive voice with active voice. "
            "(3) Replace vague qualifiers ('appropriate', 'as needed') with specific, measurable language. "
            "(4) Ensure responsibilities are attributed to named roles. (5) Make language imperative and unambiguous.\n"
            "STRICT RULES: Do NOT add SOP headings or restructure into a full SOP. Do NOT change factual content or meaning. "
            "Make only the smallest meaningful improvements required.\n\n"
            f"TEXT TO IMPROVE:\n{payload.text}\n\n"
            "Return ONLY a valid JSON object: "
            '{"improved_text": "the improved text", '
            '"changes_made": ["specific change 1", "specific change 2"], '
            '"compliance_note": "one sentence explaining the GMP/quality improvement achieved"}'
        )
    raise HTTPException(status_code=400, detail=f"Action '{action}' is not supported.")


def _render_gap_check(structured_data: dict) -> str:
    return (
        f"<h3>Issue</h3>{_paragraph(structured_data['issue'])}"
        f"<h3>Explanation</h3>{_paragraph(structured_data['explanation'])}"
        f"<h3>Recommendation</h3>{_paragraph(structured_data['recommendation'])}"
    )


def _render_rewrite(structured_data: dict) -> str:
    steps = "".join(f"<li>{escape(step)}</li>" for step in structured_data["procedure"])
    return (
        f"<h2>Purpose</h2>{_paragraph(structured_data['purpose'])}"
        f"<h2>Scope</h2>{_paragraph(structured_data['scope'])}"
        f"<h2>Responsibilities</h2>{_paragraph(structured_data['responsibilities'])}"
        f"<h2>Procedure</h2><ol>{steps}</ol>"
        f"<h2>Documentation</h2>{_paragraph(structured_data['documentation'])}"
    )


def _render_improve(structured_data: dict) -> str:
    return (
        f"<h3>Improved Version</h3>{_paragraph(structured_data['improved_version'])}"
        f"<h3>Reason for Improvement</h3>{_paragraph(structured_data['reason_for_improvement'])}"
    )


def _action_output_token_budget(input_chars: int) -> int:
    """
    Long selected text needs a larger output budget; JSON + improved copy can exceed 1–2k tokens.
    """
    cap = int(os.getenv("ACTION_MAX_OUTPUT_TOKENS_CAP", "8192"))
    if input_chars <= 0:
        return int(os.getenv("ACTION_MAX_OUTPUT_TOKENS") or "4096")
    return min(cap, max(2048, int(input_chars * 0.45) + 1200))


def _call_action_llm(runtime: Any, prompt: str, *, input_char_budget: int = 0) -> str:
    parser = StrOutputParser()
    n = _action_output_token_budget(input_char_budget) if input_char_budget else int(
        os.getenv("ACTION_MAX_OUTPUT_TOKENS") or "4096"
    )
    try:
        return (runtime.llm.bind(max_tokens=n) | parser).invoke(prompt)
    except Exception:
        return (runtime.fallback_llm.bind(max_tokens=n) | parser).invoke(prompt)


def _render_dynamic_text(text: str) -> str:
    cleaned = _normalize_gap_check_analysis_text(text or "")
    lines = [line.strip() for line in re.split(r"\r?\n+", cleaned) if line.strip()]
    if not lines:
        return "<p>No suggestion returned.</p>"
    return "".join(f"<p>{escape(line)}</p>" for line in lines)


def _normalize_gap_check_analysis_text(text: str) -> str:
    t = text or ""
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"(?m)^\s*#+\s*", "", t)
    t = t.replace("**", "")
    t = re.sub(r"(?m)^\s*---+\s*$", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _render_gap_check_analysis_html(analysis: str) -> str:
    # Keep compatibility with chatbot.routes implementation so this shim
    # never fails with NameError when gap_check is requested.
    normalized = _normalize_gap_check_analysis_text(analysis)
    if not normalized:
        return "<p>No suggestion returned.</p>"
    return _render_dynamic_text(normalized)


def _render_dynamic_gap_check(gaps: list[dict[str, str]]) -> str:
    if not gaps:
        return "<p>No compliance gaps identified for the selected text.</p>"
    return "".join(
        (
            f"<h3>Issue</h3>{_paragraph(gap.get('issue', ''))}"
            f"<h3>Explanation</h3>{_paragraph(gap.get('explanation', ''))}"
            f"<h3>Recommendation</h3>{_paragraph(gap.get('recommendation', ''))}"
        )
        for gap in gaps
    )


def _build_action_request(payload: AIActionRequest) -> ActionRequest:
    return ActionRequest(
        document_id=payload.sop_title or "editor-document",
        section_id=(payload.section_name or "selected-text").lower().replace(" ", "-"),
        sop_title=payload.sop_title or "Untitled SOP",
        section_title=payload.section_name or "Selected text",
        section_type=payload.section_type or "Selected Text",
        section_text=payload.text,
    )


def _build_gap_check_retrieval_query(request: ActionRequest) -> str:
    parts = [
        f"SOP: {request.sop_title}",
        f"Section: {request.section_title}",
        f"Type: {request.section_type}",
        request.section_text,
    ]
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _run_dynamic_ai_action(payload: AIActionRequest, action: str) -> AIActionResponse:
    runtime = _get_action_runtime()
    request = _build_action_request(payload)
    ch_budget = len(request.section_text or "")

    if action == "gap_check":
        retrieval_query = _build_gap_check_retrieval_query(request)
        raw_docs = runtime.retriever.invoke(retrieval_query)
        reranked = runtime.reranker.rerank_top_n(retrieval_query, raw_docs, 3)
        context = format_chunks(reranked)
    else:
        # improve / rewrite: no RAG — system prompt + rules + document fields + section text only
        context = IMPROVE_REWRITE_NO_RAG_CONTEXT

    if action == "improve":
        prompt = build_improve_prompt(request, context)
        parsed = parse_with_retry(
            raw=_call_action_llm(runtime, prompt, input_char_budget=ch_budget),
            schema=ImproveResponse,
            prompt=prompt,
            call_llm=lambda rp: _call_action_llm(
                runtime, rp, input_char_budget=ch_budget
            ),
            audit_log=[],
        )
        return AIActionResponse(
            action="improve",
            original_text=request.section_text,
            suggested_text=_render_dynamic_text(parsed.improved_text),
            explanation="Text verbessert / Text improved.",
            structured_data={
                "improved_text": parsed.improved_text,
                "improved_version": parsed.improved_text,
            },
        )

    if action == "rewrite":
        prompt = build_rewrite_prompt(request, context)
        parsed = parse_with_retry(
            raw=_call_action_llm(runtime, prompt, input_char_budget=ch_budget),
            schema=RewriteResponse,
            prompt=prompt,
            call_llm=lambda rp: _call_action_llm(
                runtime, rp, input_char_budget=ch_budget
            ),
            audit_log=[],
        )
        return AIActionResponse(
            action="rewrite",
            original_text=request.section_text,
            suggested_text=_render_dynamic_text(parsed.rewritten_text),
            explanation="Text neu formuliert / Text rewritten.",
            structured_data={
                "rewritten_text": parsed.rewritten_text,
            },
        )

    if action == "gap_check":
        prompt = build_gap_check_prompt(request, context)
        parsed = parse_with_retry(
            raw=_call_action_llm(runtime, prompt, input_char_budget=ch_budget),
            schema=GapCheckResponse,
            prompt=prompt,
            call_llm=lambda rp: _call_action_llm(
                runtime, rp, input_char_budget=ch_budget
            ),
            audit_log=[],
        )
        return AIActionResponse(
            action="gap_check",
            original_text=request.section_text,
            suggested_text=_render_gap_check_analysis_html(parsed.analysis),
            explanation="Compliance-Lückenanalyse abgeschlossen / Compliance gap analysis completed.",
            structured_data={
                "analysis": parsed.analysis,
            },
        )

    raise HTTPException(status_code=400, detail=f"Action '{action}' is not supported.")


def _fallback_gap_check(payload: AIActionRequest) -> AIActionResponse:
    runtime = _get_action_runtime()
    request = _build_action_request(payload)
    ch_budget = len(request.section_text or "")
    prompt = build_gap_check_prompt(request, "Kein relevanter Kontext verfügbar. / No relevant context found.")
    raw = _call_action_llm(runtime, prompt, input_char_budget=ch_budget)
    parsed = parse_with_retry(
        raw=raw,
        schema=GapCheckResponse,
        prompt=prompt,
        call_llm=lambda rp: _call_action_llm(runtime, rp, input_char_budget=ch_budget),
        audit_log=[],
    )
    return AIActionResponse(
        action="gap_check",
        original_text=_clean_text(payload.text),
        suggested_text=_render_gap_check_analysis_html(parsed.analysis),
        explanation="Compliance-Lückenanalyse abgeschlossen / Compliance gap analysis completed.",
        structured_data={"analysis": parsed.analysis},
    )


def _fallback_rewrite(payload: AIActionRequest) -> AIActionResponse:
    runtime = _get_action_runtime()
    request = _build_action_request(payload)
    ch_budget = len(request.section_text or "")
    prompt = build_rewrite_prompt(request, IMPROVE_REWRITE_NO_RAG_CONTEXT)
    raw = _call_action_llm(runtime, prompt, input_char_budget=ch_budget)
    parsed = parse_with_retry(
        raw=raw,
        schema=RewriteResponse,
        prompt=prompt,
        call_llm=lambda rp: _call_action_llm(runtime, rp, input_char_budget=ch_budget),
        audit_log=[],
    )
    return AIActionResponse(
        action="rewrite",
        original_text=_clean_text(payload.text),
        suggested_text=_render_dynamic_text(parsed.rewritten_text),
        explanation="Text neu formuliert / Text rewritten.",
        structured_data={"rewritten_text": parsed.rewritten_text},
    )


def _fallback_improve(payload: AIActionRequest) -> AIActionResponse:
    runtime = _get_action_runtime()
    request = _build_action_request(payload)
    ch_budget = len(request.section_text or "")
    prompt = build_improve_prompt(request, IMPROVE_REWRITE_NO_RAG_CONTEXT)
    raw = _call_action_llm(runtime, prompt, input_char_budget=ch_budget)
    parsed = parse_with_retry(
        raw=raw,
        schema=ImproveResponse,
        prompt=prompt,
        call_llm=lambda rp: _call_action_llm(runtime, rp, input_char_budget=ch_budget),
        audit_log=[],
    )
    return AIActionResponse(
        action="improve",
        original_text=_clean_text(payload.text),
        suggested_text=_render_dynamic_text(parsed.improved_text),
        explanation="Text verbessert / Text improved.",
        structured_data={"improved_text": parsed.improved_text},
    )


def _extract_selected_text_html(action: str, structured_data: dict, suggested_text: str) -> str:
    if action == "rewrite":
        return _render_dynamic_text(structured_data.get("rewritten_text") or suggested_text)
    if action == "improve":
        return _render_dynamic_text(structured_data.get("improved_text") or suggested_text)
    return suggested_text


def _ctx_list(values: Any) -> list:
    return values if isinstance(values, list) else []


def _extract_refs(items: list, keys: list[str], limit: int = 8) -> list[str]:
    refs: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = str(item.get(key) or "").strip()
            if value:
                refs.append(value)
                break
        if len(refs) >= limit:
            break
    return refs


def _query_intents(question: str) -> set[str]:
    q = (question or "").lower()
    intents: set[str] = set()
    if re.search(r"\b(how many|count|number of|total)\b.*\b(sop|sops)\b", q):
        intents.add("sop_count")
    if re.search(r"\b(list|show|which|what)\b.*\b(sop|sops)\b", q):
        intents.add("sop_list")
    if re.search(r"\b(summarize|summary|brief|gist)\b", q):
        intents.add("summary")
    if re.search(r"\b(compare|difference|vs|versus)\b", q):
        intents.add("compare")
    if re.search(r"\b(linked|related)\b.*\b(capa|capas|audit|audits|decision|decisions|deviation|deviations)\b", q):
        intents.add("linked")
    if re.search(r"\b(this sop|current sop|active sop)\b", q):
        intents.add("active_sop")
    if re.search(r"\b(which|what)\b.*\b(sop)\b.*\b(currently open|open now|opened|active)\b", q):
        intents.add("active_sop")
    return intents


def _summarize_live_context(assistant_context: dict | None, question: str = "") -> str:
    ctx = assistant_context or {}
    current = ctx.get("current_sop") if isinstance(ctx.get("current_sop"), dict) else {}
    linked = ctx.get("linked_context") if isinstance(ctx.get("linked_context"), dict) else {}
    tabs = _ctx_list(ctx.get("opened_tabs"))
    text = str(ctx.get("editor_excerpt") or "").strip()
    references = _ctx_list(current.get("references"))
    intents = _query_intents(question)
    linked_devs = _extract_refs(_ctx_list(linked.get("deviations")), ["deviation_number", "ref_number", "id"])
    linked_capas = _extract_refs(_ctx_list(linked.get("capas")), ["capa_number", "ref_number", "id"])
    linked_audits = _extract_refs(_ctx_list(linked.get("audits")), ["finding_number", "audit_number", "ref_number", "id"])
    linked_decisions = _extract_refs(_ctx_list(linked.get("decisions")), ["decision_number", "ref_number", "id"])
    related_sops = _extract_refs(_ctx_list(linked.get("related_sops")), ["sop_number", "ref_number", "id"])
    open_sop_tabs = _extract_refs(tabs, ["docId", "label"], limit=10)
    include_editor_excerpt = bool(text) and bool({"summary", "active_sop", "compare"} & intents)
    excerpt = text[:1200] if include_editor_excerpt else ""
    focus_note = ""
    active_sop_ref = str(current.get("sop_number") or current.get("id") or "").strip()
    if "summary" in intents and active_sop_ref:
        focus_note = f"- Focus SOP for summary: {active_sop_ref}\n"
    elif "compare" in intents and open_sop_tabs:
        focus_note = f"- Compare candidates from open tabs: {', '.join(open_sop_tabs[:6])}\n"
    return (
        "LIVE_ASSISTANT_CONTEXT\n"
        f"- Active SOP: {current.get('sop_number') or current.get('id') or 'unknown'} | "
        f"title={current.get('title') or 'unknown'} | version={current.get('version') or 'unknown'} | "
        f"status={current.get('status') or 'unknown'}\n"
        f"- Linked deviations: {len(_ctx_list(linked.get('deviations')))} ({', '.join(linked_devs) or 'none'})\n"
        f"- Linked CAPAs: {len(_ctx_list(linked.get('capas')))} ({', '.join(linked_capas) or 'none'})\n"
        f"- Linked audits: {len(_ctx_list(linked.get('audits')))} ({', '.join(linked_audits) or 'none'})\n"
        f"- Linked decisions: {len(_ctx_list(linked.get('decisions')))} ({', '.join(linked_decisions) or 'none'})\n"
        f"- Related SOPs: {len(_ctx_list(linked.get('related_sops')))} ({', '.join(related_sops) or 'none'})\n"
        f"- Open tabs: {len(tabs)}\n"
        f"{focus_note}"
        f"- References in editor metadata: {', '.join(str(r) for r in references[:10]) or 'none'}\n"
        f"- Editor text excerpt: {excerpt if excerpt else 'not injected for this query intent'}"
    )


def _build_live_context_answer(intents: set[str], assistant_context: dict | None) -> str | None:
    ctx = assistant_context or {}
    current = ctx.get("current_sop") if isinstance(ctx.get("current_sop"), dict) else {}
    linked = ctx.get("linked_context") if isinstance(ctx.get("linked_context"), dict) else {}

    if "active_sop" in intents:
        sop_ref = str(current.get("sop_number") or current.get("id") or "").strip()
        if not sop_ref:
            return None
        title = str(current.get("title") or "unknown").strip() or "unknown"
        version = str(current.get("version") or "unknown").strip() or "unknown"
        status = str(current.get("status") or "unknown").strip() or "unknown"
        return (
            f"The currently open SOP is {sop_ref}. "
            f"Title: {title}. Version: {version}. Status: {status}."
        )

    if "linked" in intents:
        dev_refs = _extract_refs(_ctx_list(linked.get("deviations")), ["deviation_number", "ref_number", "id"], limit=12)
        capa_refs = _extract_refs(_ctx_list(linked.get("capas")), ["capa_number", "ref_number", "id"], limit=12)
        audit_refs = _extract_refs(_ctx_list(linked.get("audits")), ["finding_number", "audit_number", "ref_number", "id"], limit=12)
        if not (dev_refs or capa_refs or audit_refs):
            return None
        return (
            f"Linked Deviations ({len(dev_refs)}): {', '.join(dev_refs) if dev_refs else 'none'}.\n"
            f"Linked CAPAs ({len(capa_refs)}): {', '.join(capa_refs) if capa_refs else 'none'}.\n"
            f"Linked Audits ({len(audit_refs)}): {', '.join(audit_refs) if audit_refs else 'none'}."
        )

    return None


def _resolve_sop_from_context(db, assistant_context: dict | None, question: str) -> SOP | None:
    ctx = assistant_context or {}
    current = ctx.get("current_sop") if isinstance(ctx.get("current_sop"), dict) else {}
    for raw_id in [current.get("id"), current.get("sop_number"), ctx.get("current_document_id")]:
        value = str(raw_id or "").strip()
        if not value:
            continue
        try:
            doc_uuid = uuid.UUID(value)
            sop = db.query(SOP).filter(SOP.id == doc_uuid, SOP.is_active == True).first()  # noqa: E712
        except ValueError:
            sop = db.query(SOP).filter(SOP.sop_number.ilike(value), SOP.is_active == True).first()  # noqa: E712
        if sop:
            return sop
    match = SOP_REF_PATTERN.search(question or "")
    if match:
        return db.query(SOP).filter(
            SOP.sop_number.ilike(match.group(0).upper()),
            SOP.is_active == True,  # noqa: E712
        ).first()
    return None


def _title_from_question(question: str) -> str:
    q = (question or "").strip()
    m = re.search(r"(?:title|named|called)\s*[:\-]?\s*([A-Za-z0-9 _\-/]{4,120})", q, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    for_match = re.search(r"\bfor\s+([A-Za-z0-9 _\-/]{3,120})", q, re.IGNORECASE)
    if for_match:
        core = for_match.group(1).strip(" .")
        if core:
            return f"{core.title()} SOP"
    cleaned = re.sub(r"\b(create|new|add|generate|draft)\b", "", q, flags=re.IGNORECASE).strip(" :.-")
    cleaned = re.sub(r"\b(an?|the)\b", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\bsop\b", "", cleaned, flags=re.IGNORECASE).strip(" :.-")
    return cleaned[:120] if cleaned else "Untitled SOP"


def _build_minimal_tiptap_doc(text: str) -> dict:
    content = str(text or "").strip()
    lines = [line.strip() for line in re.split(r"\r?\n+", content) if line.strip()]
    if not lines:
        lines = ["New SOP draft"]
    blocks = [
        {"type": "paragraph", "content": [{"type": "text", "text": line[:1200]}]}
        for line in lines[:80]
    ]
    return {
        "type": "doc",
        "content": blocks,
    }


def _plan_sop_action(question: str, assistant_context: dict | None) -> dict | None:
    q = (question or "")
    if ACTION_INTENT_DELETE.search(q):
        preview_target = {}
        ctx = assistant_context or {}
        current = ctx.get("current_sop") if isinstance(ctx.get("current_sop"), dict) else {}
        preview_target["sop_number"] = current.get("sop_number") or current.get("documentId") or ""
        preview_target["title"] = current.get("title") or ""
        return {"type": "delete_sop", "requires_confirmation": True, "target": preview_target}
    if ACTION_INTENT_CREATE.search(q):
        return {"type": "create_sop", "requires_confirmation": False}
    if ACTION_INTENT_UPDATE.search(q):
        mode = "append" if re.search(r"\badd\b.*\bsection\b", q, re.IGNORECASE) else "replace"
        return {"type": "update_sop", "requires_confirmation": False, "mode": mode}
    return None


def _execute_sop_action(
    action_type: str,
    question: str,
    assistant_context: dict | None,
    generated_text: str = "",
    mode: str = "replace",
) -> dict | None:
    if not action_type:
        return None
    db = SessionLocal()
    try:
        if action_type == "delete_sop":
            sop = _resolve_sop_from_context(db, assistant_context, question)
            if not sop:
                return {"type": "delete_sop", "ok": False, "message": "No active SOP could be resolved for deletion."}
            logger.info("[assistant-delete] requested sop_id=%s sop_number=%s current_is_active=%s", sop.id, sop.sop_number, sop.is_active)
            sop.is_active = False
            db.commit()
            db.refresh(sop)
            logger.info("[assistant-delete] committed sop_id=%s sop_number=%s persisted_is_active=%s", sop.id, sop.sop_number, sop.is_active)
            return {
                "type": "delete_sop",
                "ok": True,
                "sop_id": str(sop.id),
                "sop_number": sop.sop_number,
                "message": f"SOP {sop.sop_number} was soft deleted.",
            }

        if action_type == "update_sop":
            sop = _resolve_sop_from_context(db, assistant_context, question)
            if not sop:
                return {"type": "update_sop", "ok": False, "message": "No active SOP could be resolved for update."}
            current = (
                db.query(SOPVersion).filter(SOPVersion.id == sop.current_version_id).first()
                if sop.current_version_id else
                db.query(SOPVersion).filter(SOPVersion.sop_id == sop.id).order_by(SOPVersion.created_at.desc()).first()
            )
            if not current:
                return {"type": "update_sop", "ok": False, "message": "Current SOP version not found."}
            current_text = _extract_text_from_tiptap((current.content_json or {}))
            llm_text = str(generated_text or "").strip()
            if mode == "append" and llm_text:
                next_text = f"{current_text}\n\n{llm_text}".strip()
            else:
                next_text = llm_text or current_text
            current.content_json = _build_minimal_tiptap_doc(next_text[:18000])
            db.commit()
            return {
                "type": "update_sop",
                "ok": True,
                "sop_id": str(sop.id),
                "sop_number": sop.sop_number,
                "message": f"SOP {sop.sop_number} was updated.",
            }

        if action_type == "create_sop":
            title = _title_from_question(question)
            sop_number = f"SOP-{uuid.uuid4().hex[:8].upper()}"
            while db.query(SOP).filter(SOP.sop_number == sop_number).first():
                sop_number = f"SOP-{uuid.uuid4().hex[:8].upper()}"
            sop_id = uuid.uuid4()
            ver_id = uuid.uuid4()
            tenant_row = db.query(SOP.tenant_id).first()
            tenant_id = tenant_row[0] if tenant_row else uuid.UUID("00000000-0000-0000-0000-000000000000")
            sop = SOP(
                id=sop_id,
                tenant_id=tenant_id,
                sop_number=sop_number,
                title=title,
                department="Quality",
                is_active=True,
                current_version_id=ver_id,
            )
            draft_text = str(generated_text or "").strip()
            version = SOPVersion(
                id=ver_id,
                sop_id=sop_id,
                version_number="1",
                external_status="draft",
                content_json=_build_minimal_tiptap_doc(draft_text[:8000]),
                metadata_json={"sopStatus": "draft", "sopMetadata": {"title": title, "documentId": sop_number}},
            )
            db.add(sop)
            db.add(version)
            db.commit()
            return {
                "type": "create_sop",
                "ok": True,
                "sop_id": str(sop_id),
                "sop_number": sop_number,
                "title": title,
                "message": f"Created new SOP {sop_number}.",
            }
    finally:
        db.close()
    return None


@ai_router.post("/api/ai/action", response_model=AIActionResponse)
async def perform_ai_action(payload: AIActionRequest):
    """
    Perform a structured AI action on selected SOP text.
    The current implementation uses deterministic structured generation so the
    frontend can reliably support compare-and-confirm workflows.
    """
    action = _normalize_action(payload.action)
    payload.text = _clean_text(payload.text)
    if not payload.text:
        raise HTTPException(status_code=422, detail="Selected text is required.")

    try:
        return await asyncio.to_thread(_run_dynamic_ai_action, payload, action)
    except HTTPException:
        raise
    except Exception:
        if action == "gap_check":
            return _fallback_gap_check(payload)
        if action == "rewrite":
            return _fallback_rewrite(payload)
        if action == "improve":
            return _fallback_improve(payload)

    raise HTTPException(status_code=400, detail=f"Action '{payload.action}' is not supported.")


@ai_router.post("/api/ai/query")
async def query_ai(payload: dict):
    """
    Chatbot query endpoint integrated from the standalone chatbot module.
    """
    question = (payload.get("question") or payload.get("query") or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question is required")

    category = payload.get("category")
    chat_history = payload.get("chat_history") or []
    surface = str(payload.get("surface") or "unknown").strip().lower()
    route = str(payload.get("route") or "").strip()
    t0 = time.perf_counter()
    cfg = get_local_llm_config()
    logger.info(
        "[chatbot-request] surface=%s route=%s provider=%s model=%s category=%s qlen=%s",
        surface,
        route,
        cfg.provider,
        cfg.model,
        category or "auto",
        len(question),
    )
    print(
        f"[chatbot-request] surface={surface} route={route or '-'} provider={cfg.provider} model={cfg.model} category={category or 'auto'} qlen={len(question)}",
        flush=True,
    )
    assistant_context = payload.get("assistant_context") or {}
    assistant_action_confirmation = payload.get("assistant_action_confirmation") or {}
    intents = _query_intents(question)
    context_summary = _summarize_live_context(assistant_context, question)
    action_plan = _plan_sop_action(question, assistant_context)
    action_result = None
    pending_confirmation = (
        isinstance(action_plan, dict)
        and action_plan.get("type") == "delete_sop"
        and action_plan.get("requires_confirmation")
    )
    question_for_rag = question
    context_hints: list[str] = []
    current_sop = assistant_context.get("current_sop") if isinstance(assistant_context.get("current_sop"), dict) else {}
    active_ref = str(current_sop.get("sop_number") or current_sop.get("id") or "").strip()
    logger.info(
        "[chatbot-intent] surface=%s intents=%s active_ref=%s",
        surface,
        sorted(intents),
        active_ref or "none",
    )
    print(
        f"[chatbot-intent] surface={surface} intents={sorted(intents)} active_ref={active_ref or 'none'}",
        flush=True,
    )
    if ("active_sop" in intents) and active_ref:
        category = "sops"
    if "summary" in intents and active_ref:
        context_hints.append(f"ACTIVE_SOP={active_ref}")
    if "linked" in intents:
        context_hints.append("INTENT=LINKED_ENTITIES")
    if "compare" in intents:
        context_hints.append("INTENT=COMPARE_SOPS")
    if ("active_sop" in intents) and active_ref:
        context_hints.append(f"FOCUS_REF={active_ref}")
    live_context_answer = _build_live_context_answer(intents, assistant_context)
    if live_context_answer:
        response = {
            "answer": live_context_answer,
            "sources": [],
            "citations": [],
            "retrieval_debug": [],
            "suggestions": [],
            "retrieval_stats": {
                "provider": cfg.provider,
                "model": cfg.model,
                "source": "live_context",
                "surface": surface,
                "intents": sorted(intents),
                "latency_ms_total": round((time.perf_counter() - t0) * 1000.0, 1),
            },
            "routed_to": "live-context",
            "assistant_action": action_result or action_plan,
        }
        logger.info(
            "[chatbot-response] source=live_context surface=%s intents=%s latency_ms=%.1f",
            surface,
            sorted(intents),
            (time.perf_counter() - t0) * 1000.0,
        )
        return response
    if context_hints:
        question_for_rag = f"{question_for_rag}\n\nRAG_HINTS: {' | '.join(context_hints)}"
    elif action_plan:
        # Include tiny context summary only for action-intent alignment.
        question_for_rag = f"{question_for_rag}\n\n{context_summary[:500]}"
    if action_plan:
        question_for_rag = (
            f"{question_for_rag}\n\n"
            f"PLANNED_ASSISTANT_ACTION: {action_plan}\n"
            "Use this planned action and live context while answering."
        )

    # RAG is the default source of truth. Local DB primary mode is opt-in only
    # for diagnostics and should not be used in normal semantic chatbot flow.
    allow_local_db_bypass = bool(payload.get("allow_local_db_primary")) and CHATBOT_USE_LOCAL_DB and CHATBOT_ALLOW_LOCAL_DB_PRIMARY
    if allow_local_db_bypass:
        # Run in a worker thread so SQLAlchemy work does not block the event loop
        # (avoids piling up slow requests, nginx timeouts, and a stuck-feeling UI).
        response = await asyncio.to_thread(
            _build_local_db_chat_response, question_for_rag, chat_history, category
        )
        logger.info(
            "[chatbot-response] source=local-db-primary latency_ms=%.1f",
            (time.perf_counter() - t0) * 1000.0,
        )
        return response

    try:
        rag = await asyncio.wait_for(
            asyncio.to_thread(_get_smart_rag_chain),
            timeout=CHAT_QUERY_TIMEOUT_SECONDS,
        )
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    rag.invoke,
                    question_for_rag,
                    category,
                    chat_history,
                ),
                timeout=CHAT_QUERY_TIMEOUT_SECONDS,
            )
        except Exception as first_exc:
            if _is_prompt_too_large_error(first_exc) and question_for_rag != question:
                logger.warning(
                    "[chatbot-request] prompt too large; retrying compact query path"
                )
                compact_history = (chat_history or [])[-4:]
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        rag.invoke,
                        question,
                        category,
                        compact_history,
                    ),
                    timeout=CHAT_QUERY_TIMEOUT_SECONDS,
                )
            else:
                raise
    except (TimeoutError, asyncio.TimeoutError):
        raise HTTPException(
            status_code=504,
            detail="RAG request timed out. Please retry with a shorter or more specific query.",
        )
    except Exception as exc:
        if is_local_llm_unreachable_error(exc):
            logger.error(
                "[chatbot-response] source=error reason=llm_unreachable latency_ms=%.1f error=%s",
                (time.perf_counter() - t0) * 1000.0,
                str(exc),
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Local LLM server unreachable ({cfg.base_url}, model={cfg.model}). "
                    "Please ensure the local model service is running."
                ),
            )
        raise HTTPException(status_code=500, detail=f"Chatbot query failed: {exc}")

    def _json_safe_citations(cits: list) -> list:
        out = []
        for c in cits or []:
            if not isinstance(c, dict):
                continue
            d = dict(c)
            s = d.get("score", 0.0)
            try:
                f = float(s)
                d["score"] = f if math.isfinite(f) else 0.0
            except (TypeError, ValueError):
                d["score"] = 0.0
            out.append(d)
        return out

    citations = _json_safe_citations(result.get("citations", []))
    sources = []
    for idx, c in enumerate(citations):
        ref = c.get("ref") or c.get("title") or f"source-{idx+1}"
        label = c.get("title") or c.get("ref") or "Source"
        source_type = (c.get("type") or "doc").lower()
        sources.append({"id": ref, "type": source_type, "label": label})

    response = {
        "answer": result.get("answer", ""),
        "sources": sources,
        "citations": citations,
        "retrieval_debug": result.get("retrieval_debug", []),
        "suggestions": result.get("suggestions", []),
        "retrieval_stats": result.get("retrieval_stats", {}),
        "routed_to": result.get("routed_to", ""),
        "assistant_action": action_result or action_plan,
    }

    if pending_confirmation and not bool(assistant_action_confirmation.get("confirmed")):
        response["assistant_action"] = {
            **(action_plan or {}),
            "requires_confirmation": True,
        }
        response["answer"] = (
            "I can delete the active SOP, but I need your confirmation first. "
            "Please confirm deletion in the in-app modal."
        )
        return response

    if action_plan and action_plan.get("type") == "delete_sop" and bool(assistant_action_confirmation.get("confirmed")):
        action_result = await asyncio.to_thread(
            _execute_sop_action, "delete_sop", question, assistant_context, "", "replace"
        )
        response["assistant_action"] = action_result
        if action_result and action_result.get("ok"):
            response["answer"] = (
                f"{response.get('answer', '')}\n\n"
                f"Action completed: {action_result.get('message', 'SOP deleted.')}"
            ).strip()
        return response

    if action_plan and action_plan.get("type") in {"create_sop", "update_sop"}:
        llm_generated = result.get("answer", "")
        mode = action_plan.get("mode", "replace")
        action_result = await asyncio.to_thread(
            _execute_sop_action,
            action_plan["type"],
            question,
            assistant_context,
            llm_generated,
            mode,
        )
        response["assistant_action"] = action_result
        if action_result and action_result.get("ok"):
            response["answer"] = (
                f"{response.get('answer', '')}\n\n"
                f"Action completed: {action_result.get('message', 'Done.')}"
            ).strip()

    answer_text = (response.get("answer") or "").strip().lower()
    rag_weak = (
        not answer_text
        or "no relevant information found" in answer_text
        or "do not contain sufficient detail" in answer_text
    )
    if rag_weak:
        response["answer"] = "Sorry, I do not have enough information about this."
        response["citations"] = []
        response["sources"] = []
        response["retrieval_debug"] = []

    response.setdefault("retrieval_stats", {})
    response["retrieval_stats"].update(
        {
            "provider": cfg.provider,
            "model": cfg.model,
            "source": "rag",
            "surface": surface,
            "intents": sorted(intents),
            "latency_ms_total": round((time.perf_counter() - t0) * 1000.0, 1),
        }
    )
    logger.info(
        "[chatbot-response] source=rag routed_to=%s citations=%s latency_ms=%.1f",
        response.get("routed_to", ""),
        len(citations),
        (time.perf_counter() - t0) * 1000.0,
    )
    print(
        f"[chatbot-response] source=rag routed_to={response.get('routed_to', '')} citations={len(citations)} latency_ms={(time.perf_counter() - t0) * 1000.0:.1f}",
        flush=True,
    )
    return response
