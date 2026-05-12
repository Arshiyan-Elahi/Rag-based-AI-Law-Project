import json
import os
import re
from groq_client import query_groq
from contract_summary import generate_contract_summary, parse_structured_summary
from clause_analysis import (
    parse_clauses,                      # fallback
    parse_clauses_with_sources,         # <-- use this to attach legal sources
    find_indexation_clause,
    analyze_indexation_clause,
    detect_full_mrg,
)
from vpi_validation import vpi_rent_validation
from richtwert_validation import richtwert_validation

# ---------- avg_rent loader ----------

def _avg_rent_path():
    return os.path.join(os.path.dirname(__file__), "avg_rent.json")

def load_avg_rent_data():
    path = _avg_rent_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def parse_rent_value(rent_str_or_number):
    """
    Same parser used in validators — return float or None.
    """
    if rent_str_or_number is None:
        return None
    if isinstance(rent_str_or_number, (int, float)):
        return float(rent_str_or_number)
    s = str(rent_str_or_number).strip()
    if not s:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        if s.count(".") and re.search(r"\.\d{3}($|\D)", s):
            s = s.replace(".", "")
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def get_average_rent(address):
    data = load_avg_rent_data()
    states = data.get("regional_breakdown_2024", {}).get("federal_states", {})
    if address:
        for state_name in states.keys():
            if state_name.lower() in address.lower():
                info = states.get(state_name, {})
                rent = info.get("rent_with_operating_costs", {}).get("per_apartment")
                if rent is not None:
                    return float(rent), state_name
    austria = states.get("Austria_Total", {})
    rent = austria.get("rent_with_operating_costs", {}).get("per_apartment")
    return (float(rent) if rent is not None else 653.6), "Österreich"

def generate_indexation_summary(indexation_analysis):
    """Generate LLM summary for indexation clause analysis"""
    # Safe defaults in case the LLM call fails early
    index_name = indexation_analysis.get("index_name", "Keine Angabe")
    interval = indexation_analysis.get("adjustment_interval", "Keine Angabe")
    try:
        threshold = indexation_analysis.get("threshold_percent", 0)
        symmetric = indexation_analysis.get("symmetric_adjustment", False)
        clause_text = indexation_analysis.get("clause_text", "")

        prompt = f"""
Erstelle eine prägnante deutsche Zusammenfassung für die Wertsicherungsklausel-Analyse:

Index: {index_name}
Schwellenwert: {threshold}%
Symmetrische Anpassung: {'Ja' if symmetric else 'Nein'}
Anpassungsintervall: {interval}
Klauseltext: {clause_text}

Gib eine kurze, verständliche Bewertung der Wertsicherungsklausel in 2-3 Sätzen aus. 
Bewerte ob die Klausel vollständig, rechtmäßig und für den Mieter fair ist.
"""
        response = query_groq(prompt)
        return response.strip()
    except Exception:
        return f"Wertsicherungsklausel mit Index {index_name} und {interval} Anpassung."

def generate_vpi_summary(vpi_validation):
    """Generate LLM summary for VPI validation"""
    # Safe defaults
    indexation_valid = vpi_validation.get("indexation_valid", "Keine Angabe")
    expected_rent = vpi_validation.get("expected_rent", "Nicht verfügbar")
    current_rent = vpi_validation.get("current_rent", "Nicht verfügbar")
    try:
        difference_percent = vpi_validation.get("difference_percent", "N/A")
        within_tolerance = vpi_validation.get("within_tolerance")
        base_date = vpi_validation.get("base_year_or_start_date", "")

        tolerance_text = ""
        if within_tolerance is not None:
            tolerance_text = "innerhalb der Toleranz" if within_tolerance else "außerhalb der Toleranz"

        prompt = f"""
Erstelle eine prägnante deutsche Zusammenfassung für die VPI-Validierung:

Status: {indexation_valid}
Erwartete Miete: {expected_rent}
Aktuelle Miete: {current_rent}
Differenz: {difference_percent}
Toleranz: {tolerance_text}
Basisdatum: {base_date}

Gib eine kurze, verständliche Bewertung der VPI-Anpassung in 2-3 Sätzen aus.
Erkläre ob die Mietanpassung korrekt durchgeführt wurde.
"""
        response = query_groq(prompt)
        return response.strip()
    except Exception:
        return f"VPI-Validierung: {indexation_valid}. Aktuelle Miete {current_rent} vs. erwartete Miete {expected_rent}."

