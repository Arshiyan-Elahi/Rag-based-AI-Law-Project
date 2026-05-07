"""
chain/rag_chain.py

Two chain classes:
  - HybridRAGChain     : original single-collection chain (backward compat.)
  - SmartRAGChain      : routes query to relevant collections only, returns
                         clean prose answer + citations + dynamic suggestions.
"""

import time
import re
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Literal

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, MessagesPlaceholder
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document

from retrieval.hybrid_retriever import HybridRetriever
from retrieval.reranker import CrossEncoderReranker
from retrieval.context_builder import build_context
from retrieval.federated_retriever import FederatedRetriever
from retrieval.hybrid_retriever import rag_unified_enabled
from retrieval.query_router import route_query, describe_route
from retrieval.llm_router import LLMRouter
import os
from dotenv import load_dotenv
from app.database import SessionLocal
from app.models import SOP, Deviation, Capa, AuditFinding, Decision

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


MAX_QUERY_CHARS = int(os.getenv("GEMINI_MAX_QUERY_CHARS", "4000"))
MAX_CONTEXT_CHARS = int(os.getenv("GEMINI_MAX_CONTEXT_CHARS", "12000"))
MAX_HISTORY_MESSAGE_CHARS = int(os.getenv("GEMINI_MAX_HISTORY_MESSAGE_CHARS", "800"))
MAX_HISTORY_MESSAGES = int(os.getenv("GEMINI_MAX_HISTORY_MESSAGES", "8"))
RAG_DEBUG_RETRIEVAL = os.getenv("RAG_DEBUG_RETRIEVAL", "true").strip().lower() == "true"
RAG_DEBUG_MAX_CHUNKS = int(os.getenv("RAG_DEBUG_MAX_CHUNKS", "8"))


def _json_safe_float(v, default: float = 0.0) -> float:
    """Finite floats only; JSON cannot encode inf, -inf, or nan."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return x


def _sanitize_citation_list(cits: List[dict]) -> List[dict]:
    out: List[dict] = []
    for c in cits or []:
        if not isinstance(c, dict):
            continue
        d = dict(c)
        d["score"] = round(_json_safe_float(d.get("score", 0.0)), 4)
        out.append(d)
    return out


def _truncate_text(text: str, limit: int) -> str:
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _debug_chunk_summary(doc: Document, idx: int) -> str:
    meta = doc.metadata or {}
    ref = str(meta.get("ref_number") or meta.get("source_id") or f"chunk-{idx}")
    title = str(meta.get("title") or "")
    section = str(meta.get("_section") or meta.get("entity_type") or "unknown")
    score = _json_safe_float(meta.get("rerank_score", 0.0))
    snippet = _truncate_text((doc.page_content or "").replace("\n", " ").strip(), 220)
    return (
        f"[rag-debug] chunk#{idx} section={section} ref={ref} "
        f"title=\"{title}\" score={score:.4f} text=\"{snippet}\""
    )


def _build_retrieval_debug_rows(docs: List[Document], limit: int = 20) -> List[dict]:
    rows: List[dict] = []
    for i, doc in enumerate(docs[:max(0, limit)], 1):
        meta = doc.metadata or {}
        rows.append(
            {
                "rank": i,
                "section": str(meta.get("_section") or meta.get("entity_type") or ""),
                "source_id": str(meta.get("source_id") or meta.get("entity_id") or ""),
                "ref": str(meta.get("ref_number") or ""),
                "title": str(meta.get("title") or ""),
                "score": round(_json_safe_float(meta.get("rerank_score", 0.0)), 4),
                "status": str(meta.get("status") or ""),
                "snippet": _truncate_text((doc.page_content or "").replace("\n", " ").strip(), 280),
            }
        )
    return rows


# ─────────────────────────────────────────────
# Shared LLM
# ─────────────────────────────────────────────
def get_llm(temperature: float = 0.2) -> ChatGoogleGenerativeAI:
    max_tokens = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "4096"))
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        temperature=temperature,
        max_output_tokens=max_tokens,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        max_retries=6,
        thinking_budget=1024,
    )


def get_fallback_llm(temperature: float = 0.2) -> ChatGoogleGenerativeAI:
    max_tokens = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "4096"))
    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-flash")
    return ChatGoogleGenerativeAI(
        model=fallback_model,
        temperature=temperature,
        max_output_tokens=max_tokens,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        max_retries=3,
        thinking_budget=1024,
    )


# ─────────────────────────────────────────────
# ORIGINAL SINGLE-COLLECTION CHAIN (unchanged)
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are SOPSearch AI - a compliance assistant for SOPs and regulatory processes.
Answer from context only. Be concise. If not found say: "Information not available in the knowledge base."
Do NOT fabricate document numbers or dates.
"""
USER_PROMPT = "## Context\n{context}\n\n## Question\n{question}\n\nAnswer:"


class HybridRAGChain:
    def __init__(self, retriever: HybridRetriever, reranker: CrossEncoderReranker):
        self.retriever = retriever
        self.reranker  = reranker
        self.llm = get_llm()
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT), ("human", USER_PROMPT),
        ])

    def invoke(self, query: str, category_filter: str = None) -> dict:
        self.retriever.category_filter = category_filter
        raw  = self.retriever.invoke(query)
        rnk  = self.reranker.rerank(query, raw)
        ctx, cits = build_context(rnk)
        ans = (self.prompt | self.llm | StrOutputParser()).invoke({"context": ctx, "question": query})
        return {"answer": ans, "citations": cits, "num_docs_retrieved": len(raw), "num_docs_reranked": len(rnk)}


