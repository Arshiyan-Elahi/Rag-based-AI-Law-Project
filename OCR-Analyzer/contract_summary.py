from groq_client import query_groq


def parse_structured_summary(summary_text):
    def extract_value(line):
        return line.split(":", 1)[1].strip() if ":" in line else ""
    data = {
        "parties": {"landlord": "", "tenant": ""},
        "residential_property": {
            "address": "",
            "size": "",
            "rooms": "",
            "special_features": "",
            "building_year": ""
        },
        "rental_period_costs": {
            "start_date": "",
            "duration": "",
            "rent": "",
            "deposit": "",
            "last_adjustment_date": ""
        },
        "regulations": [],
        "backspace_written_form": [],
        "duties": [],
        "legal_context": ""
    }
    section = None
    for line in summary_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("PARTIES"):
            section = "parties"
            continue
        elif line.startswith("RESIDENTIAL PROPERTY"):
            section = "residential_property"
            continue
        elif line.startswith("RENTAL PERIOD & COSTS"):
            section = "rental_period_costs"
            continue
        elif line.startswith("REGULATIONS"):
            section = "regulations"
            continue
        elif line.startswith("BACKSPACE & WRITTEN FORM"):
            section = "backspace_written_form"
            continue
        elif line.startswith("DUTIES"):
            section = "duties"
            continue
        elif line.startswith("Gesetzlicher Kontext"):
            section = "legal_context"
            continue
        if section == "parties":
            if line.lower().startswith("- vermieter"):
                data["parties"]["landlord"] = extract_value(line)
            elif line.lower().startswith("- mieter"):
                data["parties"]["tenant"] = extract_value(line)
        elif section == "residential_property":
            if line.lower().startswith("- adresse"):
                data["residential_property"]["address"] = extract_value(line)
            elif line.lower().startswith("- zimmer"):
                data["residential_property"]["rooms"] = extract_value(line)
            elif line.lower().startswith("- groesse"):
                data["residential_property"]["size"] = extract_value(line)
            elif line.lower().startswith("- besonderheiten"):
                data["residential_property"]["special_features"] = extract_value(line)
            elif line.lower().startswith("- baujahr"):
                data["residential_property"]["building_year"] = extract_value(line)
        elif section == "rental_period_costs":
            if line.lower().startswith("- startdatum"):
                data["rental_period_costs"]["start_date"] = extract_value(line)
            elif line.lower().startswith("- mietdauer"):
                data["rental_period_costs"]["duration"] = extract_value(line)
            elif line.lower().startswith("- miete"):
                data["rental_period_costs"]["rent"] = extract_value(line)
            elif line.lower().startswith("- kaution"):
                data["rental_period_costs"]["deposit"] = extract_value(line)
            elif line.lower().startswith("- letzte anpassung"):
                data["rental_period_costs"]["last_adjustment_date"] = extract_value(line)
        elif section == "regulations":
            if line.startswith("-"):
                data["regulations"].append(line.lstrip("- ").strip())
        elif section == "backspace_written_form":
            if line.startswith("-"):
                data["backspace_written_form"].append(line.lstrip("- ").strip())
        elif section == "duties":
            if line.startswith("-"):
                data["duties"].append(line.lstrip("- ").strip())
        elif section == "legal_context":
            data["legal_context"] += (line + " ")
    data["legal_context"] = data["legal_context"].strip()
    return data


def generate_contract_summary(contract_text):
    """Generate structured contract summary using Groq API"""
    prompt_summary = f"""
Bitte fasse folgenden österreichischen Mietvertrag zusammen:

{contract_text}

Antworte im folgendem Format:

PARTIES:
- Vermieter: ...
- Mieter: ...

RESIDENTIAL PROPERTY:
- Adresse: ...
- Zimmer: ...
- Groesse: ...
- Besonderheiten: ...
- Baujahr: ...

RENTAL PERIOD & COSTS:
- Startdatum: ...
- Mietdauer: ...
- Miete: ...
- Kaution: ...
- Letzte Anpassung: ...

REGULATIONS:
- ...

BACKSPACE & WRITTEN FORM:
- ...

DUTIES:
- ...

Gesetzlicher Kontext:
- ...
"""
    return query_groq(prompt_summary)