def generate_richtwert_summary(richtwert_validation):
    """Generate LLM summary for Richtwert validation"""
    # Safe defaults
    applicable = richtwert_validation.get("applicable", False)
    valid = richtwert_validation.get("valid", "Keine Angabe")
    max_rent = richtwert_validation.get("max_rent_allowed", "Nicht verfügbar")
    current_rent = richtwert_validation.get("current_rent", "Nicht verfügbar")
    try:
        excess_amount = richtwert_validation.get("excess_amount", 0)
        excess_percent = richtwert_validation.get("excess_percent", "0%")
        calc_info = richtwert_validation.get("calculation_info", "")

        prompt = f"""
Erstelle eine prägnante deutsche Zusammenfassung für die Richtwert-Validierung:

Anwendbar: {'Ja' if applicable else 'Nein'}
Maximal zulässige Miete: {max_rent}
Status: {valid}
Aktuelle Miete: {current_rent}€
Überschreitung: {excess_amount}€ ({excess_percent})
Berechnung: {calc_info}

Gib eine kurze, verständliche Bewertung des Richtwerts in 2-3 Sätzen aus.
Erkläre ob die Miete den gesetzlichen Richtwert einhält oder überschreitet.
"""
        response = query_groq(prompt)
        return response.strip()
    except Exception:
        if applicable:
            return f"Richtwert-Prüfung: {valid}. Maximal zulässig {max_rent}, aktuell {current_rent}€."
        else:
            return "Richtwert-Mietzins nicht anwendbar - keine volle MRG-Anwendung."

# ---------- main analyzer ----------

