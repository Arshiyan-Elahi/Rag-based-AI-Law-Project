"""Prompt builders for SOP editor actions.
Language priority: German (de). All other languages are fully supported.
The AI always detects the language of the input text and responds in the same language.

**Canonical source** for `/api/ai/action` prompt text: this module only.
``action/prompts.py`` re-exports these symbols; do not duplicate prompt strings elsewhere.
"""

from schemas.sop_actions import ActionRequest, JustifyRequest

# Logged by the FastAPI app as ``source_file`` for observability (keep in sync with this path).
AI_ACTION_PROMPT_SOURCE_FILE = "chatbot/actions/prompts.py"

# Improve / Rewrite: no Qdrant/RAG — LLM uses only system-style instructions + document fields + section text.
IMPROVE_REWRITE_NO_RAG_CONTEXT = (
    "(Kein RAG.) Nutze nur Metadaten + unten stehenden Text. / "
    "(No RAG.) Use only metadata + quoted text below."
)

_LANGUAGE_RULE = """LANGUAGE: Match the input language (German if input is German). Do not mix languages. Keep identifiers, codes, and abbreviations unchanged."""

_SPEED_FIRST = """OUTPUT: Return exactly one valid JSON object. No markdown, no code fences, no explanation, no sources. Be concise."""

_JSON_ESCAPING_RULE = """JSON RULES: Encode newlines as \\n, tabs as \\t, quotes as \\", backslashes as \\\\ inside string values. No literal control characters inside strings."""

_PRESERVE_CORE = """PRESERVE (never alter):
- All IDs: SOP-*, DEV-*, CAPA-*, AUD-*, DEC-*, form names, thresholds, dates, frequencies, versions
- Every section, block, and record: deviations, CAPAs, audit findings, decisions, references, trailing content; item count and order unchanged
- Register-line format: Datum:, Beschreibung:, Ursache:, Aktion:, Verantwortlich:, Finding:, Entscheidung:, Risiko:, Begründung: as separate short lines
- Punctuation habits: do not add sentence-final periods to terse register lines unless already consistent in input
- Named vendors, tools, systems, ports, protocols, values exactly — never convert to examples"""

_META_USAGE = """METADATA: Use NLP_STRUCTURE_AND_PARAMETERS and database metadata for style, terminology, and structure alignment only. If metadata conflicts with TEXT, preserve TEXT meaning."""


def _doc_block(request: ActionRequest, context: str) -> str:
    return f"""DOCUMENT
  title: {request.sop_title}
  section: {request.section_title}
  type: {request.section_type}
CONTEXT: {context}"""


def _nlp_section(nlp_block: str) -> str:
    nb = (nlp_block or "").strip()
    if not nb:
        return ""
    return f"\nNLP_STRUCTURE_AND_PARAMETERS:\n{nb}\n"


def build_improve_prompt(request: ActionRequest, context: str, nlp_block: str = "") -> str:
    return f"""You are a senior GMP/QA SOP editor. TASK: light editorial polish — not a rewrite.
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
{_doc_block(request, context)}
{_nlp_section(nlp_block)}
{_META_USAGE}
{_PRESERVE_CORE}

IMPROVE RULES:
- Fix only: grammar, missing articles, unclear abbreviations, passive ownership, non-GMP wording.
- Keep original sentence shape, list/table style, numbering, blank-line rhythm, and paragraph boundaries unless clearly malformed.
- Never introduce bullets, numbering, labels, or headings not present in the original.
- Never add steps, approvals, systems, requirements, or compliance claims.
- Keep compact register statements compact — do not inflate into narrative prose.
- Before returning: internally verify output against input and restore any missing section, record, or ID.

TEXT:
\"\"\"{request.section_text}\"\"\"
Return only:
{{"improved_text":"..."}}"""


def build_summarize_prompt(request: ActionRequest, context: str, nlp_block: str = "") -> str:
    return f"""You are a senior GMP/QA communications lead. TASK: produce a concise executive summary of the SOP text (no full rewrite).
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
{_doc_block(request, context)}
{_nlp_section(nlp_block)}
{_META_USAGE}
{_PRESERVE_CORE}

SUMMARY RULES:
- 6–12 short bullets or 2 tight paragraphs maximum.
- Cover: purpose, scope, critical controls, key roles, records, and review cadence when present in the text.
- Do not invent facts, dates, systems, or approvals that are not implied by the text.
- Keep identifiers and codes exactly as written.

TEXT:
\"\"\"{request.section_text}\"\"\"
Return only:
{{"improved_text":"..."}}"""