# ─────────────────────────────────────────────────────────────────
# SMART RAG CHAIN — routes to relevant collection(s) only
# ─────────────────────────────────────────────────────────────────

SMART_SYSTEM = """\
You are a precise, bilingual QMS/IT Compliance AI Assistant integrated with a
production Hybrid RAG system.

You have access to a structured Qdrant vector database with the following SEPARATE
collections. You MUST search the correct collection based on the user's intent:

================================================================
COLLECTION MAP
================================================================

Collection: "sops"
  → Contains : Standard Operating Procedures (SOPs)
  → Fields   : sop_number, title, department, sop_content,
                version_number, effective_date, review_date, status
  → Trigger keywords: "SOP", "procedure", "standard", "policy",
    "how to", "zugriffsmanagement", "patch", "firewall", "notfall",
    "KI-Systeme", "governance"

Collection: "deviations"
  → Contains : Deviation records and incidents
  → Fields   : deviation_number, title, description_text,
                root_cause_text, impact_level, external_status, event_date
  → Trigger keywords: "deviation", "incident", "issue", "problem",
    "DEV-", "breach", "excursion", "fehler", "abweichung", "kritisch"

Collection: "sop_versions"
  → Contains : Specific version content of SOPs
  → Fields   : version_number, content_json, effective_date,
                review_date, external_version_id, external_status
  → Trigger keywords: "version", "current version", "v4", "effective",
    "latest revision", "content of", "what does SOP say"

Collection: "capas"
  Contains: Corrective and Preventive Actions
  Fields: capa_number, title, action_text, external_status, effectiveness
  Triggers: "CAPA", "corrective action", "preventive action"

Collection: "audits"
  Contains: Audit findings
  Fields: finding / audit identifiers, finding_text, acceptance_status
  Triggers: "audit", "finding", "inspection", "AUDIT-"

Collection: "decisions"
  Contains: Decisions, rationales, conclusions
  Fields: decision_number, title, decision_statement, rationale_text, final_conclusion
  Triggers: "decision", "rationale", "conclusion", "approval", "DEC-"

================================================================
RULES YOU MUST ALWAYS FOLLOW
================================================================

RULE 1 — COLLECTION ROUTING
Before answering, explicitly identify which collection(s) to search.
Never merge data from deviations into SOPs or vice versa unless the user
explicitly asks for a cross-reference.

RULE 2 — EXACT POINT MATCHING
When the user mentions a specific identifier (e.g., "SOP-IT-001",
"DEV-IT-401", "DEV-2026-103"), you MUST filter on that exact field value.
Do not rely on semantic similarity alone.
Use metadata filter: { "sop_number": "SOP-IT-001" }
                  or { "deviation_number": "DEV-IT-401" }

RULE 3 — CHAIN OF THOUGHT
Before generating your final answer, you MUST perform and show a brief
reasoning block tagged as [REASONING]. In this block:
  (a) identify what the user is asking
  (b) decide which collection to search
  (c) identify any exact identifiers to filter on
  (d) plan your answer structure
Then produce your [ANSWER].

RULE 4 — CITATIONS
Every factual claim in your answer MUST be linked to its source record
using this format: [SOP-IT-001], [DEV-IT-401], [SOP-QA-010 v4.0]
Never state a fact without a citation tag.
If you cannot cite it, do not state it.

RULE 5 — CONVERSATION MEMORY
You have access to the full conversation history. When the user says
"that deviation", "the one we just discussed", "same SOP", "previous answer"
— you MUST resolve the reference from earlier in the conversation history.
Never ask the user to repeat what they already told you.

RULE 6 — IMPACT LEVEL AWARENESS
When discussing deviations, always surface the impact_level in your answer.
Priority order: Critical > Major > Moderate > Minor
Flag Critical and Major deviations explicitly with a ⚠️ marker.

RULE 7 — BILINGUAL HANDLING
This system contains both German and English documents.
If the user asks in English about a German SOP title, translate the intent
correctly and search both languages.
Return the answer in the same language the user asked in.

RULE 8 — STATUS AWARENESS
Always report the current status of records:
  - Deviations  : open | under_investigation | closed
  - SOP versions: effective | draft | obsolete
Never present a closed deviation or obsolete SOP version as currently active.

RULE 9 — CROSS-REFERENCE DETECTION
If the user asks about a deviation, check if a related SOP exists that
governs that area.
Example: DEV-IT-101 → SOP-IT-001 (OT access management)
Proactively surface this link as: [RELATED SOP: SOP-IT-001]

RULE 10 — REFUSAL RULE
If the retrieved context does not contain enough information to answer
confidently, say:
"The available records do not contain sufficient detail to answer this
question. Please check [collection name] or provide more context."
Never hallucinate fields, dates, or root causes that are null or missing
in the data.
"""

