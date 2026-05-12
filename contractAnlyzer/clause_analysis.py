import json
import re
from groq_client import query_groq
import pymongo
from config import MONGO_URI, DB_NAME


def get_legal_sources_for_chunks(relevant_chunks):
    """
    Get legal source information for relevant chunks
    """
    sources = []
    unique_files = set()
    
    try:
        client = pymongo.MongoClient(MONGO_URI)
        collection = client[DB_NAME]["embeddings"]
        
        for chunk in relevant_chunks:
            # Find the document in MongoDB that contains this chunk
            doc = collection.find_one({"chunk": chunk})
            if doc and doc.get("file"):
                file_name = doc["file"]
                if file_name not in unique_files:
                    unique_files.add(file_name)
                    
                    # Extract year and law info from filename
                    year_match = re.search(r'(\d{4})', file_name)
                    year = year_match.group(1) if year_match else "Unknown"
                    
                    # Create source info based on filename patterns
                    source_info = create_source_info(file_name, year)
                    if source_info:
                        sources.append(source_info)
        
        client.close()
    except Exception as e:
        print(f"Could not connect to database for source attribution: {e}")
    
    return sources


def create_source_info(filename, year):
    """
    Create standardized source information based on filename
    """
    filename_lower = filename.lower()
    
    # Map common Austrian legal documents
    if "mrg" in filename_lower or "mietrecht" in filename_lower:
        return {
            "law_name": "Mietrechtsgesetz (MRG)",
            "reference": f"BGBl. Nr. 520/1981, Stand {year}",
            "url": "https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10002531",
            "description": "Österreichisches Mietrechtsgesetz"
        }
    elif "abgb" in filename_lower:
        return {
            "law_name": "Allgemeines Bürgerliches Gesetzbuch (ABGB)",
            "reference": f"JGS Nr. 946/1811, Stand {year}",
            "url": "https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10001622",
            "description": "Österreichisches Zivilrecht"
        }
    elif "kschg" in filename_lower or "konsument" in filename_lower:
        return {
            "law_name": "Konsumentenschutzgesetz (KSchG)",
            "reference": f"BGBl. Nr. 140/1979, Stand {year}",
            "url": "https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10002462",
            "description": "Konsumentenschutzbestimmungen"
        }
    else:
        # Generic federal law
        return {
            "law_name": f"Bundesgesetz {year}",
            "reference": f"Bundesgesetzblatt {year}",
            "url": "https://www.ris.bka.gv.at/",
            "description": "Österreichische Bundesgesetzgebung"
        }


def parse_clauses_with_sources(response_text, relevant_context_chunks=None):
    """
    Enhanced parsing that includes legal source attribution
    """
    illegal, questionable, legal = [], [], []
    current_category = None
    
    # Get legal sources if context chunks provided
    legal_sources = []
    if relevant_context_chunks:
        legal_sources = get_legal_sources_for_chunks(relevant_context_chunks)
    
    print(f"Parsing response: {response_text[:500]}...")  # Debug output
    
    lines = response_text.splitlines()
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        lower_line = line.lower()
        
        # Check for section headers - more flexible matching
        if any(phrase in lower_line for phrase in ["illegale klauseln", "illegal clauses", "illegale bestimmungen"]):
            current_category = "illegal"
            continue
        elif any(phrase in lower_line for phrase in ["fragwuerdige klauseln", "fragwürdige klauseln", "questionable clauses", "problematische klauseln"]):
            current_category = "questionable"
            continue
        elif any(phrase in lower_line for phrase in ["legale klauseln", "legal clauses", "rechtmäßige klauseln", "gültige klauseln"]):
            current_category = "legal"
            continue
        
        # Check for bullet points or numbered items
        if current_category and (line.startswith("-") or line.startswith("•") or line.startswith("*") or re.match(r'^\d+\.', line)):
            clause = re.sub(r'^[-•*\d\.]\s*', '', line).strip()
            
            # Skip empty clauses or "none found" messages
            if clause and not any(skip in clause.lower() for skip in ["keine gefunden", "none found", "nicht vorhanden", "keine", "none"]):
                clause_with_source = {
                    "clause": clause,
                    "legal_sources": legal_sources  # Add sources to each clause
                }
                
                if current_category == "illegal":
                    illegal.append(clause_with_source)
                elif current_category == "questionable":
                    questionable.append(clause_with_source)
                elif current_category == "legal":
                    legal.append(clause_with_source)
    
    # If we didn't find properly formatted sections, try alternative parsing
    if not illegal and not questionable and not legal:
        print("Alternative parsing attempted...")
        # Look for any mention of clauses in the text
        sentences = re.split(r'[.!?]\s+', response_text)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20:  # Only consider substantial sentences
                clause_with_source = {
                    "clause": sentence,
                    "legal_sources": legal_sources
                }
                
                # Classify based on keywords
                if any(word in sentence.lower() for word in ["illegal", "rechtswidrig", "unzulässig", "verboten"]):
                    illegal.append(clause_with_source)
                elif any(word in sentence.lower() for word in ["fragwürdig", "problematisch", "bedenklich"]):
                    questionable.append(clause_with_source)
                elif any(word in sentence.lower() for word in ["zulässig", "legal", "rechtmäßig", "gültig"]):
                    legal.append(clause_with_source)
    
    print(f"Parsed with sources - Illegal: {len(illegal)}, Questionable: {len(questionable)}, Legal: {len(legal)}")
    return illegal, questionable, legal, legal_sources