def build_analyze_prompt(request: ActionRequest, context: str, nlp_block: str = "") -> str:
    return f"""You are a senior GMP/QA compliance reviewer. TASK: structured compliance analysis of the SOP excerpt (not a rewrite).
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
{_doc_block(request, context)}
{_nlp_section(nlp_block)}
{_META_USAGE}
{_PRESERVE_CORE}

ANALYSIS RULES:
- Output a numbered list (plain lines separated by \\n) covering: clarity, control strength, evidence/records, training, change control, and residual risks.
- Reference only themes visible in the text; use bracketed placeholders when information is missing.
- Do not add regulatory citations unless already present in the text.

TEXT:
\"\"\"{request.section_text}\"\"\"
Return only:
{{"improved_text":"..."}}"""


def build_rewrite_prompt(request: ActionRequest, context: str, nlp_block: str = "") -> str:
    return f"""You are a senior GMP/QA SOP architect. TASK: full structural rewrite into industry-ready SOP language.
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
{_doc_block(request, context)}
{_nlp_section(nlp_block)}
{_META_USAGE}
{_PRESERVE_CORE}

REWRITE RULES:
SCOPE:
- Full SOP or weak draft → rewrite as complete industry SOP with all standard sections.
- Single section → rewrite that section only; add bracketed placeholders for missing controls.

FULL SOP BACKBONE (add when missing, in input language):
  Purpose/Zweck · Scope/Geltungsbereich · Responsibilities/Verantwortlichkeiten · Procedure/Verfahren ·
  Acceptance Criteria · Documentation/Records · Review/Approval/Lifecycle ·
  Training (if relevant) · Appendices/Traceability (if records present)

LANGUAGE & STYLE:
- Active voice, named accountable roles, precise verbs, consistent controlled vocabulary.
- Missing facts → bracketed placeholders: "[Zu definieren: verantwortliche Rolle]", "[To define: retention period]".
- Never invent dates, systems, owners, limits, forms, thresholds, or approvals.

RECORD / REGISTER MODE (DEV/CAPA/AUDIT/DECISION entries):
- Terse-record mode: fix grammar/clarity only; do not expand compact lines into formal narrative.
- Avoid filler: "Es existiert", "Es erfolgte", "wurde … durchgeführt" — keep concise factual form.
- Relocate deviation/CAPA/audit/decision logs to a traceability section only if original already separates them.

CONTROLS (add only where implied by TEXT, metadata, roles, or risks):
  trigger · frequency/SLA · evidence record · approval gate · verification step ·
  exception handling · escalation · acceptance criterion · retention/location · effectiveness review

- Before returning: internally verify output against input and restore any missing section, record, or ID.

TEXT:
\"\"\"{request.section_text}\"\"\"
Return only:
{{"rewritten_text":"..."}}"""


