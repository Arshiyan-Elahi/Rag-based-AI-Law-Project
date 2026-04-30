import os
import json
import re
from typing import List, Dict, Any, Optional
from huggingface_hub import InferenceClient
from .pdf_extractor import extract_traceable_text

# Prompts imported from the logic provided in the audit
SYSTEM_PROMPT = """
You are an AI Client Profile Detection Engine for SOP documents.
STRICT RULES:
- Return RAW JSON ONLY.
- DO NOT use markdown fences.
- If a field is not present, return null or an empty list.
- SUMMARY: mandatory, 4-6 sentences.
- DETECT: document type, domain, writing style, terminology, profile suggestions.
- profile_suggestions must include: suggestion_type, suggested_rule, evidence_from_document, confidence_score.
"""

MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"

def _get_hf_client():
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        # Check if we are in a dev environment with a .env file loaded
        from dotenv import load_dotenv
        load_dotenv()
        hf_token = os.getenv("HF_TOKEN")
    
    if not hf_token:
        raise ValueError("HF_TOKEN not found in environment variables.")
    return InferenceClient(token=hf_token)

def analyze_sop_traceable(file_obj) -> Dict[str, Any]:
    """
    Extracts text with traceability and analyzes it for profile suggestions.
    """
    client = _get_hf_client()
    
    # Phase 2: Traceable Extraction
    traceable_chunks = extract_traceable_text(file_obj)
    
    # Combine chunks for analysis (or process in batches if too large)
    # For now, we'll process the first 15000 chars for a quick profile
    full_text = ""
    for chunk in traceable_chunks:
        full_text += f"\n\n{chunk['text']}"
        if len(full_text) > 15000:
            break
            
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Analyze the following SOP text and return a JSON profile:\n\n{full_text}"}
    ]
    
    response = client.chat_completion(
        model=MODEL_ID,
        messages=messages,
        max_tokens=2000,
        temperature=0.1
    )
    
    raw_content = response.choices[0].message.content
    result = _extract_json(raw_content)
    
    # Integrate Traceability into Evidence
    if "profile_suggestions" in result:
        for suggestion in result["profile_suggestions"]:
            evidence_snippet = suggestion.get("evidence_from_document", "")
            if evidence_snippet:
                # Find matching traceable chunk
                match = _find_best_match(evidence_snippet, traceable_chunks)
                if match:
                    suggestion["evidence_metadata"] = {
                        "page": match["page"],
                        "section": match["section"],
                        "paragraph_index": match["paragraph_index"],
                        "traceability_id": match["traceability_id"]
                    }
                    
    return result

def _extract_json(content):
    content = content.strip()
    json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if json_match:
        content = json_match.group(1).strip()
    
    start_idx = content.find('{')
    end_idx = content.rfind('}')
    if start_idx != -1 and end_idx != -1:
        content = content[start_idx:end_idx + 1]
    
    try:
        return json.loads(content)
    except:
        return {}

def _find_best_match(snippet: str, chunks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Finds the traceable chunk that most likely contains the evidence snippet.
    """
    snippet_clean = re.sub(r'\s+', ' ', snippet.lower().strip())
    if not snippet_clean:
        return None
        
    for chunk in chunks:
        chunk_clean = re.sub(r'\s+', ' ', chunk["text"].lower().strip())
        if snippet_clean in chunk_clean or chunk_clean in snippet_clean:
            return chunk
            
    # Fallback: check if a significant part of the snippet is in the chunk
    words = snippet_clean.split()
    if len(words) > 5:
        target = " ".join(words[:5])
        for chunk in chunks:
            if target in chunk["text"].lower():
                return chunk
                
    return None