def parse_clauses(response_text):
    """
    Enhanced parsing with better error handling and multiple format support
    """
    illegal, questionable, legal = [], [], []
    current_category = None
    
    print(f"Parsing response: {response_text[:500]}...")  # Debug output
    
    lines = response_text.splitlines()
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        lower_line = line.lower()
        
        # Check for section headers - more flexible matching
        if any(phrase in lower_line for phrase in ["illegale klauseln", "illegal clauses", "illegale bestimmungen"]):
            current_category = "illegal"
            continue
        elif any(phrase in lower_line for phrase in ["fragwuerdige klauseln", "fragwürdige klauseln", "questionable clauses", "problematische klauseln"]):
            current_category = "questionable"
            continue
        elif any(phrase in lower_line for phrase in ["legale klauseln", "legal clauses", "rechtmäßige klauseln", "gültige klauseln"]):
            current_category = "legal"
            continue
        
        # Check for bullet points or numbered items
        if current_category and (line.startswith("-") or line.startswith("•") or line.startswith("*") or re.match(r'^\d+\.', line)):
            clause = re.sub(r'^[-•*\d\.]\s*', '', line).strip()
            
            # Skip empty clauses or "none found" messages
            if clause and not any(skip in clause.lower() for skip in ["keine gefunden", "none found", "nicht vorhanden", "keine", "none"]):
                if current_category == "illegal":
                    illegal.append(clause)
                elif current_category == "questionable":
                    questionable.append(clause)
                elif current_category == "legal":
                    legal.append(clause)
    
    # If we didn't find properly formatted sections, try alternative parsing
    if not illegal and not questionable and not legal:
        print("Alternative parsing attempted...")
        # Look for any mention of clauses in the text
        sentences = re.split(r'[.!?]\s+', response_text)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20:  # Only consider substantial sentences
                # Classify based on keywords
                if any(word in sentence.lower() for word in ["illegal", "rechtswidrig", "unzulässig", "verboten"]):
                    illegal.append(sentence)
                elif any(word in sentence.lower() for word in ["fragwürdig", "problematisch", "bedenklich"]):
                    questionable.append(sentence)
                elif any(word in sentence.lower() for word in ["zulässig", "legal", "rechtmäßig", "gültig"]):
                    legal.append(sentence)
    
    print(f"Parsed - Illegal: {len(illegal)}, Questionable: {len(questionable)}, Legal: {len(legal)}")
    return illegal, questionable, legal


def find_indexation_clause(illegal_clauses, questionable_clauses, legal_clauses, contract_text):
    """
    Enhanced indexation clause detection
    """
    keywords = [
        "wertsicherung", "index", "mietanpassung", "preisindex", "inflation", 
        "teuerung", "verbraucherpreisindex", "vpi", "anpassung", "erhöhung",
        "valorisierung", "indexierung", "mieterhöhung", "indexklausel"
    ]
    
    # First check in classified clauses
    all_clauses = illegal_clauses + questionable_clauses + legal_clauses
    for clause in all_clauses:
        # Handle both string and dict formats
        clause_text = clause if isinstance(clause, str) else clause.get("clause", "")
        if any(kw in clause_text.lower() for kw in keywords):
            return clause_text
    
    # Then check in contract text by sentences
    sentences = re.split(r'[.!?]\n', contract_text)
    for sentence in sentences:
        sentence = sentence.strip()
        if any(kw in sentence.lower() for kw in keywords) and len(sentence) > 20:
            return sentence
    
    # Finally check by lines
    for line in contract_text.splitlines():
        line = line.strip()
        if any(kw in line.lower() for kw in keywords) and len(line) > 20:
            return line
    
    return None


