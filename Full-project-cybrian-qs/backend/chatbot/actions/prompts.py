"""Prompt builders for SOP editor actions.
Language priority: German (de). All other languages are fully supported.
The AI always detects the language of the input text and responds in the same language.
"""

from schemas.sop_actions import ActionRequest, JustifyRequest

# Improve / Rewrite: no Qdrant/RAG — LLM uses only system-style instructions + document fields + section text.
IMPROVE_REWRITE_NO_RAG_CONTEXT = (
    "(Kein RAG.) Nutze nur Metadaten + unten stehenden Text. / "
    "(No RAG.) Use only metadata + quoted text below."
)

_LANGUAGE_RULE = """LANGUAGE: Match the input language (German if input is German). Do not mix languages. Keep identifiers, codes, and abbreviations unchanged."""

_SPEED_FIRST = """SPEED: Single pass. Return exactly one compact JSON object on a single line (or with no raw line breaks inside string values). No markdown, no code fences, no explanation, no Sources, no citations. Be concise: lean text = faster review and valid JSON."""

_JSON_ESCAPING_RULE = """JSON STRING RULES (mandatory):
- The entire answer must be one valid JSON object only.
- Every string value must be a valid JSON string: encode line breaks as \\n (two characters), tabs as \\t, double quotes as \\", backslashes as \\\\.
- Do not put real line breaks, tabs, or control characters inside a JSON string value — only escaped forms.
- If the section has multiple paragraphs, join them with \\n inside the string, not literal newlines."""


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
    return f"""You are a senior GMP/QA SOP editor. Task: IMPROVE the selected SOP text with targeted, conservative edits while preserving the original SOP context and format.
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
{_doc_block(request, context)}
{_nlp_section(nlp_block)}
Use the NLP_STRUCTURE_AND_PARAMETERS and database metadata as controlling context:
- Preserve SOP number, title, version/status, department, document type, category, risk level, lifecycle metadata, and any identifiers exactly when present.
- Use detected language, tone, formality, numbering style, section labels, role names, workflow order, compliance references, risks, gaps, and rewrite_improve_parameters.
- If metadata and selected TEXT conflict, preserve the selected TEXT meaning and use metadata only for style, structure, and terminology alignment.

Improvement rules:
- Improve means light editorial polish, not a full rewrite. Keep the original sentence shape where it is already clear.
- Keep the same SOP section structure and format as the original selected TEXT: same heading/list/table style, paragraph boundaries, blank-line rhythm, numbering pattern, and order unless the selected TEXT is clearly malformed.
- Do not introduce numbering, bullets, labels, section names, punctuation styles, or explanatory prefaces that were not present in the selected TEXT.
- Preserve the original register style: if entries are short log/register statements, keep them short and factual rather than converting them into formal legal prose.
- Preserve punctuation habits from the selected TEXT. Do not add sentence-final periods to short metadata/register lines unless that punctuation style already exists consistently in the input.
- Avoid awkward nominalizations or over-formal substitutions in German. Prefer clear SOP verbs and natural phrases (for example, avoid changing "Ermöglichung von Zugriffen" into "Verordnung von Zugriffen").
- Preserve the original severity and tone of causes. Do not make wording harsher, softer, or more judgmental unless the selected TEXT already supports that tone.
- Preserve every original section/block and every record. Do not omit DEVIATIONS, CAPAs, AUDIT FINDINGS, DECISIONS, references, or trailing content when present.
- Preserve every listed item count and every identifier, including SOP-*, DEV-*, CAPA-*, AUD-*, and DEC-* records; each original item must appear once in the output.
- Preserve original short register entries such as "Datum:", "Beschreibung:", "Ursache:", "Aktion:", "Verantwortlich:", "Finding:", "Linked CAPA:", "Entscheidung:", "Risiko:", and "Begründung:" as separate lines in the same order.
- Do not add unrelated requirements, new process steps, new approvals, new timelines, new systems, or new compliance claims.
- Improve grammar, missing articles, unclear abbreviations, role ownership, controlled vocabulary, and audit-ready wording only where the original is ambiguous, incomplete, or grammatically weak.
- Keep compact SOP facts compact; avoid turning short register statements into long narrative sentences unless clarity requires it.
- Keep all traceability IDs, form names, references, thresholds, dates, frequencies, and control outcomes unchanged.
- Use concise SOP wording with mandatory language where already implied by the original.
- Before final JSON, internally compare the output against the input and restore any missing section, record, or ID.
- Internally reason about why each edit improves clarity/compliance, but do not output the reasoning; return only the improved SOP text in JSON.
TEXT:
\"\"\"{request.section_text}\"\"\"
Return only:
{{"improved_text":"..."}}"""