SMART_USER = """\
{history_focus}

────────────────────────────────────────
CONVERSATION HISTORY:
(Carried in the message list before this user turn; use it for follow-ups.)

────────────────────────────────────────
RETRIEVED CONTEXT:
{context}

────────────────────────────────────────
USER QUESTION:
{question}

────────────────────────────────────────
INSTRUCTIONS FOR THIS RESPONSE:

STEP 1 — [REASONING]  (required; always show this block first)
Answer each point briefly before [ANSWER]:
  • What is the user asking? (one sentence)
  • Which collection(s) does the retrieved context correspond to, and why?
  • Any exact ID in the question or history? Which field/record?
  • Any reference to earlier messages to resolve?
  • Impact level / status for the records involved (if applicable)?
  • Any cross-collection links to surface?

STEP 2 — [ANSWER]
  • Answer directly and completely.
  • Cite every fact with bracket notation, e.g.
    [SOP-IT-001], [DEV-IT-401], [CAPA-22], [AUDIT-7], [DEC-15]
  • For deviations with impact_level Critical or Major, start that bullet or
    sentence with the warning emoji (warning marker).
  • If a related SOP governs the topic, add a line:
    [RELATED SOP: SOP-XX-XXX — title]
  • If version or effective date appears in the context, you may include it
    in the citation line, e.g. [SOP-QA-010 v4.0 | effective: YYYY-MM-DD]

  For non-trivial answers, use this structure (plain text, no markdown tables):
  Summary: one short paragraph
  Details: bullet lines, each with citations
  Status: current status / impact when known from context
  Cross-refs: related SOPs, deviations, CAPAs, audits, or decisions if grounded in context

  Do not use markdown headings (no #), bold, tables, or code fences.
  Stay within 400 words unless the user explicitly asks for full detail.
  End the [ANSWER] section with a line:
  Sources: list every cited record ID in brackets, comma-separated
  (You may prefix that line with 📎 for example: "📎 Sources: [SOP-IT-001], [DEV-IT-401]")

STEP 3 — [CONFIDENCE]
  One line, e.g.:
  [CONFIDENCE] HIGH — exact record aligned with an identifier in context;
  or MEDIUM — semantic match, recommend verification;
  or LOW — insufficient data; refusal rule applies.

────────────────────────────────────────
FORMAT RULES
  Do not use vague phrasing like "the document mentions" when you can name
  [SOP-…] or [DEV-…] from context.
  Do not present null or missing fields as if they were populated.
  Do not use markdown headings, bold markers, tables, or code fences.

After [CONFIDENCE], you MUST append the following machine-readable blocks
exactly (the application parses them). List each cited source once in
---CITATIONS---; then three to four follow-up questions in JSON.

---CITATIONS---
[[REF_ID|Document Title|Type|One sentence excerpt]]
[[REF_ID|Document Title|Type|One sentence excerpt]]

---SUGGESTIONS---
["Follow-up using record IDs from context", "Second follow-up", "Third follow-up"]
"""


def _build_unified_context(docs: List[Document], prefix_label: str) -> Tuple[str, List[dict]]:
    """Build a numbered context string from retrieved docs, regardless of collection."""
    if not docs:
        return "", []

    parts, raw_cits = [], []
    total = 0
    MAX = MAX_CONTEXT_CHARS

    for i, doc in enumerate(docs):
        text = doc.page_content.strip()
        if not text or total + len(text) > MAX:
            break

        meta     = doc.metadata
        ref      = meta.get("ref_number", "")
        title    = meta.get("title", "")
        doc_type = meta.get("doc_type", prefix_label)
        status   = meta.get("status", "")

        header_parts = [f"[{i}]", doc_type.upper()]
        if ref:    header_parts.append(ref)
        if title:  header_parts.append(f'"{title}"')
        if status: header_parts.append(f"({status})")
        header = " ".join(header_parts)

        parts.append(f"{header}\n{text}")
        raw_cits.append({
            "ref":    ref or f"#{i}",
            "title":  title,
            "type":   doc_type,
            "status": status,
            "score":  round(_json_safe_float(meta.get("rerank_score", 0.0)), 4),
        })
        total += len(text)

    return "\n\n---\n\n".join(parts), raw_cits


def _unique_by_source(docs: List[Document], limit: int, max_per_source: int = 3) -> List[Document]:
    """
    Keep top documents while allowing multiple chunks per source_id/ref.
    This prevents one document from dominating context while ensuring we get
    more than just the header/title page of a document.
    """
    out: List[Document] = []
    counts = {}  # {key: count}
    for doc in docs:
        meta = doc.metadata or {}
        key = meta.get("source_id") or meta.get("ref_number") or meta.get("title")
        if not key:
            key = id(doc)
            
        current_count = counts.get(key, 0)
        if current_count >= max_per_source:
            continue
            
        counts[key] = current_count + 1
        out.append(doc)
        
        if len(out) >= limit:
            break
    return out