def analyze_indexation_clause(index_clause_text):
    """
    Enhanced indexation clause analysis with better error handling
    """
    prompt = f"""
Du bist ein juristischer Experte für österreichische Mietverträge.

Analysiere folgende Wertsicherungsklausel im Vertrag exakt und antworte ausschließlich im JSON-Format mit folgenden Feldern:
{{
  "index_name": "Name des Verbraucherpreisindexes, z.B. 'VPI 2020' oder 'VPI 2015' oder 'Keine Angabe'",
  "threshold_percent": "Schwellenwert in %, z.B. 3, 5, oder 0 wenn keiner",
  "symmetric_adjustment": true oder false (ob sowohl Erhöhung als auch Senkung möglich ist),
  "adjustment_interval": "z.B. 'jährlich', 'halbjährlich' oder 'Keine Angabe'"
}}

Text der Klausel:
\"\"\"
{index_clause_text}
\"\"\"

Antworte NUR mit dem JSON-Objekt, keine zusätzlichen Erklärungen.
"""
    
    try:
        response = query_groq(prompt)
        print(f"Indexation analysis response: {response}")  # Debug
        
        # Try to extract JSON from response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            parsed = json.loads(json_str)
        else:
            parsed = json.loads(response)
            
        # Validate required fields
        required_fields = ["index_name", "threshold_percent", "symmetric_adjustment", "adjustment_interval"]
        for field in required_fields:
            if field not in parsed:
                parsed[field] = "Keine Angabe" if field != "symmetric_adjustment" else False
                
        return parsed
        
    except Exception as e:
        print(f"Error parsing indexation clause: {e}")
        return {
            "index_name": "Keine Angabe",
            "threshold_percent": 0,
            "symmetric_adjustment": False,
            "adjustment_interval": "Keine Angabe",
            "error": str(e)
        }


def analyze_richtwert_clause(richtwert_clause_text):
    """
    Enhanced richtwert clause analysis
    """
    prompt = f"""
Du bist ein juristischer Experte für österreichische Mietverträge.

Analysiere folgende Richtwertmietzins-Klausel im Vertrag exakt und antworte ausschließlich im JSON-Format mit diesen Feldern:
{{
  "applicable": true oder false,
  "max_rent_allowed": "Maximal zulässige Miete laut Richtwertmietzins oder 'Nicht angegeben'",
  "valid": true oder false,
  "comment": "Erklärung oder Anmerkung zur Klausel"
}}

Text der Klausel:
\"\"\"
{richtwert_clause_text}
\"\"\"

Antworte NUR mit dem JSON-Objekt.
"""
    
    try:
        response = query_groq(prompt)
        
        # Try to extract JSON from response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            parsed = json.loads(json_str)
        else:
            parsed = json.loads(response)
            
        return parsed
        
    except Exception as e:
        print(f"Error parsing richtwert clause: {e}")
        return {
            "applicable": False,
            "max_rent_allowed": "Nicht angegeben",
            "valid": False,
            "comment": f"Konnte die Klausel nicht analysieren: {str(e)}"
        }


def detect_full_mrg(contract_text):
    """
    Detect if full Mietrechtsgesetz (MRG) application is declared in contract text.
    Enhanced with more patterns and better detection.
    """
    patterns = [
        r"volle\s+anwendung\s+des\s+mietrechtsgesetzes",
        r"vollständige\s+mrg-anwendung",
        r"volle\s+mrg-anwendung",
        r"voll\s+mrg",
        r"mrg\s+vollständig",
        r"vollständiges\s+mrg",
        r"mietrechtsgesetz\s+vollständig",
        r"mrg\s+in\s+voller\s+anwendung",
        r"anwendung\s+des\s+mrg\s+in\s+voller\s+höhe",
        r"mrg\s+volle\s+anwendung",
        r"volle\s+mrg",
        r"das\s+mietrechtsgesetz.*vollständig.*anwendbar",
        r"vollumfängliche\s+anwendung.*mrg"
    ]
    
    text_lower = contract_text.lower()
    
    for pattern in patterns:
        if re.search(pattern, text_lower):
            print(f"Found full MRG pattern: {pattern}")  # Debug
            return True
    
    # Also check for explicit mentions that might indicate full MRG
    full_mrg_indicators = [
        "richtwertmietzins",
        "richtwert.*anwendbar",
        "mietrechtsgesetz.*vollständig"
    ]
    
    for indicator in full_mrg_indicators:
        if re.search(indicator, text_lower):
            print(f"Found MRG indicator: {indicator}")  # Debug
            return True
    
    return False