import json
import re
from datetime import datetime, date


def load_avg_rent_data():
    """Load the average rent data from avg_rent.json"""
    try:
        with open('avg_rent.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("Warning: avg_rent.json not found. Using fallback data.")
        return None
    except json.JSONDecodeError:
        print("Warning: Invalid JSON in avg_rent.json. Using fallback data.")
        return None


def extract_location_from_address(address):
    """
    Extract federal state from address string.
    Returns the federal state name or 'Wien' as default for Vienna addresses.
    """
    if not address or address.lower() in ["nicht im vertrag verfügbar", "", "unbekannt"]:
        return "Wien" 
    
    address_lower = address.lower()
    
    state_mappings = {
        "wien": "Wien",
        "vienna": "Wien",
        "salzburg": "Salzburg",
        "tirol": "Tirol",
        "tyrol": "Tirol",
        "innsbruck": "Tirol",
        "vorarlberg": "Vorarlberg",
        "bregenz": "Vorarlberg",
        "kärnten": "Kärnten",
        "carinthia": "Kärnten",
        "klagenfurt": "Kärnten",
        "steiermark": "Steiermark",
        "styria": "Steiermark",
        "graz": "Steiermark",
        "oberösterreich": "Oberösterreich",
        "upper austria": "Oberösterreich",
        "linz": "Oberösterreich",
        "niederösterreich": "Niederösterreich",
        "lower austria": "Niederösterreich",
        "st. pölten": "Niederösterreich",
        "burgenland": "Burgenland",
        "eisenstadt": "Burgenland"
    }
    
    for key, state in state_mappings.items():
        if key in address_lower:
            return state
    
    return "Wien"


def get_federal_state_rent_data(avg_rent_data, federal_state):
    """Get rent data for a specific federal state"""
    if not avg_rent_data:
        return None
        
    federal_states = avg_rent_data.get("regional_breakdown_2024", {}).get("federal_states", {})
    
    if federal_state in federal_states:
        return federal_states[federal_state]
    
    return federal_states.get("Austria_Total", None)


def calculate_contract_duration_adjustment(start_date_str):
    """
    Calculate adjustment factor based on contract duration.
    Newer contracts typically have higher rents.
    """
    if not start_date_str or start_date_str.lower() in ["unbekannt", "", "keine angabe"]:
        return 1.0  
    
    try:
        start_date = datetime.strptime(start_date_str, "%d.%m.%Y").date()
        current_date = date.today()
        years_since_start = (current_date - start_date).days / 365.25
        
        if years_since_start < 2:
            return 1.15  
        elif years_since_start < 5:
            return 1.08  
        elif years_since_start < 10:
            return 1.02  
        elif years_since_start < 20:
            return 0.95 
        else:
            return 0.85  
            
    except ValueError:
        return 1.0  


def get_average_rent_per_sqm(address, start_date_str=None, size_m2=None, rooms=None):
    """
    Calculate location-specific average rent per m² using the JSON data.
    
    Args:
        address: Address string to extract location
        start_date_str: Contract start date for duration adjustment
        size_m2: Apartment size for size-based adjustments
        rooms: Number of rooms for additional context
    
    Returns:
        tuple: (rent_per_sqm, data_source_info)
    """
    
    avg_rent_data = load_avg_rent_data()
    
    if not avg_rent_data:
        return 10.1, "Fallback data (avg_rent.json not available)"
    
    federal_state = extract_location_from_address(address)

    state_data = get_federal_state_rent_data(avg_rent_data, federal_state)
    
    if not state_data:
        austria_total = avg_rent_data.get("regional_breakdown_2024", {}).get("federal_states", {}).get("Austria_Total", {})
        base_rent_per_sqm = austria_total.get("rent_with_operating_costs", {}).get("per_sqm", 9.8)
        data_source = "Austria Total (fallback)"
    else:
        base_rent_per_sqm = state_data.get("rent_with_operating_costs", {}).get("per_sqm", 9.8)
        data_source = f"{federal_state} regional data"
    
    duration_adjustment = calculate_contract_duration_adjustment(start_date_str)
    
    size_adjustment = 1.0
    if size_m2:
        if size_m2 < 40:
            size_adjustment = 1.12  
        elif size_m2 < 60:
            size_adjustment = 1.05  
        elif size_m2 > 100:
            size_adjustment = 0.95  
    
    adjusted_rent_per_sqm = base_rent_per_sqm * duration_adjustment * size_adjustment
    
    adjustments_info = []
    if duration_adjustment != 1.0:
        adjustments_info.append(f"duration adj: {duration_adjustment:.2f}")
    if size_adjustment != 1.0:
        adjustments_info.append(f"size adj: {size_adjustment:.2f}")
    
    adjustment_text = f" (adjustments: {', '.join(adjustments_info)})" if adjustments_info else ""
    data_source_info = f"{data_source}, base: {base_rent_per_sqm}€/m²{adjustment_text}"
    
    return round(adjusted_rent_per_sqm, 2), data_source_info


def create_rent_comparison_prompt(rent_str, size_str, rooms, features, address, start_date_str=None):
    """
    Enhanced rent comparison prompt using dynamic average rent calculation.
    """
    rent_amount = None
    size_m2 = None
    
    match_rent = re.search(r"(\d+[\.,]?\d*)", rent_str)
    if match_rent:
        rent_amount = float(match_rent.group(1).replace(",", "."))
    
    match_size = re.search(r"(\d+\.?\d*)\s*m²", size_str)
    if match_size:
        size_m2 = float(match_size.group(1))
    
    average_rent_per_m2, data_source_info = get_average_rent_per_sqm(
        address, start_date_str, size_m2, rooms
    )
    
    expected_rent = None
    if size_m2 is not None and average_rent_per_m2 is not None:
        expected_rent = size_m2 * average_rent_per_m2
    
    federal_state = extract_location_from_address(address)
    location_context = f"in {federal_state}" if federal_state != "Wien" else "in Wien"
    
    # Safe formatting
    avg_rent_str = f"{average_rent_per_m2:.2f}" if average_rent_per_m2 is not None else "N/A"
    expected_rent_str = f"{expected_rent:.2f}" if expected_rent is not None else "N/A"
    
    prompt = f"""
Du bist ein österreichischer Mietexperte.

Die tatsächliche Monatsmiete beträgt {rent_str}.

Die Wohnung hat eine Größe von {size_str}, mit folgenden Merkmalen: {rooms}, {features}.

Die Adresse lautet: {address}.

Der aktuelle durchschnittliche Mietpreis {location_context} beträgt {avg_rent_str} EUR/m², also ca. {expected_rent_str} EUR für diese Wohnung.

Datenquelle: {data_source_info}

Vergleiche die tatsächliche Miete mit dem durchschnittlichen Mietspiegel und gib eine Aussage in folgendem Format aus:

+X% über dem Mietspiegel {location_context}  
oder  
-X% unter dem Mietspiegel {location_context}  
oder  
+0% über dem Mietspiegel {location_context} (wenn gleich)

KEINE weiteren Erklärungen, nur die reine Vergleichsangabe.
"""
    return prompt


def split_rent_comparison(rent_comparison_str):
    parts = rent_comparison_str.split(" ", 1)
    if len(parts) == 2:
        percent, text = parts
    else:
        percent = rent_comparison_str
        text = ""
    return percent, text