def build_gap_check_prompt(request: ActionRequest, context: str, nlp_block: str = "") -> str:
    return f"""You are a senior GMP/QA compliance auditor. TASK: audit-grade gap check of the selected SOP text.
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
SOP: "{request.sop_title}" | Section: "{request.section_title}" ({request.section_type})

HYBRID_RAG_REFERENCE_CONTEXT:
{context}
{_nlp_section(nlp_block)}
CONTEXT USAGE:
- TEXT is the primary audit evidence.
- RAG context: compare against expected controls, related SOP language, and compliance patterns.
- NLP_STRUCTURE_AND_PARAMETERS: use detected sections, roles, risks, metadata, and lifecycle signals as audit signals.
- Report a gap only when supported by TEXT, NLP metadata, or RAG context — not generic GMP knowledge alone.
- If RAG is absent or unrelated, state: "Gap check based on TEXT and NLP metadata only."

AUDIT METHOD:
1. Identify expected SOP structure from TEXT, metadata, and NLP sections.
2. Check each required element for presence, specificity, and actionability.
3. Compare deviations/CAPAs/audits/decisions/controls/dates/statuses for internal consistency.
4. Cite exact evidence (section name or record ID: SOP-*, DEV-*, CAPA-*, AUD-*, DEC-*) for every gap.

GAP CATEGORIES:
- Missing sections: Purpose, Scope, Responsibilities, Procedure, Documentation, Review/Approval
- Missing role ownership, approver, executor, escalation path, QA oversight
- Missing frequencies, deadlines, SLAs, trigger conditions, effective/review/closure dates
- Missing controls: verification step, access control, dual control, monitoring, alarm criteria, acceptance criteria
- Documentation gaps: form name, record location, retention, evidence, timestamp, signature
- Linkage gaps: missing IDs, inconsistent statuses, open CAPAs without closure, findings without CAPA, decisions without rationale
- Ambiguous wording: "regelmäßig", "zeitnah", "bei Bedarf", "sofort", "ausreichend" without measurable criteria
- Metadata inconsistencies: SOP number/title/version/status/department conflicts between TEXT and database metadata

OUTPUT RULES:
- Practical audit findings — not rewritten SOP prose.
- Prioritize compliance gaps over style/grammar observations.
- If no material gaps found, state clearly and list residual assumptions.
- Do not propose a new SOP version, new status, or relocate DEV/CAPA/AUDIT logs unless TEXT already uses appendix structure.
- Localize headings to input language. German → "Zusammenfassung", "RAG/NLP-Grundlage", "Festgestellte Lücken", "Empfohlene Korrekturen", "Vorgeschlagener SOP-Ergänzungstext", "Verbleibende Annahmen".

TEXT:
\"\"\"{request.section_text}\"\"\"

Return only one JSON object:
{{"analysis":"Zusammenfassung/Summary:\\n...\\n\\nRAG/NLP-Grundlage/Basis:\\n...\\n\\nFestgestellte Lücken/Identified Gaps:\\n1. Gap: ...\\n   Evidence: ...\\n   Risk/Impact: ...\\n   Recommended Fix: ...\\n\\nEmpfohlene Korrekturen/Recommended Fixes:\\n1. ...\\n\\nVorgeschlagener SOP-Ergänzungstext/Suggested SOP Text:\\n...\\n\\nVerbleibende Annahmen/Residual Assumptions:\\n..."}}
No markdown. No sources. No text outside JSON."""


def build_convert_prompt(request: ActionRequest) -> str:
    return f"""Du bist ein erfahrener GMP/QA Dokumentationsspezialist.
You are a senior GMP/QA technical writer and regulatory documentation specialist.

{_LANGUAGE_RULE}

Konvertiere den folgenden Rohtext in ein vollständig strukturiertes SOP-Dokument.
Convert the following raw text into a properly structured SOP document.

═══════════════════════════════════════════════════════════════
DOKUMENTKONTEXT / DOCUMENT CONTEXT
═══════════════════════════════════════════════════════════════
SOP-Titel / SOP Title: "{request.sop_title}"

ROHTEXT / RAW TEXT:
\"\"\"{request.section_text}\"\"\"

═══════════════════════════════════════════════════════════════
PFLICHTANFORDERUNGEN / MANDATORY REQUIREMENTS
═══════════════════════════════════════════════════════════════
  • Alle fünf Abschnitte sind PFLICHT. Kein Abschnitt darf fehlen.
    All five sections are MANDATORY. No section may be omitted.
  • Falls ein Abschnitt nicht genug Informationen hat, schreibe:
    "[Zu definieren — [spezifisches Detail] vor SOP-Freigabe festlegen]"
  • Schreibe "procedure" als JSON-Array von Strings, einen Schritt pro String.
  • Verwende GMP-konforme Sprache: imperative Verben, benannte Rollen, keine Mehrdeutigkeit.
  • Minimum 5 Schritte im Verfahrensabschnitt / Minimum 5 steps in the procedure section.

Gib NUR ein gültiges JSON-Objekt mit genau diesen Schlüsseln zurück:
Return ONLY a valid JSON object with exactly these keys:
{{
  "purpose": "Ein Satz: Was diese SOP regelt und warum sie existiert / One sentence: what this SOP governs and why",
  "scope": "Vollständige Geltungsbereichsdefinition mit Rollen, Systemen und ggf. Ausnahmen / Full scope definition",
  "responsibilities": "Benannte Rollen mit spezifischen, imperativen Verpflichtungen / Named roles with specific obligations",
  "procedure": [
    "Schritt 1: [Benannte Rolle] soll [Aktion] mit [Methode/Werkzeug] / Step 1: ...",
    "Schritt 2: [Benannte Rolle] soll [Aktion] und dokumentieren in [Formularname] / Step 2: ...",
    "Schritt 3: ...",
    "Schritt 4: ...",
    "Schritt 5: ..."
  ],
  "documentation": "Alle Formulare, Protokolle und Aufzeichnungen: Name, Aufbewahrungsort, Aufbewahrungsfrist / All records: name, location, retention period"
}}"""


