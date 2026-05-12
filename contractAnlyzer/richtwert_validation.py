import re

def parse_rent_value(rent_str_or_number):
    """
    Utility: parse rent text to float (same approach as vpi_validation).
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


def calculate_richtwert_rent(building_year, size_m2):
    richtwert_rates = {
        "before_1945": 5.81,
        "1945_1967": 5.97,
        "1968_1980": 6.20,
        "1981_1990": 6.50,
        "1991_2000": 7.20,
        "2001_2010": 8.50,
        "after_2010": 9.50
    }
    try:
        year = int(building_year)
    except Exception:
        return None, "Baujahr ungültig für Richtwert-Berechnung"

    if year < 1945:
        rate = richtwert_rates["before_1945"]
    elif year <= 1967:
        rate = richtwert_rates["1945_1967"]
    elif year <= 1980:
        rate = richtwert_rates["1968_1980"]
    elif year <= 1990:
        rate = richtwert_rates["1981_1990"]
    elif year <= 2000:
        rate = richtwert_rates["1991_2000"]
    elif year <= 2010:
        rate = richtwert_rates["2001_2010"]
    else:
        rate = richtwert_rates["after_2010"]

    max_rent = rate * size_m2
    return max_rent, f"Richtwert für Baujahr {year}: {rate}€/m²"


def richtwert_validation(contract_json):
    """
    contract_json: structured_summary
    Returns dict with applicable, max_rent_allowed, valid, current_rent, excess_amount, excess_percent
    NOTE: Removed 'comment' field as requested.
    """
    is_full_mrg = contract_json.get("is_full_mrg", False)
    building_year = contract_json.get("residential_property", {}).get("building_year", "")
    size_str = contract_json.get("residential_property", {}).get("size", "")
    rent_raw = contract_json.get("rental_period_costs", {}).get("rent", "")

    # parse size (e.g. "120 m²")
    size_m2 = None
    if size_str:
        m = re.search(r"(\d+\.?\d*)\s*m²", size_str.replace(" ", ""))
        if m:
            try:
                size_m2 = float(m.group(1))
            except Exception:
                size_m2 = None
        else:
            # try just number
            try:
                size_m2 = float(re.sub(r"[^\d.]", "", size_str))
            except Exception:
                size_m2 = None

    current_rent = parse_rent_value(rent_raw)

    if not is_full_mrg:
        return {
            "applicable": False,
            "max_rent_allowed": "Nicht anwendbar - Keine volle MRG-Anwendung",
            "valid": "Nicht anwendbar"
        }

    if not building_year:
        return {
            "applicable": True,
            "max_rent_allowed": "Nicht verfügbar",
            "valid": "Nicht verfügbar",
            "error": "Baujahr der Immobilie im Vertrag nicht verfügbar"
        }

    if not size_m2:
        return {
            "applicable": True,
            "max_rent_allowed": "Nicht verfügbar",
            "valid": "Nicht verfügbar",
            "error": "Wohnungsgröße im Vertrag nicht verfügbar"
        }

    if current_rent is None:
        return {
            "applicable": True,
            "max_rent_allowed": "Nicht verfügbar",
            "valid": "Nicht verfügbar",
            "error": "Aktuelle Miete im Vertrag nicht verfügbar"
        }

    max_rent, calc_info = calculate_richtwert_rent(building_year, size_m2)
    if max_rent is None:
        return {
            "applicable": True,
            "max_rent_allowed": "Nicht verfügbar",
            "valid": "Nicht verfügbar",
            "error": calc_info
        }

    is_valid = current_rent <= max_rent
    excess_amount = (current_rent - max_rent) if current_rent > max_rent else 0.0
    excess_percent = (excess_amount / max_rent * 100) if max_rent > 0 else 0.0

    return {
        "applicable": True,
        "max_rent_allowed": f"{max_rent:.2f}€",
        "valid": "Gültig" if is_valid else "Überschreitung des Richtwerts",
        "current_rent": current_rent,
        "excess_amount": excess_amount,
        "excess_percent": f"{excess_percent:.1f}%",
        "calculation_info": calc_info
    }