def build_rewrite_prompt(request: ActionRequest, context: str, nlp_block: str = "") -> str:
    return f"""You are a senior GMP/QA SOP editor. Task: REWRITE the selected SOP text into the best professional SOP version while preserving the original context and control intent.
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
{_doc_block(request, context)}
{_nlp_section(nlp_block)}
Use the NLP_STRUCTURE_AND_PARAMETERS and database metadata as controlling context:
- Preserve SOP number, title, version/status, department, document type, category, risk level, lifecycle metadata, and any identifiers exactly when present.
- Use detected language, tone, formality, numbering style, section labels, role names, workflow order, compliance references, risks, gaps, and rewrite_improve_parameters.
- If metadata and selected TEXT conflict, preserve the selected TEXT meaning and use metadata only for style, structure, and terminology alignment.

Rewrite rules:
- Follow the same structure and format as the original selected TEXT: same section/header pattern, list/table shape, numbering style, paragraph sequence, and SOP flow.
- Preserve every original section/block and every record. Do not omit DEVIATIONS, CAPAs, AUDIT FINDINGS, DECISIONS, references, or trailing content when present.
- Preserve every listed item count and every identifier, including SOP-*, DEV-*, CAPA-*, AUD-*, and DEC-* records; each original item must appear once in the output.
- Rewrite for clearer SOP language, active voice, concrete accountable roles, precise verbs, consistent terms, and audit-ready readability.
- Do not change intent, scope, sequence, responsibilities, controls, acceptance criteria, references, thresholds, dates, frequencies, or records.
- Do not add unrelated requirements, new process steps, new approvals, new systems, or new compliance claims.
- Do not add explanatory business value, regulatory rationale, risk claims, examples, or qualifiers that are not already present in the selected TEXT or metadata.
- Preserve named vendors/tools/systems exactly. Do not convert a specific value into an example (for example, keep "SEC Consult" rather than "z.B. SEC Consult").
- Preserve technical qualifiers exactly where they affect meaning, including "alle", "nur", "nicht nur", "ausschließlich", ports, protocols, VLAN numbers, time windows, dates, statuses, and responsible roles.
- Keep compact register entries concise; rewrite the wording, but do not inflate short descriptions into broader interpretations.
- Preserve the source's register/log style when the selected TEXT uses terse records. Do not convert every "Beschreibung", "Ursache", "Aktion", "Finding", "Entscheidung", or "Begründung" line into a full formal sentence unless the original line is unclear.
- Preserve punctuation style for register lines where possible. Do not add sentence-final periods throughout terse record fields if the input does not use them consistently.
- Prefer minimal grammatical completion over stylistic expansion for DEV/CAPA/AUDIT/DECISION entries.
- For DEV/CAPA/AUDIT/DECISION record fields, use terse-record mode: keep line length and compact syntax close to the original, fix only missing grammar or clarity, and avoid adding filler phrases such as "Es existiert", "Es erfolgte", "Die Kontrolle", "Das Fehlen", or "wurde ... durchgeführt" when a shorter factual form preserves the meaning.
- Keep all traceability IDs, form names, references, and metadata-derived identifiers unchanged.
- If the original is incomplete or ambiguous, improve wording around the available content without inventing missing facts.
- Before final JSON, internally compare the output against the input and restore any missing section, record, or ID.
- Internally reason about preservation of context, structure, and compliance quality, but do not output the reasoning; return only the rewritten SOP text in JSON.
TEXT:
\"\"\"{request.section_text}\"\"\"
Return only:
{{"rewritten_text":"..."}}"""