def _parse_answer_citations_suggestions(raw: str) -> Tuple[str, List[dict], List[str], str, str]:
    """
    Parse the LLM output into:
      answer     : clean prose text from [ANSWER] block
      citations  : list of dicts extracted from [[REF|TITLE|TYPE|EXCERPT]] tags
      suggestions: list of strings from the ---SUGGESTIONS--- block
      reasoning  : text from [REASONING] block
      confidence : text from [CONFIDENCE] block
    """
    answer      = ""
    citations   = []
    suggestions = []
    reasoning   = ""
    confidence  = ""

    # 1. Extract ---SUGGESTIONS---
    sug_match = re.search(r'---SUGGESTIONS---\s*(\[.*?\])', raw, re.DOTALL | re.IGNORECASE)
    if sug_match:
        try:    suggestions = json.loads(sug_match.group(1))
        except: suggestions = []
        raw = raw[:sug_match.start()].strip()

    # 2. Extract Citations using Tag Format: [[ref|title|type|excerpt]]
    cit_marker = "---CITATIONS---"
    if cit_marker in raw:
        parts = raw.split(cit_marker)
        raw_content = parts[0].strip()
        cit_text = parts[1].strip()
        
        # Match [[ ... | ... | ... | ... ]]
        matches = re.findall(r'\[\[(.*?)\|(.*?)\|(.*?)\|(.*?)\]\]', cit_text)
        for ref, title, doc_type, excerpt in matches:
            citations.append({
                "ref":     ref.strip(),
                "title":   title.strip(),
                "type":    doc_type.strip(),
                "excerpt": excerpt.strip()
            })
    else:
        raw_content = raw.strip()

    # 3. Extract [REASONING], [ANSWER], [CONFIDENCE] blocks
    # Looking for blocks started by bracketed headers
    reason_match = re.search(r'\[REASONING\](.*?)(?=\[ANSWER\]|\[CONFIDENCE\]|$)', raw_content, re.DOTALL | re.IGNORECASE)
    if reason_match:
        reasoning = reason_match.group(1).strip()
    
    answer_match = re.search(r'\[ANSWER\](.*?)(?=\[CONFIDENCE\]|\[REASONING\]|$)', raw_content, re.DOTALL | re.IGNORECASE)
    if answer_match:
        answer = answer_match.group(1).strip()
    else:
        # Fallback if no specific block found, use everything but reasoning/confidence
        answer = raw_content

    conf_match = re.search(r'\[CONFIDENCE\](.*?)$', raw_content, re.DOTALL | re.IGNORECASE)
    if conf_match:
        confidence = conf_match.group(1).strip()

    # Clamp suggestions
    suggestions = [s for s in suggestions if isinstance(s, str)][:4]

    return answer, citations, suggestions, reasoning, confidence


from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.prompts import HumanMessagePromptTemplate, MessagesPlaceholder