def build_convert_retry_prompt(request: ActionRequest) -> str:
    return build_convert_prompt(request) + (
        "\n\n═══════════════════════════════════════════════════════════════\n"
        "KRITISCHE WIEDERHOLUNGSANWEISUNG / CRITICAL RETRY INSTRUCTION\n"
        "═══════════════════════════════════════════════════════════════\n"
        "Deine vorherige Antwort war kein gültiges JSON oder enthielt fehlende Schlüssel.\n"
        "Your previous response was not valid JSON or was missing required keys.\n"
        "Du MUSST NUR ein gültiges JSON-Objekt mit genau diesen fünf Schlüsseln zurückgeben:\n"
        "  'purpose', 'scope', 'responsibilities', 'procedure' (als Array), 'documentation'\n"
        "Alle fünf Schlüssel müssen vorhanden und nicht leer sein.\n"
        "Verwende professionellen Platzhaltertext wenn Quellinformationen unvollständig sind.\n"
        "KEIN Markdown, KEINE Erklärung, KEIN Text außerhalb des JSON-Objekts."
    )


def build_justify_prompt(request: JustifyRequest) -> str:
    return f"""Du bist ein leitender GMP/QA Compliance-Schreiber, der GMP-Audit-Trail-Einträge erstellt,
die regulatorischen Inspektionsanforderungen entsprechen.
You are a senior GMP/QA compliance writer generating GMP audit trail entries.

{_LANGUAGE_RULE}

═══════════════════════════════════════════════════════════════
ÄNDERUNGSKONTEXT / CHANGE CONTEXT
═══════════════════════════════════════════════════════════════
SOP-Titel / SOP Title    : "{request.sop_title}"
Abschnittstitel / Section: "{request.section_title}"
Abschnittstyp / Type     : {request.section_type}
Änderungstyp / Change    : {request.change_type}

ORIGINALTEXT / ORIGINAL TEXT:
\"\"\"{request.old_text}\"\"\"

NEUER TEXT / NEW TEXT:
\"\"\"{request.new_text}\"\"\"

═══════════════════════════════════════════════════════════════
AUFGABE / TASK
═══════════════════════════════════════════════════════════════
Erstelle eine formelle, rechtlich vertretbare Begründung für diese Änderung.
Write a formal, legally defensible justification for this change.

ANFORDERUNGEN / REQUIREMENTS:
  • Nennt explizit die SOP: "{request.sop_title}"
  • Nennt explizit den Abschnitt: "{request.section_title}"
  • Beschreibt WAS sich geändert hat (Art der Änderung)
  • Erklärt WARUM die Änderung vorgenommen wurde
  • Beschreibt WIE die Änderung Compliance, Risikominimierung oder Qualität verbessert
  • Genau 2 bis 3 Sätze — nicht mehr, nicht weniger
  • Formelle, professionelle Sprache (kein "ich/wir")
  • Vergangenheitsform (die Änderung wurde vorgenommen)

Gib NUR ein gültiges JSON-Objekt zurück / Return ONLY a valid JSON object:
{{
  "justification": "2-3 formelle Sätze mit expliziter Nennung der SOP und des Abschnitts sowie der spezifischen Begründung.",
  "change_category": "eines von genau: clarity_improvement | compliance_alignment | error_correction | process_update | regulatory_requirement",
  "regulatory_reference": "Spezifische regulatorische Klausel (z.B. 'ISO 13485:2016 Abschnitt 4.2.4') oder null"
}}"""