def analyze_contract_text(contract_text, context_text=None):
    # Generate summaries
    summary_response = generate_contract_summary(contract_text)
    structured_summary = parse_structured_summary(summary_response)

    # detect full MRG
    structured_summary["is_full_mrg"] = detect_full_mrg(contract_text)

    # Clause classification via GROQ - IMPROVED PROMPT
    prompt_clauses = f"""
Analysiere den folgenden österreichischen Mietvertrag und klassifiziere alle Klauseln nach ihrer Rechtmäßigkeit.

Vertragstext:
{contract_text}

Bitte antworte GENAU in folgendem Format:

ILLEGALE KLAUSELN:
- [Klausel 1 falls vorhanden]
- [Klausel 2 falls vorhanden]

FRAGWUERDIGE KLAUSELN:
- [Klausel 1 falls vorhanden]
- [Klausel 2 falls vorhanden]

LEGALE KLAUSELN:
- [Klausel 1]
- [Klausel 2]
- [Klausel 3]

Analysiere alle im Vertrag enthaltenen Bestimmungen wie:
- Mietpreisklauseln
- Wertsicherungsklauseln
- Kündigungsbestimmungen
- Haustierhaltung
- Untervermietung
- Reparaturverpflichtungen
- Kautionsregelungen
- Schriftformklauseln
- Nutzungsbeschränkungen

Falls keine illegalen oder fragwürdigen Klauseln gefunden werden, schreibe dennoch die Überschriften und füge "Keine gefunden" hinzu.
"""
    # prepare relevant chunks from provided FAISS context (main.py joins with "\n\n")
    relevant_chunks = None
    if context_text and isinstance(context_text, str):
        # Keeping separator consistent with main.py context building
        relevant_chunks = [c for c in context_text.split("\n\n") if c.strip()]

    try:
        clauses_response = query_groq(prompt_clauses)
        print(f"Clauses response: {clauses_response}")  # Debug output

        # Parse WITH legal sources if we have any context; otherwise fallback to basic parsing
        if relevant_chunks:
            illegal_clauses, questionable_clauses, legal_clauses, legal_sources = \
                parse_clauses_with_sources(clauses_response, relevant_context_chunks=relevant_chunks)
        else:
            # no context available -> parse without sources
            i, q, l = parse_clauses(clauses_response)
            illegal_clauses, questionable_clauses, legal_clauses = i, q, l
            legal_sources = []
    except Exception as e:
        print(f"Error in clause analysis: {e}")
        illegal_clauses, questionable_clauses, legal_clauses, legal_sources = [], [], [], []

    # Risk score (still using GROQ prompt)
    prompt_risk = f"""
Bewerte das Risiko dieses österreichischen Mietvertrags als Prozentsatz (0% = kein Risiko, 100% = sehr hohes Risiko).

Berücksichtige dabei:
- Illegale Klauseln
- Überhöhte Miete
- Fehlende Rechte des Mieters
- Unklare Formulierungen
- Benachteiligung des Mieters

Vertragstext:
{contract_text}

Antworte nur mit der Prozentzahl, z.B. "25%"
"""
    try:
        risk_score_response = query_groq(prompt_risk)
        match_risk = re.search(r"(\d+)%", risk_score_response)
        risk_score_percentage = match_risk.group(1) + "%" if match_risk else "Keine Angabe"
    except Exception as e:
        print(f"Error in risk assessment: {e}")
        risk_score_percentage = "Keine Angabe"

    # Normalize contract rent early
    rent_raw = structured_summary.get("rental_period_costs", {}).get("rent", "")
    parsed_rent = parse_rent_value(rent_raw)
    if parsed_rent is not None:
        structured_summary.setdefault("rental_period_costs", {})["rent"] = parsed_rent

    # Rent comparison
    address = structured_summary.get("residential_property", {}).get("address", "") or ""
    avg_rent_value, region_name = get_average_rent(address)
    rent_comparison = {"percent": "Keine Angabe", "text": "Keine Angabe"}
    if parsed_rent is not None and avg_rent_value:
        diff_percent = ((parsed_rent - avg_rent_value) / avg_rent_value) * 100
        rent_comparison = {
            "percent": f"{diff_percent:+.1f}%",
            "text": f"{'über' if diff_percent > 0 else 'unter'} dem Mietspiegel in {region_name}"
        }

    # Indexation clause detection & analysis
    index_clause = find_indexation_clause(illegal_clauses, questionable_clauses, legal_clauses, contract_text)
    if not index_clause:
        indexation_keywords = [
            "wertsicherung", "index", "mietanpassung", "preisindex", 
            "inflation", "teuerung", "verbraucherpreisindex", "vpi",
            "anpassung", "erhöhung", "valorisierung", "indexierung"
        ]
        for line in contract_text.lower().split('\n'):
            if any(keyword in line for keyword in indexation_keywords):
                index_clause = line.strip()
                break

    if index_clause:
        try:
            indexation_clause_analysis = analyze_indexation_clause(index_clause)
            if isinstance(indexation_clause_analysis, dict):
                indexation_clause_analysis["clause_text"] = index_clause
                indexation_clause_analysis.pop("comment", None)
                indexation_clause_analysis["llm_generated_summary"] = generate_indexation_summary(indexation_clause_analysis)
        except Exception as e:
            print(f"Error analyzing indexation clause: {e}")
            indexation_clause_analysis = {
                "info": f"Fehler bei der Analyse: {str(e)}", 
                "clause_text": index_clause,
                "index_name": "Keine Angabe",
                "threshold_percent": 0,
                "symmetric_adjustment": False,
                "adjustment_interval": "Keine Angabe",
                "llm_generated_summary": f"Wertsicherungsklausel gefunden, aber Analyse fehlgeschlagen: {str(e)}"
            }
    else:
        indexation_clause_analysis = {
            "info": "Keine Wertsicherungsklausel gefunden.",
            "index_name": "Keine Angabe",
            "threshold_percent": 0,
            "symmetric_adjustment": False,
            "adjustment_interval": "Keine Angabe",
            "llm_generated_summary": "Keine Wertsicherungsklausel im Vertrag vorhanden."
        }

    # Validations
    try:
        vpi_validation_result = vpi_rent_validation(structured_summary)
        vpi_validation_result.pop("comment", None)
        vpi_validation_result["llm_generated_summary"] = generate_vpi_summary(vpi_validation_result)
    except Exception as e:
        print(f"Error in VPI validation: {e}")
        vpi_validation_result = {
            "indexation_valid": "Keine Angabe",
            "base_year_or_start_date": structured_summary.get("rental_period_costs", {}).get("start_date", ""),
            "last_adjustment_date": structured_summary.get("rental_period_costs", {}).get("last_adjustment_date", ""),
            "expected_rent": "Nicht verfügbar",
            "current_rent": parsed_rent,
            "llm_generated_summary": f"VPI Validierung fehlgeschlagen: {str(e)}"
        }

    try:
        richtwert_validation_result = richtwert_validation(structured_summary)
        richtwert_validation_result.pop("comment", None)
        richtwert_validation_result["llm_generated_summary"] = generate_richtwert_summary(richtwert_validation_result)
    except Exception as e:
        print(f"Error in Richtwert validation: {e}")
        richtwert_validation_result = {
            "applicable": structured_summary.get("is_full_mrg", False),
            "max_rent_allowed": "Nicht verfügbar",
            "valid": "Keine Angabe",
            "llm_generated_summary": f"Richtwert Validierung fehlgeschlagen: {str(e)}"
        }

    # Build summary comment (human readable) - uses LLM summaries
    idx_summary = indexation_clause_analysis.get("llm_generated_summary", "Keine Wertsicherungsklausel gefunden.")
    vpi_summary = vpi_validation_result.get("llm_generated_summary", "VPI-Validierung nicht verfügbar.")
    richtwert_summary = richtwert_validation_result.get("llm_generated_summary", "Richtwert-Prüfung nicht verfügbar.")
    summary_comment = f"{idx_summary} {vpi_summary} {richtwert_summary}"

    result = {
        "contract_summary_german": summary_response,
        "structured_summary_json": structured_summary,
        "risk_score_percentage": risk_score_percentage,
        "average_rent_austria": avg_rent_value,
        "rent_comparison": rent_comparison,

        # Each clause item (when context was available) is a dict:
        # {"clause": "...", "legal_sources": [...]}
        "clauses_german": {
            "illegal": illegal_clauses,
            "questionable": questionable_clauses,
            "legal": legal_clauses
        },

        # Top-level deduplicated sources derived from the FAISS-retrieved chunks
        "legal_sources": legal_sources,

        "indexation_clause_analysis": indexation_clause_analysis,
        "vpi_validation": vpi_validation_result,
        "richtwert_validation": richtwert_validation_result,
        "summary_comment": summary_comment
    }

    return result