def _looks_like_sop_generation_query(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return False
    sop_terms = r"\b(sop|standard operating procedure|procedure document|work instruction)\b"
    intent_terms = (
        r"\b(generate|create|draft|write|prepare|convert|format|structure|"
        r"make this|turn this|build)\b"
    )
    # Generation mode should only trigger on explicit create/draft intent.
    # Multiline/context-heavy assistant prompts can otherwise cause false positives.
    return bool(re.search(sop_terms, q)) and bool(re.search(intent_terms, q))


def _build_sop_generation_prompt(raw_input: str) -> str:
    return f"""You are a senior SOP technical writer for regulated environments.

TASK
Transform the raw user input into a complete, production-ready SOP document.

RAW INPUT
{raw_input}

OUTPUT REQUIREMENTS
1) Output ONLY the SOP body in clean plain text (no markdown headings, no code fences).
2) Use professional, concise, domain-appropriate language.
3) Build a logical, complete hierarchy with numbered sections and subsections.
4) Numbering style must be consistent (e.g., 1.0, 1.1, 1.2 ... 2.0 ...).
5) Include these core sections when relevant:
   - Title
   - Purpose
   - Scope
   - Responsibilities
   - Procedure
6) Add additional sections when context requires them, for example:
   - Definitions
   - Safety / Precautions
   - Compliance / Regulatory References
   - Records / Documentation
   - Deviations / Exceptions
   - Revision History
7) Do NOT force irrelevant sections. If a section is not relevant, omit it.
8) In Procedure, provide clear ordered steps and substeps with role ownership where possible.
9) Resolve fragmented/raw notes into polished paragraphs and structured bullets.
10) Keep terminology and tone consistent throughout.

QUALITY BAR
- The SOP must be ready to paste directly into an editor with minimal/no formatting edits.
- Avoid placeholders like TBD unless absolutely necessary.
"""


def _classify_sop_inventory_query(query: str) -> Optional[Literal["count", "list"]]:
    """
    Detects SOP inventory questions so we can return a deterministic count/list
    without LLM drift. "count" = how many; "list" = enumerate SOPs.
    """
    q = (query or "").lower()
    if not re.search(
        r"\b(sop|sops|standard operating procedures?)\b",
        q,
        re.IGNORECASE,
    ):
        return None
    has_list_intent = bool(
        re.search(
            r"\b(list all|list every|show all|show me all|get all|name all|enumerate|all sops|every sop)\b",
            q,
        )
    ) or bool(re.search(r"\b(list|show)\b.+\b(sop|sops)\b", q)) or bool(
        re.search(r"\b(which|what) sops\b", q)
    )
    has_count_intent = any(
        p in q
        for p in (
            "how many",
            "how much",
            "number of",
            "total",
            "count ",
            " count",
            "quantity",
            "sop count",
        )
    )
    if re.search(r"\bkitne\b", q):
        has_count_intent = True
    if re.search(r"\b(how many sops|count sops|sop count|number of sops|total sops)\b", q):
        has_count_intent = True
    if re.search(
        r"\b(do we have|have we|is there|are there)\b", q
    ) and re.search(r"\b(sop|sops)\b", q):
        has_count_intent = True

    if has_list_intent and not has_count_intent:
        return "list"
    if has_count_intent and not has_list_intent:
        return "count"
    if has_list_intent and has_count_intent:
        if re.search(r"\bhow many\b", q) or re.search(
            r"\b(number|count|total) of\b", q
        ):
            return "count"
        return "list"
    if re.search(
        r"\b(available|exist|in the (system|index|database))\b", q
    ) and re.search(r"\bwhich\b.*\b(sop|sops)\b", q):
        return "list"
    if re.search(r"\b(available|exist|inventory)\b", q) and re.search(
        r"\b(how many|count|number)\b", q
    ):
        return "count"
    return None


def _looks_cross_domain_query(query: str) -> bool:
    q = (query or "").lower()
    return bool(
        re.search(
            r"\b(deviation|deviations|dev-|capa|capas|audit|audits|finding|findings|decision|decisions|linked|related)\b",
            q,
        )
    )


def _strict_sop_inventory_response(
    docs: List[Document],
    query: str,
    retriever: HybridRetriever | None = None,
    mode: Literal["count", "list"] = "list",
) -> dict:
    """Build deterministic SOP inventory from the SOP section corpus (deduped by SOP id)."""
    inventory_docs: List[Document] = list(docs or [])
    allowed_ids: set[str] = set()
    if retriever is not None:
        mf = getattr(retriever, "metadata_filters", {}) or {}
        raw_ids = mf.get("allowed_entity_ids") if isinstance(mf, dict) else None
        if isinstance(raw_ids, list):
            allowed_ids = {str(v) for v in raw_ids}
    if retriever is not None:
        try:
            corpus_docs, _ = retriever._get_bm25_corpus()
            if corpus_docs:
                if allowed_ids:
                    inventory_docs = [
                        d for d in corpus_docs
                        if str((d.metadata or {}).get("entity_id", "")) in allowed_ids
                    ]
                else:
                    inventory_docs = corpus_docs
        except Exception:
            pass

    rows: List[Tuple[str, str, str]] = []
    seen: set = set()
    for doc in inventory_docs:
        meta = doc.metadata or {}
        et = str(meta.get("entity_type", "")).lower()
        if rag_unified_enabled() and et and et != "sop":
            continue
        ref = (
            (meta.get("ref_number") or meta.get("sop_number") or meta.get("source_id"))
            or ""
        )
        if not ref and meta.get("entity_id"):
            ref = f"id:{str(meta.get('entity_id'))[:8]}"
        title = meta.get("title") or "Untitled SOP"
        status = meta.get("status") or "Unknown"
        page_content = (doc.page_content or "").strip()

        if (not ref or ref.startswith("id:")) and page_content:
            first_line = page_content.splitlines()[0].strip()
            if " - " in first_line:
                maybe_ref, maybe_title = first_line.split(" - ", 1)
                if maybe_ref.strip() and not maybe_ref.strip().lower().startswith(
                    "id:"
                ):
                    ref = maybe_ref.strip()
                if maybe_title.strip() and title == "Untitled SOP":
                    title = maybe_title.strip()

        eid = str(meta.get("entity_id") or "").lower()
        dedupe_key = f"{eid}|{(ref or '').lower()}" if eid else f"r|{(ref or title).lower()}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        display_ref = ref or title
        rows.append((display_ref, title, status))

    rows = sorted(rows, key=lambda x: (x[0] or "").lower())
    # Use live PostgreSQL scoped IDs when available for authoritative SOP count.
    total = len(allowed_ids) if allowed_ids else len(rows)
    list_cap = int(os.getenv("SOP_INVENTORY_LIST_MAX", "50"))

    if mode == "count":
        if total == 0:
            count_answer = (
                "No active SOP records were found in the current indexed dataset.\n\n"
                "If SOPs were recently added, run indexing and ask again."
            )
        else:
            sample = ", ".join(f"{ref} ({title})" for ref, title, _ in rows[:5])
            count_answer = (
                f"There are {total} active SOP record(s) in the indexed dataset.\n\n"
                f"Top matches: {sample}."
            )
        return {
        "answer": count_answer,
        "citations": [
            {
                "ref": f"INDEX-SOP-COUNT({total})",
                "title": "Indexed SOP inventory",
                "type": "sop",
                "excerpt": f"Distinct SOPs in SOP index: {total}.",
            }
        ],
        "suggestions": [
            "List all SOPs with titles and status",
            "What does SOP-IT-001 cover?",
            "Which SOPs mention access control?",
        ],
        "retrieval_stats": {},
        "routed_to": "SOPs (strict count)",
        "cached": False,
        "metadata_snapshot": [],
        "audit_log_snapshot": [],
        "action_metadata": {
            "query": query,
            "routing": ["sops"],
            "latency_ms": 0.0,
            "timestamp": time.time(),
            "model": "deterministic",
            "strict_mode": "sop_inventory_count",
        },
    }

    key_points = "\n".join(
        [f"- {ref}: {title} [{status}]" for ref, title, status in rows[:list_cap]]
    )
    if total > list_cap:
        key_points += f"\n- … and {total - list_cap} more (truncated; increase SOP_INVENTORY_LIST_MAX to show more in list mode)."
    sources_lines = "\n".join(
        [f"- {ref}: {title} (SOP)" for ref, title, _ in rows[:list_cap]]
    )
    citations = [
        {"ref": ref, "title": title, "type": "SOP", "excerpt": f"Status: {status}"}
        for ref, title, status in rows[: list_cap * 2]
    ][:200]
    suggestions = [
        "How many SOPs are in the index?",
        "Show details for a specific SOP by number",
        "Find SOPs related to access control",
    ]

    answer = (
        f"Found {total} active SOP record(s).\n\n"
        f"SOP list:\n{key_points if key_points else 'No SOPs found in the current index.'}\n\n"
        f"Sources:\n{sources_lines if sources_lines else 'None.'}"
    )

    return {
        "answer": answer,
        "citations": citations,
        "suggestions": suggestions,
        "retrieval_stats": {},
        "routed_to": "SOPs",
        "cached": False,
        "metadata_snapshot": [],
        "audit_log_snapshot": [],
        "action_metadata": {
            "query": query,
            "routing": ["sops"],
            "latency_ms": 0.0,
            "timestamp": time.time(),
            "model": os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            "strict_mode": "sop_inventory",
        },
    }


class SmartRAGChain:
    """
    Intelligent RAG chain that:
      1. Routes the query to the relevant collection(s) only.
      2. Does Hybrid Search (Dense + BM25) + Cross-Encoder reranking.
      3. Injects chat history for multi-turn memory + CoT reasoning.
      4. Returns: clean prose answer | citations | dynamic suggestions.
    """

    def __init__(self, federated_retriever: FederatedRetriever):
        self.federated = federated_retriever
        self.llm = get_llm()
        self.router = LLMRouter(llm=self.llm)
        self._active_ids_cache: dict[str, tuple[datetime, list[str]]] = {}
        self.prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=SMART_SYSTEM),
            MessagesPlaceholder(variable_name="chat_history_messages"),
            HumanMessagePromptTemplate.from_template(SMART_USER),
        ])

    def _get_active_entity_ids(self, section: str) -> list[str]:
        cache_ttl = int(os.getenv("RAG_ACTIVE_IDS_CACHE_SECONDS", "30"))
        now = datetime.utcnow()
        cached = self._active_ids_cache.get(section)
        if cached and (now - cached[0]) < timedelta(seconds=cache_ttl):
            return cached[1]

        db = SessionLocal()
        try:
            if section == "sops":
                ids = [str(row[0]) for row in db.query(SOP.id).filter(SOP.is_active == True).all()]  # noqa: E712
            elif section == "deviations":
                ids = [str(row[0]) for row in db.query(Deviation.id).all()]
            elif section == "capas":
                ids = [str(row[0]) for row in db.query(Capa.id).all()]
            elif section == "audits":
                ids = [str(row[0]) for row in db.query(AuditFinding.id).all()]
            elif section == "decisions":
                ids = [str(row[0]) for row in db.query(Decision.id).all()]
            else:
                ids = []
        finally:
            db.close()

        self._active_ids_cache[section] = (now, ids)
        return ids


    def _extract_metadata_filters(self, query: str) -> dict:
        """
        Extracts department or specific document reference filters from the query.
        Example: 'IT/sops' -> {'department': 'IT'}
        Example: 'SOP-IT-001' -> {'ref_number': 'SOP-IT-001'}
        """
        filters = {}
        q = query.upper()
        
        # 1. Department pattern (e.g. IT/sops, HR documents)
        dept_match = re.search(r'\b(IT|HR|FINANCE|QUALITY|COMPLIANCE|SECURITY|OPS|LEGAL)\b', q)
        if dept_match:
            filters["department"] = dept_match.group(1)
            
        # 2. Document ID pattern (e.g. SOP-xxx, DEV-xxx)
        id_match = re.search(r'\b(SOP|DEV|CAPA|AUDIT|DEC)-[A-Z0-9-]+\b', q)
        if id_match:
            filters["ref_number"] = id_match.group(0)
            
        return filters

    def _find_active_doc_id(self, chat_history: List[Dict]) -> str:
        """Scan last 2-3 messages in history for any document IDs (SOP, DEV, etc)."""
        if not chat_history:
            return ""
        
        # Scan in reverse, looking for document ID patterns
        pattern = re.compile(r'\b(SOP|DEV|CAPA|AUDIT|DEC)-[A-Z0-9-]+\b', re.IGNORECASE)
        for msg in reversed(chat_history[-4:]):
            content = msg.get("content", "")
            match = pattern.search(content)
            if match:
                return match.group(0).upper()
        return ""

    def _generate_structured_sop(self, user_input: str) -> str:
        prompt = _build_sop_generation_prompt(_truncate_text(user_input, MAX_QUERY_CHARS))
        parser = StrOutputParser()
        try:
            return (self.llm | parser).invoke(prompt).strip()
        except Exception:
            fallback_llm = get_fallback_llm()
            return (fallback_llm | parser).invoke(prompt).strip()

    def invoke(self, query: str, category: str = None, chat_history: List[Dict] = None) -> dict:
        t0 = time.time()
        if _looks_like_sop_generation_query(query):
            sop_text = self._generate_structured_sop(query)
            return {
                "answer": sop_text,
                "reasoning": "",
                "confidence": "HIGH — direct SOP authoring mode from user-provided notes.",
                "citations": [],
                "retrieval_debug": [],
                "suggestions": [
                    "Review role assignments for each procedure step",
                    "Add organization-specific compliance references",
                    "Request a shorter version for training use",
                ],
                "retrieval_stats": {
                    "searched": [],
                    "per_section": {},
                    "total_docs": 0,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "authoring_mode": "sop_generation",
                },
                "routed_to": "sop-generation",
                "cached": False,
                "metadata_snapshot": [],
                "audit_log_snapshot": [],
                "action_metadata": {
                    "query": _truncate_text(query, MAX_QUERY_CHARS),
                    "routing": ["sop-generation"],
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "timestamp": time.time(),
                    "model": os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
                },
            }

        cat_norm = (category or "").strip().lower()
        route_data = self.router.route(query)
        sop_inventory_mode: Optional[Literal["count", "list"]] = None
        if (not cat_norm) or cat_norm == "sops":
            sop_inventory_mode = _classify_sop_inventory_query(query)

        # ── Step 0: Extract Metadata Filters & Active Doc ID ──
        metadata_filters = self._extract_metadata_filters(query)
        active_doc_id = self._find_active_doc_id(chat_history) if chat_history else ""
        if active_doc_id and not sop_inventory_mode:
            print(f"  [context] identified active doc from history: {active_doc_id}")
            is_sop_query = any(
                k in (query or "").lower() for k in ["sop", "procedure", "standard"]
            )
            if active_doc_id.startswith("SOP") and is_sop_query:
                metadata_filters["ref_number"] = active_doc_id
        if sop_inventory_mode == "count" and not re.search(
            r"\bSOP-[A-Z0-9-]+\b", query or "", re.IGNORECASE
        ):
            metadata_filters.pop("ref_number", None)

        print(
            f"  [filters] extracted: {metadata_filters} | sop_inventory_mode: {sop_inventory_mode}"
        )

        # ── Step 1: Route query using LLM Router (Prompt 3) ──
        forced_category = category and category.strip().lower() in {
            "sops",
            "deviations",
            "capas",
            "audits",
            "decisions",
        }
        if forced_category and not _looks_cross_domain_query(query):
            target_sections = [category.strip().lower()]
            route_data = {"collections": target_sections, "exact_filters": dict(metadata_filters)}
        elif sop_inventory_mode:
            target_sections = ["sops"]
            route_data = {"collections": ["sops"], "exact_filters": dict(metadata_filters)}
        else:
            target_sections = route_data.get("collections", [])
            metadata_filters.update(route_data.get("exact_filters", {}))

        routed_label = describe_route(target_sections)
        print(f"  [router] '{query[:60]}' -> {target_sections} | filters: {metadata_filters}")

        if sop_inventory_mode:
            sop_retriever = self.federated.retrievers.get("sops")
            if sop_retriever:
                section_filters = dict(metadata_filters or {})
                section_filters["allowed_entity_ids"] = self._get_active_entity_ids("sops")
                sop_retriever.metadata_filters = section_filters
                if rag_unified_enabled():
                    sop_retriever.category_filter = "sops"
                strict_resp = _strict_sop_inventory_response(
                    [],
                    query,
                    sop_retriever,
                    mode=sop_inventory_mode,
                )
                strict_resp["retrieval_stats"] = {
                    "searched": ["sops"],
                    "per_section": {"sops": 0},
                    "total_docs": 0,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "strict_mode": True,
                }
                return strict_resp

        # ── Step 2: Hybrid search on targeted collections only ──
        all_docs: List[Document] = []
        per_section_counts: Dict[str, int] = {}

        for section in target_sections:
            retriever = self.federated.retrievers.get(section)
            if not retriever:
                continue
            try:
                # Apply metadata filters (if any)
                section_filters = dict(metadata_filters or {})
                # Strictly constrain vector hits to currently existing entities in PostgreSQL
                # so stale Qdrant points cannot leak into chatbot responses.
                section_filters["allowed_entity_ids"] = self._get_active_entity_ids(section)
                retriever.metadata_filters = section_filters
                if rag_unified_enabled():
                    retriever.category_filter = section
                else:
                    retriever.category_filter = None
                # Deep retrieval: fetch 30 to allow for deduplication/diversification
                docs = retriever.invoke(query)
                allowed_ids = set(section_filters.get("allowed_entity_ids") or [])
                if allowed_ids:
                    qdrant_ids = [
                        str((d.metadata or {}).get("entity_id") or (d.metadata or {}).get("source_id") or "")
                        for d in docs
                    ]
                    docs = [
                        d
                        for d in docs
                        if str((d.metadata or {}).get("entity_id") or (d.metadata or {}).get("source_id") or "") in allowed_ids
                    ]
                    if RAG_DEBUG_RETRIEVAL:
                        print(
                            f"[rag-debug] section='{section}' db_ids={len(allowed_ids)} qdrant_ids={qdrant_ids[:RAG_DEBUG_MAX_CHUNKS]} kept={len(docs)}",
                            flush=True,
                        )
                if RAG_DEBUG_RETRIEVAL:
                    print(
                        f"[rag-debug] query='{query[:120]}' section='{section}' raw_hits={len(docs)}",
                        flush=True,
                    )
                
                # Rerank within this section
                top_n = 20 if len(target_sections) == 1 else 10
                ranked = self.federated.reranker.rerank_top_n(query, docs, top_n)
                
                # Deduplicate but allow more content depth (3-4 chunks per source)
                # If we have a single targeted document, we can afford more depth.
                max_chunks = 6 if active_doc_id else 4
                unique_limit = 15 if len(target_sections) == 1 else 8
                
                ranked = _unique_by_source(ranked, unique_limit, max_per_source=max_chunks)
                
                # Tag each doc with its section
                for d in ranked:
                    d.metadata["_section"] = section
                all_docs.extend(ranked)
                per_section_counts[section] = len(ranked)
                if RAG_DEBUG_RETRIEVAL and ranked:
                    for i, doc in enumerate(ranked[:RAG_DEBUG_MAX_CHUNKS], 1):
                        print(_debug_chunk_summary(doc, i), flush=True)
            except Exception as e:
                print(f"  [router] Warning: retrieval failed for '{section}': {e}")
                per_section_counts[section] = 0

        if not all_docs:
            if sop_inventory_mode:
                strict_resp = _strict_sop_inventory_response(
                    [],
                    query,
                    self.federated.retrievers.get("sops"),
                    mode=sop_inventory_mode,
                )
                strict_resp["retrieval_stats"] = {
                    "searched": target_sections,
                    "per_section": per_section_counts,
                    "total_docs": 0,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "strict_mode": True,
                }
                return strict_resp
            return {
                "answer": "No relevant information found in the knowledge base for your query.",
                "citations": [],
                "suggestions": [
                    "Ask about a specific SOP number",
                    "Search for related deviations",
                    "Check CAPA status",
                ],
                "retrieval_stats": {
                    "searched": target_sections,
                    "total_docs": 0,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                },
                "routed_to": routed_label,
            }

        if sop_inventory_mode:
            strict_resp = _strict_sop_inventory_response(
                all_docs,
                query,
                self.federated.retrievers.get("sops"),
                mode=sop_inventory_mode,
            )
            strict_resp["retrieval_stats"] = {
                "searched": target_sections,
                "per_section": per_section_counts,
                "total_docs": len(all_docs),
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "strict_mode": True,
            }
            return strict_resp

        # ── Step 3: Build unified context ──
        context_str, raw_cits = _build_unified_context(all_docs, "document")

        # ── Step 3b: Format chat history for CoT continuity ──
        chat_history_messages = []
        if chat_history:
            for msg in chat_history:
                role = msg.get("role")
                content = msg.get("content", "").strip()
                if role == "assistant":
                    content = _truncate_text(content, MAX_HISTORY_MESSAGE_CHARS)
                    chat_history_messages.append(AIMessage(content=content))
                else:
                    content = _truncate_text(content, MAX_HISTORY_MESSAGE_CHARS)
                    chat_history_messages.append(HumanMessage(content=content))

            if len(chat_history_messages) > MAX_HISTORY_MESSAGES:
                chat_history_messages = chat_history_messages[-MAX_HISTORY_MESSAGES:]

        # ── Step 4: LLM generation ──
        query = _truncate_text(query, MAX_QUERY_CHARS)
        context_str = _truncate_text(context_str, MAX_CONTEXT_CHARS)
        try:
            history_focus = f"HISTORY FOCUS: Priority should be given to {active_doc_id} as it was discussed recently." if active_doc_id else ""
            raw_answer = (self.prompt | self.llm | StrOutputParser()).invoke({
                "context":      context_str,
                "question":     query,
                "chat_history_messages": chat_history_messages,
                "history_focus": history_focus,
            })
        except Exception as e:
            err = str(e).lower()
            if "503" in err or "unavailable" in err or "high demand" in err:
                fallback_llm = get_fallback_llm()
                raw_answer = (self.prompt | fallback_llm | StrOutputParser()).invoke({
                    "context":      context_str,
                    "question":     query,
                    "chat_history_messages": chat_history_messages,
                })
            else:
                raise

        # ── Step 5: Parse answer, citations, suggestions, reasoning, confidence ──
        answer, llm_citations, suggestions, reasoning, confidence = _parse_answer_citations_suggestions(raw_answer)

        # Merge LLM-parsed citations with raw retrieval metadata for richer response
        final_citations = []
        used_refs = set()
        for lc in llm_citations:
            ref = lc.get("ref", "")
            # Try to enrich from raw_cits
            match = next((r for r in raw_cits if ref in r.get("ref", "") or (r.get("title") and r["title"] in lc.get("title", ""))), None)
            entry = {
                "ref":     ref,
                "title":   lc.get("title", match.get("title","") if match else ""),
                "type":    lc.get("type", match.get("type","") if match else ""),
                "excerpt": lc.get("excerpt", ""),
                "status":  match.get("status","") if match else "",
                "score":   _json_safe_float(
                    (match.get("score", 0.0) if match else 0.0)
                ),
            }
            if ref not in used_refs:
                final_citations.append(entry)
                used_refs.add(ref)

        # Fall back to raw citations if LLM did not produce any
        if not final_citations:
            final_citations = raw_cits
        final_citations = _sanitize_citation_list(final_citations)
        if RAG_DEBUG_RETRIEVAL:
            cited_refs = [str(c.get("ref", "")).strip() for c in final_citations if isinstance(c, dict)]
            print(
                f"[rag-debug] final_citations={len(cited_refs)} refs={cited_refs[:RAG_DEBUG_MAX_CHUNKS]}",
                flush=True,
            )

        # ── Step 6: Assemble full Audit Vault snapshots ──

        metadata_snapshot = []
        audit_log_snapshot = []
        
        seen_docs = set()
        for doc in all_docs:
            source_id = doc.metadata.get("source_id")
            if source_id not in seen_docs:
                metadata_snapshot.append(doc.metadata.get("full_metadata", doc.metadata))
                audit_log_snapshot.extend(doc.metadata.get("audit_trail", []))
                seen_docs.add(source_id)

        latency_ms = round((time.time() - t0) * 1000, 1)

        return {
            "answer":      answer,
            "reasoning":   reasoning,
            "confidence":  confidence,
            "citations":   final_citations,
            "retrieval_debug": _build_retrieval_debug_rows(all_docs),
            "suggestions": suggestions,
            "retrieval_stats": {
                "searched":     target_sections,
                "per_section":  per_section_counts,
                "total_docs":   len(all_docs),
                "latency_ms":   latency_ms,
            },
            "routed_to":   routed_label,
            "cached":      False,
            # Audit Vault Fields
            "metadata_snapshot":  metadata_snapshot,
            "audit_log_snapshot": audit_log_snapshot,
            "action_metadata": {
                "query": query,
                "routing": target_sections,
                "latency_ms": latency_ms,
                "timestamp": time.time(),
                "model": os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
            }
        }


# Keep FederatedRAGChain as alias for backward compat
FederatedRAGChain = SmartRAGChain