def build_gap_check_prompt(request: ActionRequest, context: str, nlp_block: str = "") -> str:
    return f"""You are a senior GMP/QA compliance auditor performing an audit-grade SOP GAP CHECK on the current selected SOP text.
{_SPEED_FIRST}
{_JSON_ESCAPING_RULE}
{_LANGUAGE_RULE}
SOP title: "{request.sop_title}"
Section: "{request.section_title}" ({request.section_type})
HYBRID_RAG_REFERENCE_CONTEXT:
{context}
{_nlp_section(nlp_block)}
How to use context:
- The TEXT below is the current SOP content being audited and is the primary evidence.
- HYBRID_RAG_REFERENCE_CONTEXT may contain retrieved SOP/database context from the hybrid RAG pipeline (dense + BM25 + reranking), style profile, and uploaded SOP excerpts. Use it to compare against expected controls, related SOP language, metadata, and known compliance patterns.
- NLP_STRUCTURE_AND_PARAMETERS may contain detected sections, roles, workflow steps, compliance references, risks, structural gaps, metadata JSON fields, SOP/version status, and database parameters. Use these as audit signals.
- Do not invent gaps from generic knowledge alone. Report a gap only when it is supported by the current TEXT, NLP/database metadata, or relevant RAG context.
- If RAG context is absent, weak, or unrelated, state that the gap check is based on the current TEXT and NLP/database metadata only.

Audit method:
1. Identify the expected SOP structure from the TEXT, metadata, detected NLP sections, and RAG context.
2. Check whether required SOP elements are present, specific, and actionable.
3. Compare stated deviations, CAPAs, audit findings, decisions, controls, dates, statuses, links, roles, and records for consistency.
4. Detect gaps that could affect GMP/QA audit readiness, operational control, traceability, or lifecycle compliance.
5. For every gap, cite the exact evidence from TEXT or context by naming the section/record/identifier (for example SOP-*, DEV-*, CAPA-*, AUD-*, DEC-*).

Gap categories to check:
- missing mandatory SOP sections: Zweck/Purpose, Scope/Geltungsbereich, Responsibilities/Verantwortlichkeiten, Procedure/Verfahren, Documentation/Records, Review/Approval where expected
- unclear or missing role ownership, approval owner, reviewer, executor, escalation path, or QA oversight
- missing or unclear frequencies, deadlines, trigger conditions, SLAs, effective dates, review dates, retention periods, or closure expectations
- missing procedural controls, verification steps, access controls, segregation of duties, dual control, monitoring, alarm criteria, or acceptance criteria
- documentation and audit-trail gaps: missing form/log/ticket name, record location, retention period, evidence required, timestamp, signature, or review record
- deviation/CAPA/audit/decision linkage gaps: missing linked IDs, inconsistent statuses, open actions without mitigation, CAPA due dates inconsistent with deviation dates, audit findings without CAPA, decisions without rationale or risk
- regulatory or standard alignment gaps when the context indicates a standard, regulation, or internal SOP expectation
- ambiguous wording: passive wording without accountable role, vague terms such as "regelmäßig", "zeitnah", "bei Bedarf", "ausreichend", or "sofort" without measurable criteria
- metadata/lifecycle gaps: inconsistent SOP number/title/version/status/department/risk level/category between TEXT and database metadata

Output requirements:
- Return a practical audit report, not rewritten SOP prose only.
- Prioritize real gaps over style suggestions. Do not list grammar-only edits as compliance gaps.
- If no material gaps are found, say so clearly and list any residual assumptions.
- Keep the same language as the input.
- Localize the report section headings to the input language. For German input use headings such as "Zusammenfassung", "RAG/NLP-Grundlage", "Festgestellte Lücken", "Empfohlene Korrekturen", "Vorgeschlagener SOP-Ergänzungstext", and "Verbleibende Annahmen".
- Do not change or invent SOP identity/lifecycle metadata in suggested text. Preserve the current SOP number, title, version, status, and department exactly as given unless a metadata inconsistency is itself listed as a gap.
- Do not propose a complete new SOP, new version number, or new status. Provide only targeted gap-fix text snippets or a concise structural outline for missing sections.
- Do not relocate DEV/CAPA/AUDIT/DECISION logs to an appendix unless the TEXT or RAG context explicitly shows that this repository uses appendices for those records.
- For each gap, separate observed evidence from recommendation; avoid presenting assumptions as facts.
TEXT:
\"\"\"{request.section_text}\"\"\"
Return only one JSON object:
{{"analysis":"Summary:\\n...\\n\\nRAG/NLP Basis:\\n...\\n\\nIdentified Gaps:\\n1. Gap: ...\\n   Evidence: ...\\n   Risk/Impact: ...\\n   Recommended Fix: ...\\n\\nRecommended Fixes:\\n1. ...\\n\\nSuggested SOP Text:\\n...\\n\\nResidual Assumptions:\\n..."}}
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
