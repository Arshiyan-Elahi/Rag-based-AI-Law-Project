import json
import os
import re
from datetime import datetime, date

# ---------- Helpers / Data loading ----------

def _cpi_file_path():
    return os.path.join(os.path.dirname(__file__), "cpi_data.json")

def load_cpi_data():
    """
    Load CPI data and return a lookup dict:
      - annual: { "2024": value }
      - monthly: { "2024-01": value }
    Uses the CPI field 'CPI_2020' if present, otherwise tries other CPI_* columns.
    """
    path = _cpi_file_path()
    if not os.path.exists(path):
        return {"annual": {}, "monthly": {}}

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    data_list = raw.get("data") or []
    annual = {}
    monthly = {}

    # prefer CPI_2020 if present, otherwise fallbacks
    preferred_keys = ["CPI_2020", "CPI_2015", "CPI_2010", "CPI_2005"]

    for item in data_list:
        period = item.get("period", "")
        item_type = item.get("type", "").lower()
        # pick the first non-null preferred key
        val = None
        for k in preferred_keys:
            v = item.get(k)
            if v is not None:
                val = v
                break
        if val is None:
            # as fallback try 'CPI_2015' or any numeric CPI field
            for k, v in item.items():
                if k.startswith("CPI_") and isinstance(v, (int, float)):
                    val = v
                    break

        if val is None:
            continue

        if item_type in ("annual_average", "annual"):
            # period like "2024"
            annual[period] = float(val)
        else:
            # monthly like "2024-01" or "2012-01"
            monthly[period] = float(val)

    return {"annual": annual, "monthly": monthly}


def parse_date_german_or_iso(date_str):
    if not date_str:
        return None
    ds = date_str.strip()
    if ds.lower() in ["unbekannt", "keine angabe", ""]:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(ds, fmt).date()
        except Exception:
            continue
    # try year-only
    m = re.match(r"^\s*(\d{4})\s*$", ds)
    if m:
        return date(int(m.group(1)), 1, 1)
    return None


def _get_cpi_for_date(cpi_index, target_date):
    """
    cpi_index: returned by load_cpi_data()
    Try monthly first (YYYY-MM), then annual (YYYY).
    """
    if not target_date:
        return None
    ym = f"{target_date.year}-{target_date.month:02d}"
    if ym in cpi_index["monthly"]:
        return cpi_index["monthly"][ym]
    y = str(target_date.year)
    if y in cpi_index["annual"]:
        return cpi_index["annual"][y]
    return None


def parse_rent_value(rent_str_or_number):
    """
    Parse rent strings like "3.500,00 EUR" or "3500" or numeric float/int.
    Returns float or None.
    """
    if rent_str_or_number is None:
        return None
    if isinstance(rent_str_or_number, (int, float)):
        return float(rent_str_or_number)
    s = str(rent_str_or_number).strip()
    if not s:
        return None
    # Remove currency symbols and whitespace
    s = re.sub(r"[^\d,.\-]", "", s)
    # Remove thousand separators (.) and unify decimal separator to dot
    # But be careful when string like "3.500" - we treat '.' as thousands sep
    # Approach: if both '.' and ',' present, treat '.' as thousands, ',' as decimal.
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        # if only dots present and have 3-digit groups -> remove dots
        if s.count(".") and re.search(r"\.\d{3}($|\D)", s):
            s = s.replace(".", "")
        # replace commas with dots (decimal)
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

# ---------- Main VPI logic ----------

def calculate_vpi_adjustment(start_date, last_adjustment_date, base_rent, current_date=None):
    """
    Use CPI data to calculate expected rent by index change between last_adjustment_date (or start_date)
    and current_date (default today). Returns (expected_rent, info_string) or (None, msg).
    """
    if current_date is None:
        current_date = date.today()

    cpi_index = load_cpi_data()
    if not cpi_index["annual"] and not cpi_index["monthly"]:
        return None, "VPI-Daten nicht verfügbar"

    # prefer last_adjustment_date for base, else start_date
    base_date = last_adjustment_date or start_date
    base_vpi = _get_cpi_for_date(cpi_index, base_date)
    current_vpi = _get_cpi_for_date(cpi_index, current_date)

    if base_vpi is None or current_vpi is None:
        return None, "VPI-Daten nicht verfügbar"

    adjustment_factor = current_vpi / base_vpi
    expected_rent = base_rent * adjustment_factor
    return expected_rent, f"VPI-Anpassung von {base_vpi} auf {current_vpi}"


def vpi_rent_validation(contract_json):
    """
    contract_json: structured_summary JSON (same shape you're using).
    Returns a dict with comment, indexation_valid, expected_rent, current_rent, difference_percent, within_tolerance.
    """
    start_date_str = contract_json.get("rental_period_costs", {}).get("start_date", "")
    last_adj_str = contract_json.get("rental_period_costs", {}).get("last_adjustment_date", "")
    rent_raw = contract_json.get("rental_period_costs", {}).get("rent", "")

    rent_amount = parse_rent_value(rent_raw)
    if rent_amount is None:
        return {
            "comment": "Mietbetrag im Vertrag nicht verfügbar für VPI Validierung.",
            "indexation_valid": "Keine Angabe"
        }

    start_date = parse_date_german_or_iso(start_date_str)
    if not start_date:
        return {
            "comment": "Vertragsbeginn-Datum im Vertrag nicht verfügbar für VPI Validierung.",
            "indexation_valid": "Keine Angabe"
        }

    last_adj_date = parse_date_german_or_iso(last_adj_str) or start_date

    expected_rent, calc_info = calculate_vpi_adjustment(start_date, last_adj_date, rent_amount)
    if expected_rent is None:
        return {
            "comment": calc_info,
            "indexation_valid": "Keine Angabe",
            "base_year_or_start_date": start_date_str,
            "last_adjustment_date": last_adj_str,
            "expected_rent": "Nicht verfügbar",
            "current_rent": rent_amount
        }

    difference = rent_amount - expected_rent
    # tolerance 5% of expected rent (commonly used); adjust if desired
    tolerance = expected_rent * 0.05
    within_tolerance = abs(difference) <= tolerance
    difference_percent = (difference / expected_rent) * 100 if expected_rent != 0 else 0.0

    return {
        "comment": f"{calc_info}. Aktuelle Miete: {rent_amount:.2f}€, Erwartete Miete: {expected_rent:.2f}€",
        "indexation_valid": "Gültig" if within_tolerance else "Möglicherweise ungültig",
        "base_year_or_start_date": start_date_str,
        "last_adjustment_date": last_adj_str,
        "expected_rent": f"{expected_rent:.2f}€",
        "current_rent": rent_amount,
        "difference_percent": f"{difference_percent:+.1f}%",
        "within_tolerance": within_tolerance
    }
