import os
import json
import re
import math
import numpy as np
from langdetect import detect, detect_langs, DetectorFactory
import textstat
try:
    import pronouncing
except ImportError:
    pronouncing = None
import spacy

try:
    from sentence_transformers import SentenceTransformer as _SBertModel
    _sbert_cache = {}
    def _get_sbert():
        if "m" not in _sbert_cache:
            _sbert_cache["m"] = _SBertModel("all-MiniLM-L6-v2")
        return _sbert_cache["m"]
    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False
    def _get_sbert(): return None

try:
    from rapidfuzz import fuzz as _rfuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    _rfuzz = None

try:
    import dateparser as _dateparser
    DATEPARSER_AVAILABLE = True
except ImportError:
    DATEPARSER_AVAILABLE = False
    _dateparser = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

DetectorFactory.seed = 0

# Lazy load spacy models
_spacy_cache = {}
def _get_spacy(lang="en"):
    if lang not in _spacy_cache:
        model_name = SPACY_SUPPORT.get(lang, "en_core_web_sm")
        try:
            import spacy
            import numpy as np
            _spacy_cache[lang] = spacy.load(model_name)
        except Exception:
            return None
    return _spacy_cache[lang]

SPACY_SUPPORT = {
    "en": "en_core_web_sm",
    "de": "de_core_news_sm",
    "fr": "fr_core_news_sm",
    "es": "es_core_news_sm",
    "it": "it_core_news_sm",
}

def detect_language(text: str) -> dict:
    """Stage 1: Full language + script detection with bilingual flag and confidence."""
    try:
        lang_probs = detect_langs(text)
        lang_code  = lang_probs[0].lang
        confidence = round(lang_probs[0].prob, 3)
    except Exception:
        lang_code  = "en"
        confidence = 0.5

    total = max(len(text), 1)
    urdu_arabic = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    cyrillic    = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    cjk         = sum(1 for c in text if '\u4E00' <= c <= '\u9FFF')
    latin       = sum(1 for c in text if c.isascii() and c.isalpha())

    ua_r  = urdu_arabic / total
    cyr_r = cyrillic    / total
    cjk_r = cjk         / total
    lat_r = latin       / total

    if   ua_r  > 0.30: script_type = "arabic_urdu"
    elif cyr_r > 0.30: script_type = "cyrillic"
    elif cjk_r > 0.30: script_type = "cjk"
    elif (ua_r > 0.1 and lat_r > 0.1) or (cyr_r > 0.1 and lat_r > 0.1):
        script_type = "mixed"
    else: script_type = "latin"

    # Bilingual EN+DE detection — common in ISO/GMP environments
    words = text.split()
    tw = max(len(words), 1)
    de_tok = len(re.findall(r'\b(und|die|der|das|ist|ein|eine|f.r|mit|von|bei|auf|des|dem|nach|werden|durch|oder|auch|dieser|diese)\b', text, re.I))
    en_tok = len(re.findall(r'\b(the|and|for|with|from|this|that|shall|must|will|have|been|they|which|when|where|are|was|were)\b', text, re.I))
    de_ratio = de_tok / tw
    en_ratio = en_tok / tw
    is_bilingual  = de_ratio >= 0.05 and en_ratio >= 0.05 and lang_code in ("de", "en")
    bilingual_pair = ["de", "en"] if is_bilingual else []

    # Technical language density (acronyms or alphanumeric tokens)
    technical_tokens = len(re.findall(r'\b[A-Z0-9-]{3,}\b', text))
    technical_language_density = round(technical_tokens / tw, 3)

    # Simple code switching detection (alternating between English/German tokens in close proximity)
    has_code_switching = False
    if is_bilingual:
        # Check if they are mixed within the same paragraphs rather than just separate blocks
        paragraphs = text.split('\n\n')
        mixed_paragraphs = 0
        for p in paragraphs:
            ptw = max(len(p.split()), 1)
            p_de = len(re.findall(r'\b(und|die|der|das|ist|ein|eine)\b', p, re.I)) / ptw
            p_en = len(re.findall(r'\b(the|and|for|with|this|that|shall)\b', p, re.I)) / ptw
            if p_de >= 0.03 and p_en >= 0.03:
                mixed_paragraphs += 1
        if mixed_paragraphs > 0:
            has_code_switching = True

    # High acronym docs may need processing mode adjustment
    processing_mode = "standard"
    if is_bilingual:
        processing_mode = "bilingual_parallel"
    elif technical_language_density > 0.25:
        processing_mode = "technical_acronym_heavy"

    return {
        "lang_code":            lang_code,
        "primary_language":     lang_code,
        "iso_code":             lang_code,
        "confidence":           confidence,
        "is_bilingual":         is_bilingual,
        "bilingual_pair":       bilingual_pair,
        "script_type":          script_type,
        "script_ratios":        {"latin": round(lat_r,3), "arabic_urdu": round(ua_r,3),
                                 "cyrillic": round(cyr_r,3), "cjk": round(cjk_r,3)},
        "is_mixed_script":      script_type == "mixed",
        "has_urdu_script":      ua_r > 0.3,
        "has_code_switching":   has_code_switching,
        "technical_language_density": technical_language_density,
        "processing_mode":      processing_mode,
        "has_spacy_support":    lang_code in SPACY_SUPPORT,
        "spacy_model":          SPACY_SUPPORT.get(lang_code),
        "spacy_model_available": lang_code in SPACY_SUPPORT,
    }

def detect_writing_style(text: str) -> dict:
    """Stage 2: Detect the structural and typographic conventions."""
    lines = text.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return {"primary_style": "FREE_PROSE", "chunking_strategy_selected": "STRATEGY_H_SEMANTIC"}

    # Content density signals
    table_lines = sum(1 for l in lines if l.count('|') >= 2)
    bullet_lines = sum(1 for l in lines if re.match(r'^\s*[-*â€¢]\s+', l))
    blank_lines = sum(1 for l in lines if not l.strip())
    
    words_per_line = [len(l.split()) for l in lines if l.strip()]
    avg_line_length = sum(words_per_line) / len(words_per_line) if words_per_line else 0
    
    form_lines = sum(1 for l in lines if re.match(r'^[A-Z][A-Za-z\s]+:\s*(.*)$', l))
    
    table_density = table_lines / total_lines
    bullet_ratio = bullet_lines / total_lines
    blank_line_ratio = blank_lines / total_lines
    form_field_ratio = form_lines / total_lines
    prose_ratio = sum(1 for w in words_per_line if w > 12) / len(words_per_line) if words_per_line else 0
    
    # Numbering style signals
    deep_decimal = bool(re.search(r'^\s*\d+\.\d+\.\d+\s+', text, re.MULTILINE))
    standard_decimal = bool(re.search(r'^\s*\d+\.\d+\s+', text, re.MULTILINE))
    simple_numeric = bool(re.search(r'^\s*\d+[\.\)]\s+', text, re.MULTILINE))
    roman = bool(re.search(r'^\s*[IVX]+[\.\)]\s+', text, re.MULTILINE))
    military = bool(re.search(r'^\s*\d+\.[a-z]\.[ivx]+\.', text, re.MULTILINE))
    legal_article = bool(re.search(r'^\s*Article\s+[IVX\d]', text, re.MULTILINE | re.IGNORECASE))
    section_symbol = bool(re.search(r'^\s*§\s*\d+', text, re.MULTILINE))
    
    # Header styles
    markdown_header = bool(re.search(r'^\s*#{1,4}\s+', text, re.MULTILINE))
    
    # Style logic
    primary_style = "FREE_PROSE"
    chunk_strategy = "STRATEGY_H_SEMANTIC"
    
    if legal_article or section_symbol:
        primary_style = "LEGAL_CLAUSE"
        chunk_strategy = "STRATEGY_B_LEGAL_STRUCTURE"
    elif deep_decimal or standard_decimal:
        primary_style = "ISO_DECIMAL_NUMBERED"
        chunk_strategy = "STRATEGY_A_DECIMAL_NUMBERED"
    elif military:
        primary_style = "MILITARY_OUTLINE"
        chunk_strategy = "STRATEGY_C_OUTLINE"
    elif simple_numeric and not deep_decimal:
        primary_style = "SIMPLE_NUMBERED"
        chunk_strategy = "STRATEGY_A_DECIMAL_NUMBERED"
    elif table_density > 0.3:
        primary_style = "TABLE_DOMINANT"
        chunk_strategy = "STRATEGY_E_TABLE_AWARE"
    elif bullet_ratio > 0.4:
        primary_style = "BULLET_ONLY"
        chunk_strategy = "STRATEGY_D_HEADER_BULLET"
    elif form_field_ratio > 0.3:
        primary_style = "FORM_BASED"
        chunk_strategy = "STRATEGY_F_FIELD_VALUE"
    elif avg_line_length < 8 and bullet_ratio > 0.1:
        primary_style = "CHECKLIST"
        chunk_strategy = "STRATEGY_G_CHECKLIST"
    elif markdown_header:
        primary_style = "MARKDOWN_FORMATTED"
        chunk_strategy = "STRATEGY_I_MARKDOWN"
    elif (simple_numeric or standard_decimal) and (bullet_ratio > 0.1 or table_density > 0.1):
        primary_style = "MIXED_STRUCTURED"
        chunk_strategy = "STRATEGY_J_HIERARCHICAL_FALLBACK"

    return {
        "primary_style": primary_style,
        "secondary_style": "TABLE_DOMINANT" if table_density > 0.1 and primary_style != "TABLE_DOMINANT" else "PROSE",
        "numbering_type": "decimal" if deep_decimal or standard_decimal else "simple" if simple_numeric else "none",
        "header_style": "markdown" if markdown_header else "unknown",
        "max_nesting_depth": 3 if deep_decimal else 2 if standard_decimal else 1,
        "table_density": round(table_density, 3),
        "bullet_ratio": round(bullet_ratio, 3),
        "form_field_ratio": round(form_field_ratio, 3),
        "prose_ratio": round(prose_ratio, 3),
        "chunking_strategy_selected": chunk_strategy
    }

HEADER_PATTERN = re.compile(
    r'^((?:\d+[\.\)]\s+)?[A-Z][A-Za-z0-9\s\+\-\(\)]+:\s*)$',
    re.MULTILINE
)

def classify_tone_profile(text: str, is_bilingual: bool, spacy_doc=None) -> dict:
    """Stage 3: Tone Profile Classification based on linguistic signals."""
    tokens = [t for t in re.findall(r'\b\w+\b', text) if not t.isdigit()]
    total_tokens = max(len(tokens), 1)
    
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 10]
    total_sentences = max(len(sentences), 1)
    total_lines = len(text.split('\n'))
    
    # Modals
    shall_c = len(re.findall(r'\b(shall)\b', text, re.I))
    must_c = len(re.findall(r'\b(must|required|mandatory)\b', text, re.I))
    should_c = len(re.findall(r'\b(should|recommended)\b', text, re.I))
    may_c = len(re.findall(r'\b(may|can|permitted)\b', text, re.I))
    total_modals = max(shall_c + must_c + should_c + may_c, 1)
    
    shall_ratio = shall_c / total_modals
    must_ratio = must_c / total_modals
    modal_density = total_modals / total_sentences
    
    # Person
    first_person = len(re.findall(r'\b(I|we|our|us|my)\b', text, re.I)) / total_tokens
    second_person = len(re.findall(r'\b(you|your)\b', text, re.I)) / total_tokens
    third_person = max(1.0 - first_person - second_person, 0.0)
    
    # Sentence stats
    sentence_lengths = [len(s.split()) for s in sentences]
    avg_sentence = sum(sentence_lengths) / total_sentences if sentence_lengths else 0
    variance = sum((x - avg_sentence) ** 2 for x in sentence_lengths) / total_sentences if sentence_lengths else 0
    std_dev_sentence = variance ** 0.5
    
    # Complexity and vocabulary
    subordinate_ratio = len(re.findall(r'\b(because|when|if|unless|although|whereas)\b', text, re.I)) / total_sentences
    nominalisation = len(re.findall(r'\w+(tion|ment|ance|ence)\b', text, re.I)) / total_tokens
    connector_density = len(re.findall(r'\b(therefore|however|furthermore|consequently)\b', text, re.I)) / total_sentences
    hedging = len(re.findall(r'\b(may indicate|suggests|consistent with|appears)\b', text, re.I)) / total_sentences
    contractions = bool(re.search(r'\b(don\'t|can\'t|won\'t|isn\'t|aren\'t)\b', text, re.I))
    acronym_density = len(re.findall(r'\b[A-Z]{2,}\b', text)) / total_tokens
    risk_terms = len(re.findall(r'\b(severity|likelihood|criticality|probability|consequence|hazard|FMEA|RPN)\b', text, re.I)) / total_sentences
    
    # Advanced NLP (spaCy) features if available, else heuristic fallbacks
    imperative_ratio = 0.0
    passive_ratio = 0.0
    active_voice_ratio = 0.5
    present_tense_ratio = 0.5
    
    if spacy_doc:
        verb_phrases = 0
        passives = 0
        imperatives = 0
        presents = 0
        for sent in spacy_doc.sents:
            if len(sent) > 2 and sent[0].pos_ == "VERB" and sent[0].tag_ == "VB":
                imperatives += 1
            for token in sent:
                if token.pos_ in ("VERB", "AUX"):
                    verb_phrases += 1
                    if token.dep_ == "auxpass": passives += 1
                    if "Tense=Pres" in str(token.morph): presents += 1
        
        imperative_ratio = imperatives / max(len(list(spacy_doc.sents)), 1)
        passive_ratio = passives / max(verb_phrases, 1)
        active_voice_ratio = 1.0 - passive_ratio
        present_tense_ratio = presents / max(verb_phrases, 1)
    else:
        # Heuristic fallbacks
        imperative_ratio = sum(1 for s in sentences if re.match(r'^(Verify|Ensure|Document|Check|Record|Perform|Execute|Review)\b', s, re.I)) / total_sentences
        passive_ratio = len(re.findall(r'\b(is|are|was|were|be|been|being)\s+\w+ed\b', text, re.I)) / total_sentences
    
    # Rules evaluation
    primary_tone = "narrative_explanatory" # default
    
    if shall_ratio > 0.55 and passive_ratio > 0.30 and avg_sentence > 18 and not contractions and first_person < 0.02:
        primary_tone = "highly_formal_regulatory"
    elif imperative_ratio > 0.40 and avg_sentence < 15 and (shall_ratio > 0.20 or active_voice_ratio > 0.65):
        primary_tone = "instructional_imperative"
    elif third_person > 0.70 and modal_density < 0.15 and present_tense_ratio > 0.65 and 14 <= avg_sentence <= 22:
        primary_tone = "technical_descriptive"
    elif 0.25 <= shall_ratio <= 0.55 and acronym_density > 0.12 and 0.40 <= passive_ratio <= 0.60:
        primary_tone = "hybrid_formal_technical"
    elif avg_sentence < 10 and acronym_density > 0.18 and imperative_ratio > 0.55 and connector_density < 0.05:
        primary_tone = "military_command"
    elif passive_ratio > 0.50 and third_person > 0.85 and hedging > 0.08:
        primary_tone = "clinical_evidence_based"
    elif avg_sentence > 22 and modal_density < 0.12 and connector_density > 0.12 and subordinate_ratio > 0.25:
        primary_tone = "narrative_explanatory"
    elif std_dev_sentence < 8 and len(re.findall(r'^\s*[-*â€¢]\s+', text, re.MULTILINE)) / max(total_lines, 1) > 0.60 and avg_sentence < 8:
        primary_tone = "checklist_tabular"
    elif risk_terms > 0.08:
        primary_tone = "risk_weighted"
    
    if is_bilingual and primary_tone in ("highly_formal_regulatory", "hybrid_formal_technical"):
        primary_tone = "bilingual_formal"

    modifiers = []
    if risk_terms > 0.05: modifiers.append("RISK_AWARE")
    if len(re.findall(r'\b(audit|traceability|evidence|record|log)\b', text, re.I)) / total_sentences > 0.1:
        modifiers.append("AUDIT_HEAVY")
    if len(re.findall(r'\b(approve|sign|authorize|release)\b', text, re.I)) / total_sentences > 0.05:
        modifiers.append("APPROVAL_GATED")
    if len(re.findall(r'\b(within|before|after|days|hours|minutes)\b', text, re.I)) / total_sentences > 0.15:
        modifiers.append("TIME_BOUND")
    if is_bilingual: modifiers.append("BILINGUAL")

    return {
        "primary_tone": primary_tone,
        "sub_tone_modifiers": modifiers,
        "tone_signals": {
            "SHALL_ratio": round(shall_ratio, 3),
            "imperative_ratio": round(imperative_ratio, 3),
            "passive_ratio": round(passive_ratio, 3),
            "avg_sentence_length": round(avg_sentence, 1),
            "acronym_density": round(acronym_density, 3),
            "hedging_density": round(hedging, 3),
            "contraction_presence": contractions,
            "nominalisation_density": round(nominalisation, 3)
        },
        "formality_level": "highly_formal" if primary_tone in ("highly_formal_regulatory", "bilingual_formal", "hybrid_formal_technical") else "standard",
        "authority_level": "high" if "highly_formal" in primary_tone or primary_tone == "military_command" else "medium",
        "compliance_weight": "mandatory" if shall_ratio > 0.4 else "recommended"
    }

class DocumentChunk:
    def __init__(self, title, content, section_type="general", is_generic=False, chunk_id=None, chunk_type="text", nesting_level=0, start_line=0, end_line=0, semantic_score=1.0):
        self.chunk_id = chunk_id or id(self)
        self.section_title = title
        self.content = content
        self.section_type = section_type
        self.is_generic = is_generic
        self.chunk_type = chunk_type # text | table | numbered | header
        self.nesting_level = nesting_level
        self.start_line = start_line
        self.end_line = end_line
        self.semantic_boundary_score = semantic_score

    def to_dict(self):
        return {
            "chunk_id": self.chunk_id,
            "title": self.section_title,
            "section_title": self.section_title,
            "section_type": self.section_type,
            "chunk_type": self.chunk_type,
            "nesting_level": self.nesting_level,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "semantic_boundary_score": round(self.semantic_boundary_score, 3),
            "is_generic": self.is_generic,
            "content": self.content
        }

def auto_discover_domain(text: str, lang_code: str = "en") -> dict:
    """Stage 4: Domain Auto-Discovery using Regex, Vocab, and TF-IDF."""
    confidence = 0.0
    citations = []
    roles = []
    
    # Regulatory Citation Extract
    reg_map = {
        r'\bISO\s+9001\b': 'QMS_general',
        r'\bISO\s+14001\b': 'environmental_EHS',
        r'\bISO\s+13485\b': 'medical_devices',
        r'\bISO\s+17025\b': 'laboratory',
        r'\bIEC\s+62443\b': 'IT_OT_security',
        r'\b21\s+CFR\s+Part\s+11\b': 'pharma_IT',
        r'\b21\s+CFR\s+Part\s+211\b': 'pharma_manufacturing',
        r'\bNIST\s+SP\s+800\b': 'cybersecurity',
        r'\bAS9100\b': 'aviation_aerospace',
        r'\bDO-178C\b': 'aviation_aerospace',
        r'\bHACCP\b': 'food_safety',
        r'\b10\s+CFR\b': 'nuclear',
        r'\bMIL-SPEC\b': 'defense_military'
    }
    
    domain_hits = {}
    for pat, dom in reg_map.items():
        if re.search(pat, text, re.I):
            citations.append(pat.replace(r'\b', '').replace(r'\s+', ' ').strip())
            domain_hits[dom] = domain_hits.get(dom, 0) + 1
    # Keyword Based Extract
    keyword_map = {
        r'\bFirewall\b': 'IT_OT_security',
        r'\bNetzwerksicherheit\b': 'IT_OT_security',
        r'\bZugriffsmanagement\b': 'IT_OT_security',
        r'\bVerschlüsselung\b': 'IT_OT_security',
        r'\bAudit\b': 'QMS_general',
        r'\bQualitätssicherung\b': 'QMS_general',
        r'\bValidierung\b': 'pharma_gmp',
        r'\bPharma\b': 'pharma_gmp'
    }
    for pat, dom in keyword_map.items():
        if re.search(pat, text, re.I):
            domain_hits[dom] = domain_hits.get(dom, 0) + 1
            confidence += 0.15
            
    # Role Title Extract (Expanded for bilingual/German)
    role_map = {
        'CISO': 'cybersecurity', 'SOC analyst': 'cybersecurity', 
        'IT Admin': 'IT_OT_security', 'IT-Admin': 'IT_OT_security',
        'Administrator': 'IT_OT_security', 'Systemadministrator': 'IT_OT_security',
        'Produktionsleiter': 'IT_OT_security', 'Production Manager': 'IT_OT_security',
        'Qualitätssicherung': 'QMS_general', 'QA': 'QMS_general',
        'pharmacist': 'pharma_manufacturing', 'Apotheker': 'pharma_manufacturing',
        'QC analyst': 'pharma_gmp', 'QP': 'pharma_gmp',
        'pilot': 'aviation_aerospace', 'nurse': 'healthcare_clinical',
        'trader': 'finance_banking', 'HACCP team': 'food_safety'
    }
    for role, dom in role_map.items():
        if re.search(r'\b' + re.escape(role) + r'\b', text, re.I):
            roles.append(role)
            domain_hits[dom] = domain_hits.get(dom, 0) + 1
            
    if citations: confidence += 0.45
    if roles: confidence += 0.25
    
    primary_domain = "UNKNOWN"
    domain_auto_discovered = False
    tfidf_top10 = []
    secondary_domain = None
    
    if domain_hits:
        sorted_domains = sorted(domain_hits.items(), key=lambda x: x[1], reverse=True)
        primary_domain = sorted_domains[0][0]
        if len(sorted_domains) > 1:
            secondary_domain = sorted_domains[1][0]
        confidence = min(confidence + 0.1, 0.95)
    else:
        # Step C: TF-IDF fallback
        if SKLEARN_AVAILABLE and SBERT_AVAILABLE:
            # Use 'english' stop words only for English; otherwise None or custom list
            sw = 'english' if lang_code == 'en' else None
            vectorizer = TfidfVectorizer(stop_words=sw, max_features=100)
            try:
                tfidf_matrix = vectorizer.fit_transform([text])
                feature_names = vectorizer.get_feature_names_out()
                tfidf_scores = tfidf_matrix.toarray()[0]
                sorted_idx = tfidf_scores.argsort()[-30:][::-1]
                top_tokens = [feature_names[i] for i in sorted_idx if not feature_names[i].isdigit()]
                tfidf_top10 = top_tokens[:10]
                
                # Synthetic centroids for fallback matching
                seed_domains = {
                    "QMS_general": "quality management policy procedure standard audit compliance",
                    "IT_security": "network access password firewall authentication encryption server zugriff authentifizierung",
                    "pharma_gmp": "batch lot release assay sterilization contamination FDA abweichung capa",
                    "finance_banking": "transaction account compliance trading audit risk capital",
                    "legal_regulatory": "agreement party liability governing jurisdiction terms obligations"
                }
                model = _get_sbert()
                fingerprint_emb = model.encode(" ".join(top_tokens))
                
                best_sim = 0
                best_dom = "UNKNOWN"
                for dom, kw in seed_domains.items():
                    dom_emb = model.encode(kw)
                    sim = cosine_similarity([fingerprint_emb], [dom_emb])[0][0]
                    if sim > best_sim:
                        best_sim = sim
                        best_dom = dom
                
                if best_sim > 0.45: # Lowered threshold slightly for cross-lingual
                    primary_domain = best_dom
                    confidence = round(best_sim, 2)
                else:
                    top3 = "_".join(tfidf_top10[:3]) if tfidf_top10 else "generic"
                    primary_domain = f"AUTO:{top3}"
                    domain_auto_discovered = True
                    confidence = 0.38
            except Exception:
                primary_domain = "UNKNOWN"
                domain_auto_discovered = True
                confidence = 0.1
        else:
            primary_domain = "UNKNOWN"
            domain_auto_discovered = True
            confidence = 0.1

    return {
        "primary_domain": primary_domain,
        "domain_confidence": min(round(confidence, 2), 1.0),
        "regulatory_citations": citations,
        "role_vocabulary_match": roles,
        "tfidf_fingerprint_top10": tfidf_top10,
        "domain_auto_discovered": domain_auto_discovered,
        "secondary_domain": secondary_domain,
        "applicable_standards": citations
    }

def _sbert_chunking(lines: list) -> list:
    if not SBERT_AVAILABLE or not os.getenv("ENABLE_SBERT_CHUNKING", "true").lower() == "true":
        return None
    try:
        model = _get_sbert()
        if not model: return None
        from sklearn.metrics.pairwise import cosine_similarity
        
        # Batch lines into small blocks
        blocks = ["\n".join(lines[i:i+5]) for i in range(0, len(lines), 5)]
        if len(blocks) < 2: return None
        
        embeddings = model.encode(blocks)
        similarities = cosine_similarity(embeddings)
        
        boundaries = []
        for i in range(len(blocks)-1):
            if similarities[i][i+1] < 0.35: # Semantic boundary
                boundaries.append(i * 5 + 5)
        
        if not boundaries: return None
        
        chunks = []
        start = 0
        boundaries.append(len(lines))
        for i, b in enumerate(boundaries):
            content = "\n".join(lines[start:b])
            if content.strip():
                chunks.append(DocumentChunk(
                    f"Semantic Chunk {i+1}", 
                    content, 
                    chunk_type="semantic", 
                    start_line=start, 
                    end_line=b-1,
                    semantic_score=1.0 - (similarities[start//5-1][start//5] if start>0 else 1.0)
                ))
            start = b
        return chunks
    except Exception:
        return None

def adaptive_semantic_chunk(text: str, style_info: dict) -> list:
    """Stage 5: Adaptive Semantic Chunking based on Writing Style."""
    lines = text.splitlines()
    chunks = []
    strategy = style_info.get("chunking_strategy_selected", "STRATEGY_H_SEMANTIC")
    
    # Define regex patterns for different strategies
    numbered_pattern = re.compile(r'^(\d+(?:\.\d+)*)[\.\)]\s+([A-Z].*)$')
    legal_pattern = re.compile(r'^\s*(Article\s+[IVX\d]+|Section\s+\d+\.\d+|§\s*\d+)\s*(.*)', re.I)
    military_pattern = re.compile(r'^(\d+\.[a-z]\.[ivx]+\.|\d+\.[a-z]\.|\d+\.)\s+(.*)')
    caps_pattern = re.compile(r'^([A-Z][A-Z\s\-\/\&]+)$')
    bullet_header_pattern = re.compile(r'^([A-Z][A-Za-z0-9\s]+)$')
    form_pattern = re.compile(r'^([A-Z][A-Za-z\s]+):\s*(.*)$')
    markdown_pattern = re.compile(r'^(#{1,4})\s+(.*)$')
    
    current_chunk = {"lines": [], "title": "Intro", "type": "intro", "start": 0, "nesting": 0}
    
    def finalize_chunk(end_idx):
        if current_chunk["lines"]:
            content = "\n".join(current_chunk["lines"]).strip()
            if content:
                chunks.append(DocumentChunk(
                    current_chunk["title"], content, 
                    determine_section_type(current_chunk["title"]),
                    chunk_type=current_chunk["type"],
                    nesting_level=current_chunk["nesting"],
                    start_line=current_chunk["start"],
                    end_line=end_idx
                ))
    
    # SBERT Semantic strategy (H)
    if strategy == "STRATEGY_H_SEMANTIC" and len(lines) > 100:
        sbert_chunks = _sbert_chunking(lines)
        if sbert_chunks: return [c.to_dict() for c in sbert_chunks]
    elif strategy == "STRATEGY_H_SEMANTIC":
        # Fallback to structural chunking for small files
        strategy = "STRATEGY_J_HIERARCHICAL_FALLBACK"
        
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            current_chunk["lines"].append(line)
            continue
            
        matched = False
        num_match, leg_match, mil_match, caps_match, form_match, md_match = None, None, None, None, None, None
        
        is_table = "|" in stripped and stripped.count("|") > 2
        is_bullet = re.match(r'^[-*â€¢]\s+', stripped)
        
        if strategy == "STRATEGY_A_DECIMAL_NUMBERED" or strategy == "STRATEGY_J_HIERARCHICAL_FALLBACK":
            num_match = numbered_pattern.match(stripped)
            if not num_match and len(stripped.split()) <= 10:
                caps_match = caps_pattern.match(stripped)
        elif strategy == "STRATEGY_B_LEGAL_STRUCTURE":
            leg_match = legal_pattern.match(stripped)
        elif strategy == "STRATEGY_C_OUTLINE":
            mil_match = military_pattern.match(stripped)
        elif strategy == "STRATEGY_E_TABLE_AWARE":
            if is_table and current_chunk["type"] != "table":
                matched = True
        elif strategy == "STRATEGY_D_HEADER_BULLET":
            if not is_bullet and len(stripped.split()) <= 10 and not stripped.endswith('.'):
                caps_match = bullet_header_pattern.match(stripped)
        elif strategy == "STRATEGY_F_FIELD_VALUE":
            form_match = form_pattern.match(stripped)
        elif strategy == "STRATEGY_I_MARKDOWN":
            md_match = markdown_pattern.match(stripped)
            
        if num_match or leg_match or mil_match or caps_match or form_match or md_match or (strategy == "STRATEGY_E_TABLE_AWARE" and matched):
            finalize_chunk(i-1)
            current_chunk = {"lines": [line], "start": i, "title": "Section", "type": "text", "nesting": 0}
            if num_match:
                current_chunk["title"] = f"{num_match.group(1)} {num_match.group(2)}"
                current_chunk["type"] = "numbered"
                current_chunk["nesting"] = num_match.group(1).count('.')
            elif leg_match:
                current_chunk["title"] = f"{leg_match.group(1)} {leg_match.group(2)}"
                current_chunk["type"] = "legal_clause"
                current_chunk["nesting"] = 1
            elif mil_match:
                current_chunk["title"] = f"{mil_match.group(1)} {mil_match.group(2)}"
                current_chunk["type"] = "military_outline"
                current_chunk["nesting"] = mil_match.group(1).count('.')
            elif caps_match:
                current_chunk["title"] = caps_match.group(1)
                current_chunk["type"] = "header"
                current_chunk["nesting"] = 0
            elif form_match:
                current_chunk["title"] = form_match.group(1)
                current_chunk["type"] = "form_field"
                current_chunk["nesting"] = 0
            elif md_match:
                current_chunk["title"] = md_match.group(2)
                current_chunk["type"] = "markdown"
                current_chunk["nesting"] = len(md_match.group(1))
            elif strategy == "STRATEGY_E_TABLE_AWARE" and matched:
                current_chunk["title"] = f"Table @ {i}"
                current_chunk["type"] = "table"
                current_chunk["nesting"] = 0
        else:
            current_chunk["lines"].append(line)
            
    finalize_chunk(len(lines)-1)
    
    if len(chunks) < 2 and strategy != "STRATEGY_H_SEMANTIC":
        sbert_chunks = _sbert_chunking(lines)
        if sbert_chunks: return [c.to_dict() for c in sbert_chunks]
        
    if len(chunks) < 2:
        words = text.split()
        chunks = []
        chunk_words = 1500
        overlap = 50
        for i in range(0, len(words), chunk_words - overlap):
            content = " ".join(words[i:i + chunk_words])
            chunks.append(DocumentChunk(f"Chunk {i//(chunk_words - overlap) + 1}", content, "general", is_generic=True, chunk_type="fallback"))
            
    return [c.to_dict() for c in chunks]

def assign_universal_section_labels(header: str, domain: str = "general") -> str:
    """Stage 6: Domain-aware universal section label assignment."""
    h = header.lower().strip()

    # Universal section labels (across all domains)
    universal_map = [
        (["purpose", "objective", "goal", "intent", "zweck", "ziel"], "PURPOSE"),
        (["scope", "applicability", "coverage", "applies to", "geltungsbereich"], "SCOPE"),
        (["definition", "terms", "glossar", "abbreviation", "begriffe"], "DEFINITIONS"),
        (["responsib", "roles", "raci", "ownership", "verantwortlich", "rollen"], "RESPONSIBILITIES"),
        (["procedure", "process", "steps", "instructions", "method", "verfahren", "durchführung", "ablauf"], "PROCEDURE"),
        (["record", "log", "documentation", "evidence", "form", "aufzeichnung", "dokumentation"], "RECORDS"),
        (["reference", "related doc", "related standard", "referenz", "mitgeltend"], "REFERENCES"),
        (["revision", "change history", "amendment", "version", "änderungshistorie"], "REVISION_HISTORY"),
        (["appendix", "annex", "attachment", "exhibit", "anlage"], "APPENDIX"),
        (["approval", "sign", "authorized", "freigabe", "genehmigung"], "APPROVAL"),
        (["decision", "dec-", "entscheidung", "beschluss"], "DECISIONS"),
        (["deviation", "dev-", "abweichung"], "DEVIATIONS"),
        (["capa", "korrektur", "vorbeugend"], "CAPA"),
        (["audit finding", "aud-", "auditfund", "befund"], "AUDIT_FINDINGS"),
        (["sop content", "main content"], "SOP_CONTENT"),
    ]

    # Domain-specific labels layered on top
    domain_specific = {
        "QMS_general": [],
        "legal_regulatory": [
            (["represent", "warrant"], "REPRESENTATIONS"),
            (["indemnif", "liabilit", "haftung"], "INDEMNIFICATION"),
            (["governing law", "jurisdiction", "gerichtsstand"], "GOVERNING_LAW"),
            (["terminat", "kündigung"], "TERMINATION"),
            (["confidential", "vertraulich"], "CONFIDENTIALITY"),
        ],
        "cybersecurity": [
            (["threat", "risk", "vulnerability"], "THREAT_MODEL"),
            (["incident", "response", "soc"], "INCIDENT_RESPONSE"),
            (["access control", "authentication", "zugang"], "ACCESS_CONTROL"),
        ],
        "pharma_gmp": [
            (["batch", "lot", "release", "charge"], "BATCH_RECORD"),
            (["steril", "clean room", "reinraum"], "CLEANROOM_CONTROL"),
        ],
        "healthcare_clinical": [
            (["patient", "consent", "einwilligung"], "PATIENT_SAFETY"),
            (["clinical", "protocol", "trial"], "CLINICAL_PROTOCOL"),
        ],
    }

    # Check universal first
    for aliases, label in universal_map:
        if any(alias in h for alias in aliases):
            return label

    # Check domain-specific overrides
    for d, rules in domain_specific.items():
        if d in domain or domain in d:
            for aliases, label in rules:
                if any(alias in h for alias in aliases):
                    return label

    return "GENERAL"

# Backward-compatible alias
def determine_section_type(header: str) -> str:
    return assign_universal_section_labels(header).lower()

def compute_domain_quality_metrics(text: str, chunks: list, tone_info: dict, domain: str) -> dict:
    """Stage 6b: Domain-adaptive quality metrics replacing Flesch/Fog."""
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 10]
    total_sentences = max(len(sentences), 1)
    tokens = text.split()
    total_tokens = max(len(tokens), 1)

    # Modal density (obligation signals per sentence)
    modals = len(re.findall(r'\b(shall|must|required|mandatory|will|should|may|permitted)\b', text, re.I))
    modal_density = round(modals / total_sentences, 3)

    # Actorless obligation rate (sentences with shall/must but no subject noun before verb)
    actorless = 0
    for s in sentences:
        has_modal = bool(re.search(r'\b(shall|must|is required)\b', s, re.I))
        has_actor = bool(re.search(r'\b(the|a|an|this|that)\s+\w+\s+(shall|must|will)\b', s, re.I))
        if has_modal and not has_actor:
            actorless += 1
    actorless_rate = round(actorless / total_sentences, 3)

    # Instruction completeness: step sentences with actor + action + object
    complete_steps = 0
    incomplete_steps = 0
    step_sentences = [s for s in sentences if re.match(r'\b(Verify|Ensure|Check|Record|Submit|Perform|Execute|Review|Inspect|Approve)\b', s.strip(), re.I)]
    for s in step_sentences:
        words = s.split()
        if len(words) >= 5:
            complete_steps += 1
        else:
            incomplete_steps += 1
    instruction_completeness = round(complete_steps / max(len(step_sentences), 1), 3)

    # Traceability density (IDs per 100 tokens)
    trace_ids = re.findall(r'\b[A-Z]{2,5}-[A-Z]{2,4}-\d{3,5}\b|\b[A-Z]{2,4}\d{3,5}\b', text)
    traceability_density = round(len(trace_ids) / total_tokens * 100, 3)

    # Passive ratio
    passive_count = len(re.findall(r'\b(is|are|was|were|be|been|being)\s+\w+ed\b', text, re.I))
    passive_ratio = round(passive_count / total_sentences, 3)

    # Empty/stub section detection
    empty_sections = sum(1 for c in chunks if len(c.get("content", "").split()) < 5)

    # Chunk coverage
    chunk_count = len(chunks)
    avg_chunk_words = round(sum(len(c.get("content","").split()) for c in chunks) / max(chunk_count, 1), 1)

    # Overall quality score (0-100)
    quality_score = round(
        (min(modal_density / 0.5, 1.0) * 20) +
        ((1.0 - actorless_rate) * 20) +
        (instruction_completeness * 20) +
        (min(traceability_density / 2.0, 1.0) * 20) +
        ((1.0 - passive_ratio) * 20),
        1
    )

    return {
        "modal_density": modal_density,
        "actorless_obligation_rate": actorless_rate,
        "instruction_completeness_score": instruction_completeness,
        "traceability_density_per_100": traceability_density,
        "passive_ratio": passive_ratio,
        "empty_sections_count": empty_sections,
        "chunk_count": chunk_count,
        "avg_chunk_words": avg_chunk_words,
        "domain_quality_score": quality_score,
        "grade": "A" if quality_score >= 80 else "B" if quality_score >= 65 else "C" if quality_score >= 50 else "D"
    }

def build_universal_symbol_table(text: str, chunks: list, domain: str) -> dict:
    """Stage 7 prerequisite: Build Universal Symbol Table for cross-reference checking."""
    # Extract all role mentions
    role_patterns = [
        r'\b(Quality Manager|QA Manager|QC Analyst|CISO|System Owner|Plant Manager|'
        r'Document Controller|Process Owner|Pharmacist|Operator|Supervisor|'
        r'Administrator|Analyst|Engineer|Technician|Team Lead|Director|'
        r'Authorized Person|QP|Responsible Person)\b'
    ]
    roles = []
    for pat in role_patterns:
        roles.extend(re.findall(pat, text, re.I))
    roles = list(dict.fromkeys(r.title() for r in roles))  # deduplicated

    # Extract all traceability IDs
    ids = re.findall(r'\b[A-Z]{2,5}-[A-Z]{2,4}-\d{3,5}\b', text)
    ids += re.findall(r'\b(?:SOP|WI|POL|FORM|DEV|CAPA|AUD|NCR|ECO)-\d{3,6}\b', text, re.I)
    ids = list(dict.fromkeys(ids))

    # Extract all section labels from chunks
    section_labels = []
    for c in chunks:
        title = c.get("title", "")
        label = assign_universal_section_labels(title, domain)
        if label != "GENERAL":
            section_labels.append({"title": title, "label": label, "chunk_id": c.get("chunk_id")})

    # Extract dates using dateparser if available
    dates = []
    if DATEPARSER_AVAILABLE and _dateparser:
        date_strings = re.findall(r'\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b|\b\d{4}[./\-]\d{1,2}[./\-]\d{1,2}\b', text)
        for ds in date_strings[:20]:
            try:
                parsed = _dateparser.parse(ds)
                if parsed:
                    dates.append({"raw": ds, "parsed": parsed.strftime("%Y-%m-%d")})
            except Exception:
                pass
    else:
        date_strings = re.findall(r'\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b', text)
        dates = [{"raw": ds, "parsed": ds} for ds in date_strings[:20]]

    # SLA / time commitments
    sla_mentions = re.findall(r'\b(within\s+\d+\s+(?:hours?|days?|minutes?|working days?)|'
                              r'\d+\s*(?:hour|day|minute|business day)s?\s+(?:deadline|limit|window))\b', text, re.I)

    return {
        "roles": roles,
        "traceability_ids": ids,
        "section_labels": section_labels,
        "dates": dates,
        "sla_mentions": sla_mentions[:15],
        "symbol_count": len(roles) + len(ids) + len(section_labels)
    }

def rhyme_score(lines):
    last_words = [l.strip().split()[-1].lower() for l in lines if l.strip()]
    hits = 0
    valid_pairs = 0
    for i in range(len(last_words)-1):
        pa = pronouncing.phones_for_word(last_words[i])
        pb = pronouncing.phones_for_word(last_words[i+1])
        if pa and pb:
            valid_pairs += 1
            if pronouncing.rhyming_part(pa[0]) == pronouncing.rhyming_part(pb[0]):
                hits += 1
        elif len(last_words[i]) > 2 and len(last_words[i+1]) > 2:
            valid_pairs += 1
            if last_words[i][-2:] == last_words[i+1][-2:]:
                hits += 1
    return hits / max(valid_pairs, 1) if valid_pairs > 0 else 0

def _clean_word(word: str) -> str:
    return re.sub(r"[^a-zA-Z']", "", word).lower()

def _rhyme_key(word: str) -> str:
    cleaned = _clean_word(word)
    phones = pronouncing.phones_for_word(cleaned)
    if phones:
        return pronouncing.rhyming_part(phones[0])
    return cleaned[-3:] if len(cleaned) > 3 else cleaned

def rhyme_scheme(lines):
    scheme = []
    key_to_letter = {}
    next_letter = ord("A")
    end_words = []

    for line in lines:
        words = [_clean_word(w) for w in line.split()]
        words = [w for w in words if w]
        if not words:
            continue

        end_word = words[-1]
        end_words.append(end_word)
        key = _rhyme_key(end_word)
        if key not in key_to_letter:
            key_to_letter[key] = chr(next_letter)
            next_letter += 1
        scheme.append(key_to_letter[key])

    rhyming_pairs = []
    for i in range(len(end_words) - 1):
        if _rhyme_key(end_words[i]) == _rhyme_key(end_words[i + 1]):
            rhyming_pairs.append(f"{end_words[i]}/{end_words[i + 1]}")

    return {
        "scheme": "".join(scheme),
        "end_words": end_words,
        "rhyming_pairs": rhyming_pairs,
        "unique_rhyme_sounds": len(key_to_letter),
    }

def poetic_style_features(text: str, lines: list, total_words: int, pos_dist: dict, ttr: float) -> dict:
    non_empty_lines = [l.strip() for l in lines if l.strip()]
    line_word_counts = [len(l.split()) for l in non_empty_lines]
    avg_line_len = sum(line_word_counts) / max(len(line_word_counts), 1)
    line_len_variance = (
        sum((count - avg_line_len) ** 2 for count in line_word_counts) / max(len(line_word_counts), 1)
    )
    line_break_density = len(non_empty_lines) / max(total_words, 1)
    short_line_ratio = sum(1 for count in line_word_counts if count <= 8) / max(len(line_word_counts), 1)
    scheme = rhyme_scheme(non_empty_lines)

    lower = text.lower()
    metaphor_markers = [
        "like", "as", "as if", "shadow", "light", "flame", "fire", "sea", "ocean",
        "sky", "skies", "storm", "heart", "soul", "dream", "grace", "wings",
        "echo", "silence", "moon", "sun", "stars", "river", "stone", "bloom",
    ]
    sensory_terms = [
        "bright", "dark", "soft", "cold", "warm", "gold", "silver", "whisper",
        "sing", "scent", "touch", "taste", "glow", "hush", "music", "rhythm",
    ]
    direct_explanation_markers = [
        "this means", "in other words", "because", "therefore", "explains",
        "clearly", "directly", "specifically", "the reason", "as a result",
    ]
    subtle_markers = ["perhaps", "almost", "seems", "quiet", "silent", "soft", "beneath", "within"]

    metaphor_hits = sum(1 for marker in metaphor_markers if marker in lower)
    sensory_hits = sum(1 for marker in sensory_terms if marker in lower)
    explanation_hits = sum(1 for marker in direct_explanation_markers if marker in lower)
    subtle_hits = sum(1 for marker in subtle_markers if marker in lower)

    figurative_density = (metaphor_hits + sensory_hits + (pos_dist.get("adj_ratio", 0) * 10)) / max(total_words / 50, 1)
    compression_score = (
        short_line_ratio * 0.45
        + (avg_line_len <= 8) * 0.25
        + min(ttr, 1.0) * 0.20
        + (line_break_density > 0.12) * 0.10
    )
    musicality_score = (
        min(rhyme_score(non_empty_lines), 1.0) * 0.45
        + (short_line_ratio * 0.20)
        + (line_len_variance < 12) * 0.20
        + (len(scheme["rhyming_pairs"]) > 0) * 0.15
    )

    if explanation_hits > subtle_hits and explanation_hits > 0:
        emotional_delivery = "direct/explanatory"
    elif figurative_density >= 1.2 and subtle_hits > 0:
        emotional_delivery = "subtle/symbolic"
    elif figurative_density >= 1.2:
        emotional_delivery = "figurative/emotive"
    else:
        emotional_delivery = "plain/descriptive"

    return {
        "is_lineated": len(non_empty_lines) >= 3 and line_break_density > 0.08,
        "line_count": len(non_empty_lines),
        "avg_line_words": round(avg_line_len, 2),
        "line_length_variance": round(line_len_variance, 2),
        "line_break_density": round(line_break_density, 3),
        "short_line_ratio": round(short_line_ratio, 3),
        "rhyme_scheme": scheme["scheme"],
        "end_words": scheme["end_words"],
        "rhyming_pairs": scheme["rhyming_pairs"],
        "musicality_score": round(musicality_score, 3),
        "figurative_density": round(figurative_density, 3),
        "compression_score": round(compression_score, 3),
        "emotional_delivery": emotional_delivery,
        "metaphor_marker_hits": metaphor_hits,
        "sensory_marker_hits": sensory_hits,
        "explanation_marker_hits": explanation_hits,
    }

def sop_style_features(text: str, lines: list) -> dict:
    lower = text.lower()
    normalized_headers = [re.sub(r"[^a-z ]", "", l.strip().lower()) for l in lines if l.strip()]
    section_aliases = {
        "title": ["sop id titel", "sop id", "titel", "title"],
        "purpose": ["purpose", "objective", "zweck", "ziel"],
        "scope": ["scope", "applicability", "geltungsbereich", "anwendungsbereich"],
        "definitions": ["definitions", "terms and definitions", "definitionen", "begriffe"],
        "responsibilities": ["responsibilities", "roles and responsibilities", "verantwortlichkeiten", "rollen"],
        "procedure": ["procedure", "process", "method", "verfahren", "prozess", "durchfhrung", "ablauf"],
        "key_points": ["key points", "kernpunkte", "wichtige punkte"],
        "records": ["records", "documentation", "forms", "aufzeichnungen", "dokumentation", "logs"],
        "deviations": ["deviations", "abweichungen"],
        "capas": ["capas", "capa", "korrekturmanahmen", "korrekturmassnahmen"],
        "audit_findings": ["audit findings", "audit", "audit findings", "auditfeststellungen"],
        "decisions": ["decisions", "entscheidungen"],
        "references": ["references", "related documents", "referenzen", "mitgeltende dokumente"],
        "revision_history": ["revision history", "change history", "revision", "nderungshistorie"],
    }
    sections_found = []
    for canonical, aliases in section_aliases.items():
        if any(any(alias == h or alias in h for alias in aliases) for h in normalized_headers):
            sections_found.append(canonical)

    expected = list(section_aliases.keys())
    required_core = ["purpose", "scope", "procedure"]
    missing_core = [s for s in required_core if s not in sections_found]

    qms_terms = [
        "qms", "quality", "sop", "procedure", "process", "record", "document control",
        "revision", "approval", "effective date", "capa", "deviation", "nonconformance",
        "audit", "training", "compliance", "iso", "gmp", "controlled copy",
        "zweck", "geltungsbereich", "zugriff", "zugriffsmanagement", "produktionsnetzwerk",
        "ot", "it/ot", "iec 62443", "deviation", "capa", "audit findings", "decisions",
        "abweichung", "entscheidung", "freigabe", "genehmigung", "vertraulichkeit",
        "aufbewahrung", "sperre", "notfall", "break-glass", "qp", "ema", "fda",
    ]
    compliance_ids = re.findall(r"\b(?:SOP|DEV|CAPA|AUD|DEC)-[A-Z]{2,}-\d{3}\b", text, re.I)
    imperative_starts = len(re.findall(r"^\s*(?:[-*]|\d+[\.\)])\s*(verify|record|ensure|review|approve|maintain|document|inspect|submit|implement|migrate|enable|preserve|prfen|dokumentieren|freigeben|genehmigen|umsetzen|migrieren)\b", text, re.I | re.M))
    numbered_steps = len(re.findall(r"^\s*\d+[\.\)]\s+", text, re.M))
    sub_steps = len(re.findall(r"^\s*\d+\.\d+\s+", text, re.M))
    bullet_count = len(re.findall(r"^\s*(?:[-*]|[A-Z]+-[A-Z]+-\d{3}\s*[â€“-])", text, re.I | re.M))
    shall_count = len(re.findall(r"\b(shall|muss|mssen|darf nur|drfen nur)\b", text, re.I))
    must_count = len(re.findall(r"\b(must|required|mandatory|pflicht|erforderlich|obligatorisch)\b", text, re.I))
    should_count = len(re.findall(r"\b(should|soll|sollte)\b", text, re.I))
    qms_hits = sum(1 for term in qms_terms if term in lower)
    precision_terms = {}
    if re.search(r"\bSPS\b", text):
        precision_terms["SPS"] = "Preserve as SPS (PLC) on first English use; do not replace with PLC only."
    if re.search(r"\bKI\b|KI-", text):
        precision_terms["KI"] = "Translate consistently as AI; preserve KI-* access labels as AI-* or KI-* consistently."
    if re.search(r"Log\s+nach\s+1h", text, re.I):
        precision_terms["Log nach 1h"] = "Translate as activity/logging checkpoint after 1 hour; never as logout."
    if re.search(r"\b22:30\b", text):
        precision_terms["22:30"] = "Preserve 24-hour time format; do not convert to 10:30 PM."
    if re.search(r"15\s*Min\s*Sperre", text, re.I):
        precision_terms["15 Min Sperre"] = "Translate as 15-minute lockout."

    completeness_score = len(sections_found) / max(len(expected), 1)
    control_language_score = min((shall_count + must_count + imperative_starts) / 8, 1.0)
    format_score = (
        (numbered_steps > 0 or bullet_count > 4) * 0.20
        + (len(sections_found) >= 4) * 0.25
        + (qms_hits >= 4) * 0.20
        + (sub_steps > 0) * 0.10
        + (len(compliance_ids) >= 3) * 0.25
    )

    if completeness_score >= 0.45 and (numbered_steps or bullet_count > 4 or compliance_ids):
        format_pattern = "controlled QMS/SOP with section headers, traceability IDs, and procedural controls"
    elif numbered_steps:
        format_pattern = "procedure-style numbered work instruction"
    elif len(sections_found) >= 3:
        format_pattern = "sectioned policy/procedure document"
    else:
        format_pattern = "general operational prose"

    return {
        "format_pattern": format_pattern,
        "sections_found": sections_found,
        "sections_missing_core": missing_core,
        "completeness_score": round(completeness_score, 3),
        "numbered_step_count": numbered_steps,
        "sub_step_count": sub_steps,
        "bullet_count": bullet_count,
        "compliance_ids": sorted(set(compliance_ids)),
        "compliance_id_count": len(set(compliance_ids)),
        "imperative_step_count": imperative_starts,
        "shall_count": shall_count,
        "must_count": must_count,
        "should_count": should_count,
        "qms_term_hits": qms_hits,
        "control_language_score": round(control_language_score, 3),
        "format_score": round(format_score, 3),
        "detected_standard_terms": [term for term in qms_terms if term in lower],
        "precision_terms": precision_terms,
    }

def legal_style_features(text: str, lines: list) -> dict:
    lower = text.lower()
    clause_headers = re.findall(
        r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?(definitions|term|confidentiality|obligations|representations|warranties|indemnification|limitation of liability|termination|governing law|jurisdiction|notices|miscellaneous)\b",
        text,
        re.I,
    )
    legal_terms = [
        "agreement", "whereas", "therefore", "hereby", "pursuant", "notwithstanding",
        "covenant", "indemnify", "liable", "liability", "jurisdiction", "governing law",
        "confidential information", "representations", "warranties", "termination",
        "breach", "remedy", "force majeure", "severability", "assigns", "successors",
    ]
    defined_terms = re.findall(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s+means\b", text)
    obligation_count = len(re.findall(r"\b(shall|must|agrees to|is obligated to|will)\b", text, re.I))
    permission_count = len(re.findall(r"\b(may|is permitted to|has the right to)\b", text, re.I))
    prohibition_count = len(re.findall(r"\b(shall not|must not|may not|is prohibited from)\b", text, re.I))
    cross_refs = len(re.findall(r"\b(section|clause|article)\s+\d+(?:\.\d+)*\b", text, re.I))
    numbered_clauses = len(re.findall(r"(?:^|\n)\s*\d+(?:\.\d+)*[\.\)]\s+", text))
    whereas_count = len(re.findall(r"\bwhereas\b", text, re.I))
    legal_hits = sum(1 for term in legal_terms if term in lower)

    sentence_parts = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    avg_clause_sentence_words = sum(len(s.split()) for s in sentence_parts) / max(len(sentence_parts), 1)
    clause_density = (numbered_clauses + len(clause_headers) + cross_refs) / max(len(text.split()) / 100, 1)
    legalese_score = min((legal_hits + whereas_count + obligation_count + prohibition_count + len(defined_terms)) / 14, 1.0)

    if whereas_count and numbered_clauses:
        format_pattern = "formal agreement with recitals and numbered clauses"
    elif numbered_clauses or len(clause_headers) >= 3:
        format_pattern = "clause-based legal/firm document"
    elif legal_hits >= 4:
        format_pattern = "legal prose with obligation language"
    else:
        format_pattern = "general prose"

    return {
        "format_pattern": format_pattern,
        "clause_headers_found": sorted(set(h.lower() for h in clause_headers)),
        "numbered_clause_count": numbered_clauses,
        "defined_terms": sorted(set(defined_terms))[:20],
        "obligation_count": obligation_count,
        "permission_count": permission_count,
        "prohibition_count": prohibition_count,
        "cross_reference_count": cross_refs,
        "whereas_count": whereas_count,
        "legal_term_hits": legal_hits,
        "legalese_score": round(legalese_score, 3),
        "clause_density": round(clause_density, 3),
        "avg_clause_sentence_words": round(avg_clause_sentence_words, 2),
        "detected_legal_terms": [term for term in legal_terms if term in lower],
    }

SOP_SECTION_LABELS = {
    "purpose": ["purpose", "objective", "aim", "zweck", "ziel"],
    "scope": ["scope", "applicability", "geltungsbereich", "anwendungsbereich"],
    "definitions": ["definitions", "terms and definitions", "glossary", "definitionen", "begriffe"],
    "responsibilities": ["responsibilities", "roles", "accountability", "ownership", "verantwortlichkeiten", "rollen"],
    "procedure": ["procedure", "process", "method", "steps", "workflow", "work instruction", "verfahren", "prozess", "ablauf", "key points"],
    "prerequisites": ["prerequisites", "requirements", "precondition", "materials", "equipment", "voraussetzungen"],
    "safety": ["safety", "warning", "caution", "hazard", "risk", "sicherheit", "warnung", "gefahr"],
    "notes": ["notes", "important note", "remarks"],
    "exceptions": ["exceptions", "deviation", "alternate flow"],
    "references": ["references", "related documents", "source documents", "referenzen", "mitgeltende dokumente"],
    "records": ["records", "logs", "retention", "documentation", "aufzeichnungen", "dokumentation", "protokoll"],
    "appendix": ["appendix", "annex", "attachment"],
    "approvals": ["approval", "approvals", "sign-off", "authorization", "freigabe", "genehmigung"],
    "revision_history": ["revision history", "change log", "version history", "anderungshistorie", "anderungen"],
}

ROLE_TERMS = [
    "user", "operator", "technician", "supervisor", "manager", "admin",
    "reviewer", "approver", "system", "qa", "qc", "auditor", "department",
]

DOMAIN_TERM_BANKS = {
    "healthcare": ["patient", "clinical", "hospital", "medication", "diagnosis", "healthcare"],
    "laboratory": ["specimen", "lab", "assay", "reagent", "calibration", "laboratory", "traceability", "uncertainty", "accreditation", "iso 17025", "chain of custody", "method validation"],
    "manufacturing": ["production", "batch", "line", "assembly", "gmp", "manufacturing"],
    "it": ["server", "application", "deploy", "backup", "incident", "it", "ot", "sps", "scada", "vpn", "service-account", "produktionsnetzwerk", "zugriffsmanagement", "zero-trust", "privilege access", "pam", "iam", "sso", "mfa", "rbac", "ldap", "active directory", "firewall", "siem", "soc", "cve", "patch management", "vulnerability"],
    "security": ["access control", "authentication", "authorization", "threat", "security", "2fa", "token", "firewall", "passwort", "zugriff", "berechtigung", "vertraulichkeit"],
    "operations": ["operations", "runbook", "handover", "workflow", "sla", "produktion", "wartung"],
    "administration": ["office", "administrative", "policy", "documentation", "filing", "hr_admin"],
    "finance": ["invoice", "ledger", "reconciliation", "expense", "financial"],
    "education": ["curriculum", "student", "classroom", "assessment", "education"],
    "logistics": ["shipment", "warehouse", "dispatch", "inventory", "logistics"],
    "customer_support": ["ticket", "customer", "escalation", "resolution", "support"],
    "research": ["study", "protocol", "hypothesis", "experiment", "research"],
    "pharma_gmp": ["batch record", "deviation", "oos", "oot", "lims", "gdp", "gmp", "ich", "usp", "pharmacovigilance", "stability", "validation", "iq/oq/pq", "pharma"],
}

QMS_SUB_TYPE_BANKS = {
    "IT_security": DOMAIN_TERM_BANKS["it"] + DOMAIN_TERM_BANKS["security"],
    "pharma_gmp": DOMAIN_TERM_BANKS["pharma_gmp"],
    "laboratory": DOMAIN_TERM_BANKS["laboratory"],
    "manufacturing": DOMAIN_TERM_BANKS["manufacturing"],
    "hr_admin": DOMAIN_TERM_BANKS["administration"],
    "finance": DOMAIN_TERM_BANKS["finance"],
    "logistics": DOMAIN_TERM_BANKS["logistics"],
    "customer_support": DOMAIN_TERM_BANKS["customer_support"],
    "environmental": ["ehs", "environmental", "spill", "waste", "emissions", "sustainability"],
    "research": DOMAIN_TERM_BANKS["research"]
}

LEGAL_AGREEMENT_PATTERNS = {
    "NDA": ["non-disclosure", "confidentiality agreement", "nda"],
    "MSA": ["master services agreement", "msa", "master service agreement"],
    "Employment": ["employment agreement", "offer letter", "employee agreement"],
    "License": ["license agreement", "eula", "end user license"],
    "Settlement": ["settlement agreement", "release of claims"],
    "Operating": ["operating agreement", "llc agreement"],
    "Asset_Purchase": ["asset purchase", "apa"],
    "Share_Purchase": ["share purchase", "stock purchase", "spa"],
    "Loan": ["loan agreement", "promissory note", "credit agreement"],
    "Lease": ["lease agreement", "commercial lease"],
    "Consulting": ["consulting agreement", "independent contractor"],
    "Supply": ["supply agreement", "supplier agreement", "manufacturing and supply"],
    "Shareholders": ["shareholders agreement", "stockholders agreement"],
    "Terms_of_Service": ["terms of service", "terms and conditions", "tos"],
    "IP_Assignment": ["ip assignment", "intellectual property assignment", "invention assignment"],
    "Distribution": ["distribution agreement", "distributor agreement"],
}

TRACE_ID_PATTERN = re.compile(r"\b(SOP|DEV|CAPA|AUD|DEC)-[A-Z]{2,}-\d{3,}\b", re.I)

CANONICAL_SECTION_VARIANTS = {
    "purpose": ["purpose", "objective", "intent", "zweck"],
    "scope": ["scope", "applicability", "coverage", "geltungsbereich"],
    "responsibilities": ["responsibilities", "roles", "ownership", "accountability", "verantwortlichkeiten"],
    "procedure": ["procedure", "process", "method", "workflow", "verfahren", "prozess", "ablauf"],
    "deviations": ["deviations", "exceptions", "incidents", "abweichungen"],
    "capa": ["capa", "corrective actions", "corrective and preventive actions", "korrekturmassnahmen"],
    "audit_findings": ["audit findings", "observations", "audits", "auditfeststellungen"],
    "decisions": ["decisions", "approvals", "resolution", "entscheidungen"],
    "records": ["records", "logs", "retention", "aufzeichnungen"],
    "references": ["references", "standards", "related documents", "referenzen"],
}

DOMAIN_SECTION_VARIANTS = {
    "medical": ["patient safety", "clinical protocol", "risk assessment"],
    "healthcare": ["patient safety", "clinical protocol", "risk assessment"],
    "insurance": ["claims processing", "policy rules", "fraud checks"],
    "it": ["access control", "network security", "authentication rules"],
    "security": ["access control", "network security", "authentication rules"],
    "pharma": ["gmp controls", "batch release", "quality risk management"],
}

SOP_LANGUAGE_PACKS = {
    "de": {
        "section_headers": {
            "title": "Titel",
            "purpose": "Zweck",
            "scope": "Geltungsbereich",
            "responsibilities": "Verantwortlichkeiten",
            "procedure": "Verfahren",
            "exceptions": "Ausnahmen",
            "records": "Aufzeichnungen",
            "references": "Referenzen",
        },
        "phrases": {
            "mandatory": "muss",
            "prohibition": "darf nicht",
            "conditional": "falls",
            "verify": "verifizieren",
            "record": "dokumentieren",
        },
    },
    "en": {
        "section_headers": {
            "title": "Title",
            "purpose": "Purpose",
            "scope": "Scope",
            "responsibilities": "Responsibilities",
            "procedure": "Procedure",
            "exceptions": "Exceptions",
            "records": "Records",
            "references": "References",
        },
        "phrases": {
            "mandatory": "shall",
            "prohibition": "must not",
            "conditional": "if",
            "verify": "verify",
            "record": "document",
        },
    },
}

def _infer_sop_language(text: str, lang_code: str = "en") -> str:
    lower = text.lower()
    de_markers = len(re.findall(r"\b(zweck|geltungsbereich|verantwortlichkeiten|verfahren|aufzeichnungen|genehmigung|abweichung|muss|darf nicht)\b", lower))
    en_markers = len(re.findall(r"\b(purpose|scope|responsibilities|procedure|records|approval|deviation|shall|must)\b", lower))
    if de_markers > en_markers:
        return "de"
    if lang_code.startswith("de"):
        return "de"
    return "en"

def _normalize_header(line: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", line.lower()).strip()

def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^[^:]{2,40}:\s*$", stripped):
        return True
    return bool(re.match(r"^(\d+(\.\d+)*[\.\)]\s+.+|[A-Z][A-Z0-9 \-/]{3,}|[A-Z][a-z]+(?:\s+[A-Za-z]+){0,8}\s*:?)$", stripped))

def _semantic_overlap_score(text_a: str, text_b: str) -> float:
    a_tokens = set(re.findall(r"[a-z]{3,}", text_a.lower()))
    b_tokens = set(re.findall(r"[a-z]{3,}", text_b.lower()))
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(len(a_tokens | b_tokens), 1)

def _classify_header_semantically(raw_header: str, domain: str = "general") -> tuple:
    norm = _normalize_header(raw_header)
    best_label = "general"
    best_score = 0.0
    for canonical, variants in CANONICAL_SECTION_VARIANTS.items():
        variant_score = max((_semantic_overlap_score(norm, v) for v in variants), default=0.0)
        if any(v in norm for v in variants):
            variant_score = max(variant_score, 0.95)
        if variant_score > best_score:
            best_score = variant_score
            best_label = canonical

    domain_hits = []
    for dom, labels in DOMAIN_SECTION_VARIANTS.items():
        if dom in domain and any(lbl in norm for lbl in labels):
            domain_hits.extend([lbl for lbl in labels if lbl in norm])
    return best_label, round(best_score, 3), sorted(set(domain_hits))

def _qdrant_semantic_section_lookup(section_candidates: list, domain: str = "general") -> dict:
    """
    Optional Qdrant semantic linking. Safe no-op when Qdrant config/client is unavailable.
    Uses term-overlap proxy vectors unless external embeddings are configured.
    """
    # Keep analysis stable by default in local/desktop usage.
    # Enable this only when explicitly requested.
    if os.getenv("ENABLE_QDRANT_SECTION_LOOKUP", "false").strip().lower() not in {"1", "true", "yes"}:
        return {"enabled": False, "reason": "disabled by default (set ENABLE_QDRANT_SECTION_LOOKUP=true to enable)", "links": []}

    qdrant_url = os.getenv("QDRANT_URL")
    if not qdrant_url:
        return {"enabled": False, "reason": "QDRANT_URL not configured", "links": []}
    try:
        from qdrant_client import QdrantClient  # type: ignore
    except Exception as exc:
        return {"enabled": False, "reason": f"qdrant-client unavailable: {exc}", "links": []}

    try:
        client = QdrantClient(url=qdrant_url, api_key=os.getenv("QDRANT_API_KEY"))
        collection = os.getenv("QDRANT_SOP_COLLECTION", "sop_sections")
        links = []
        for candidate in section_candidates[:40]:
            query_text = f"{domain} {candidate.get('raw', '')} {candidate.get('normalized', '')}".strip()
            # Keep integration lightweight by using text payload filter; embedding lookup can be layered later.
            hits = client.scroll(
                collection_name=collection,
                limit=3,
                with_payload=True,
                scroll_filter=None,
            )[0]
            mapped = []
            for h in hits:
                payload = getattr(h, "payload", {}) or {}
                sem = payload.get("section_label") or payload.get("canonical") or payload.get("title")
                if sem:
                    mapped.append(str(sem))
            if mapped:
                links.append({
                    "header": candidate.get('raw'),
                    "query_text": query_text,
                    "semantic_matches": mapped[:3],
                })
        return {"enabled": True, "reason": "linked via Qdrant scroll", "links": links}
    except Exception as exc:
        return {"enabled": False, "reason": f"qdrant query failed: {exc}", "links": []}

def _extract_metadata(text: str) -> dict:
    patterns = {
        "title": r"(?im)^(?:title|document title)\s*[:\-]\s*(.+)$",
        "sop_number": r"(?im)^(?:sop(?:\s*(?:number|no|id))?|document id)\s*[:\-]\s*([A-Za-z0-9\-_/.]+)$",
        "version": r"(?im)^(?:version|rev(?:ision)?)\s*[:\-]\s*([A-Za-z0-9.\-_]+)$",
        "effective_date": r"(?im)^(?:effective date|date effective)\s*[:\-]\s*(.+)$",
        "review_date": r"(?im)^(?:review date|next review)\s*[:\-]\s*(.+)$",
        "author": r"(?im)^(?:author|prepared by)\s*[:\-]\s*(.+)$",
        "owner": r"(?im)^(?:owner|process owner|document owner)\s*[:\-]\s*(.+)$",
        "approver": r"(?im)^(?:approver|approved by)\s*[:\-]\s*(.+)$",
        "department": r"(?im)^(?:department|dept)\s*[:\-]\s*(.+)$",
    }
    metadata = {}
    for key, pat in patterns.items():
        m = re.search(pat, text)
        metadata[key] = m.group(1).strip() if m else None

    if not metadata.get("title"):
        m = re.search(r"(?im)^sop\s*id\s*\+\s*titel\s*[:\-]\s*(.+)$", text)
        metadata["title"] = m.group(1).strip() if m else metadata.get("title")
    if not metadata.get("sop_number"):
        m = re.search(r"\b(SOP-[A-Z]{2,}-\d{3,})\b", text, re.I)
        metadata["sop_number"] = m.group(1).upper() if m else None

    metadata["revision_history_found"] = bool(re.search(r"(?im)\b(revision history|change log|version history|anderungshistorie)\b", text))
    metadata["change_log_entries"] = len(re.findall(r"(?im)^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}.*(?:version|rev|change)\b", text))
    return metadata

def _extract_section_structure(lines: list, domain: str = "general") -> dict:
    headings = []
    for idx, line in enumerate(lines):
        if _is_heading(line):
            raw = line.strip()
            norm = _normalize_header(raw)
            numeric_prefix = re.match(r"^(\d+(?:\.\d+)*)", raw)
            level = numeric_prefix.group(1).count(".") + 1 if numeric_prefix else 1
            label = "general"
            domain_hits = []
            rule_hits = []
            for canonical, aliases in SOP_SECTION_LABELS.items():
                if any(alias in norm for alias in aliases):
                    label = canonical
                    rule_hits.append(canonical)
                    break
            sem_label, sem_score, domain_hits = _classify_header_semantically(raw, domain=domain)
            final_label = sem_label if sem_score >= 0.5 else label
            headings.append({
                "line_index": idx,
                "raw": raw,
                "normalized": norm,
                "label": final_label,
                "rule_label": label,
                "semantic_label": sem_label,
                "semantic_score": sem_score,
                "domain_hits": domain_hits,
                "rule_hits": rule_hits,
                "level": level
            })

    labels = [h["label"] for h in headings if h["label"] != "general"]
    unique_labels = sorted(set(labels))
    required = ["purpose", "scope", "responsibilities", "procedure", "records"]
    missing = [s for s in required if s not in unique_labels]
    repeated = sorted({label for label in labels if labels.count(label) > 1})
    optional = sorted([s for s in unique_labels if s not in required])

    all_caps_ratio = sum(1 for h in headings if h["raw"].isupper()) / max(len(headings), 1)
    numbering_styles = {
        "decimal": any(re.match(r"^\d+\.\d+", h["raw"]) for h in headings),
        "simple_numeric": any(re.match(r"^\d+[\.\)]\s+", h["raw"]) for h in headings),
        "roman": any(re.match(r"^(?:[IVXLC]+)[\.\)]\s+", h["raw"], re.I) for h in headings),
    }
    qdrant_links = _qdrant_semantic_section_lookup(headings, domain=domain)
    return {
        "sections": headings,
        "section_order": labels,
        "section_hierarchy_levels": [h["level"] for h in headings],
        "missing_core_sections": missing,
        "repeated_sections": repeated,
        "optional_sections": optional,
        "heading_capitalization_style": "all_caps" if all_caps_ratio > 0.6 else "title_or_sentence_case",
        "heading_numbering_style": [k for k, v in numbering_styles.items() if v] or ["none"],
        "heading_formatting_style": "numbered headings" if any(numbering_styles.values()) else "plain headings",
        "semantic_section_classifier": {
            "enabled": True,
            "method": "rule + token-overlap semantics + optional qdrant linking",
            "qdrant": qdrant_links,
        },
    }

def extract_traceability(text: str) -> dict:
    ids = TRACE_ID_PATTERN.findall(text)
    id_values = re.findall(TRACE_ID_PATTERN, text)
    flat_ids = re.findall(r"\b(?:SOP|DEV|CAPA|AUD|DEC)-[A-Z]{2,}-\d{3,}\b", text, re.I)
    entities = []
    for uid in sorted(set([x.upper() for x in flat_ids])):
        etype = uid.split("-", 1)[0]
        entities.append({"id": uid, "entity_type": etype})

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    relations = []
    entity_ids = [e["id"] for e in entities]
    for line in lines:
        refs = [eid for eid in entity_ids if eid in line.upper()]
        if len(refs) < 2:
            continue
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                a, b = refs[i], refs[j]
                relation = "related_to"
                upper_line = line.upper()
                if a.startswith("DEV-") and b.startswith("CAPA-") or b.startswith("DEV-") and a.startswith("CAPA-"):
                    relation = "addressed_by"
                elif a.startswith("AUD-") and b.startswith("CAPA-") or b.startswith("AUD-") and a.startswith("CAPA-"):
                    relation = "remediated_by"
                elif a.startswith("SOP-") and b.startswith("DEV-") or b.startswith("SOP-") and a.startswith("DEV-"):
                    relation = "has_deviation"
                elif a.startswith("SOP-") and b.startswith("DEC-") or b.startswith("SOP-") and a.startswith("DEC-"):
                    relation = "decision_for"
                elif "CAUSE" in upper_line or "ROOT" in upper_line:
                    relation = "cause_of"
                relations.append({"source": a, "target": b, "relation": relation, "evidence": line})

    # Section-context relationship inference even when IDs are not on same line.
    sections = {"deviations": [], "capa": [], "audit": [], "decisions": [], "sop": []}
    current = None
    for line in lines:
        low = line.lower().strip(":")
        if "deviation" in low or "abweich" in low:
            current = "deviations"
            continue
        if "capa" in low or "corrective" in low:
            current = "capa"
            continue
        if "audit" in low:
            current = "audit"
            continue
        if "decision" in low or "approval" in low or "entscheid" in low:
            current = "decisions"
            continue
        for uid in re.findall(r"\b(?:SOP|DEV|CAPA|AUD|DEC)-[A-Z]{2,}-\d{3,}\b", line, re.I):
            key = current if current in sections else uid.split("-", 1)[0].lower()
            if key in sections:
                sections[key].append(uid.upper())
            if uid.upper().startswith("SOP-"):
                sections["sop"].append(uid.upper())

    sop_refs = sorted(set(sections["sop"])) or [e["id"] for e in entities if e["id"].startswith("SOP-")]
    for sop_id in sop_refs:
        for dev in sorted(set(sections["deviations"])):
            relations.append({"source": sop_id, "target": dev, "relation": "has_deviation", "evidence": "section:deviations"})
        for aud in sorted(set(sections["audit"])):
            relations.append({"source": sop_id, "target": aud, "relation": "has_audit_finding", "evidence": "section:audit"})
        for dec in sorted(set(sections["decisions"])):
            relations.append({"source": sop_id, "target": dec, "relation": "decision_for", "evidence": "section:decisions"})

    # Pair DEV/CAPA and AUD/CAPA by numeric suffix when domain code matches.
    dev_map = {d.split("-")[-1]: d for d in sorted(set(sections["deviations"]))}
    aud_map = {a.split("-")[-1]: a for a in sorted(set(sections["audit"]))}
    capa_map = {c.split("-")[-1]: c for c in sorted(set(sections["capa"]))}
    for num, dev_id in dev_map.items():
        if num in capa_map:
            relations.append({"source": dev_id, "target": capa_map[num], "relation": "addressed_by", "evidence": "id_suffix_match"})
    for num, aud_id in aud_map.items():
        if num in capa_map:
            relations.append({"source": aud_id, "target": capa_map[num], "relation": "remediated_by", "evidence": "id_suffix_match"})

    # Heuristic cross-links by matching domain segment (e.g., IT) and numeric locality.
    for ent in entities:
        ent["links"] = [r for r in relations if r["source"] == ent["id"] or r["target"] == ent["id"]]

    relation_stats = {}
    for r in relations:
        relation_stats[r["relation"]] = relation_stats.get(r["relation"], 0) + 1

    return {
        "entities": entities,
        "relations": relations[:300],
        "relation_counts": relation_stats,
        "id_counts": {
            "SOP": len([e for e in entities if e["entity_type"] == "SOP"]),
            "DEV": len([e for e in entities if e["entity_type"] == "DEV"]),
            "CAPA": len([e for e in entities if e["entity_type"] == "CAPA"]),
            "AUD": len([e for e in entities if e["entity_type"] == "AUD"]),
            "DEC": len([e for e in entities if e["entity_type"] == "DEC"]),
        }
    }

def extract_workflow(text: str) -> dict:
    lines = text.splitlines()
    step_pattern = re.compile(r"^\s*(\d+(?:\.\d+)*[\.\)]?)\s+(.+)$")
    bullet_pattern = re.compile(r"^\s*[-*â€¢]\s+(.+)$")
    condition_pattern = re.compile(r"\b(if|when|unless|in case of|otherwise|nur mit|nur bei|falls)\b", re.I)
    decision_pattern = re.compile(r"\b(decide|decision|approve|reject|pass|fail|yes|no|genehmig|freigab)\b", re.I)
    exception_pattern = re.compile(r"\b(exception|deviation|abweich|escalate|escalation|error|incident|notfall)\b", re.I)
    completion_pattern = re.compile(r"\b(completed|completion|verify|validated|closed|sign[- ]?off|freigegeben|abschluss)\b", re.I)

    steps = []
    for idx, line in enumerate(lines):
        m = step_pattern.match(line)
        if m:
            step_id = m.group(1).rstrip(".)")
            content = m.group(2).strip()
            steps.append({"id": step_id, "line_index": idx, "text": content, "type": "numbered"})
            continue
        b = bullet_pattern.match(line)
        if b and steps:
            steps.append({"id": f"{steps[-1]['id']}.b{len(steps)}", "line_index": idx, "text": b.group(1).strip(), "type": "bullet_substep"})
        elif ":" in line and len(line.split()) > 3:
            label, content = line.split(":", 1)
            if content.strip():
                steps.append({"id": f"s{len(steps)+1}", "line_index": idx, "text": f"{label.strip()}: {content.strip()}", "type": "inline_instruction"})

    edges = []
    for i in range(len(steps) - 1):
        edges.append({"from": steps[i]["id"], "to": steps[i + 1]["id"], "type": "sequence"})

    decisions = [s for s in steps if decision_pattern.search(s["text"]) or condition_pattern.search(s["text"])]
    exceptions = [s for s in steps if exception_pattern.search(s["text"])]
    validations = [s for s in steps if completion_pattern.search(s["text"])]
    dependencies = [
        {"step_id": s["id"], "depends_on": steps[max(i - 1, 0)]["id"] if i > 0 else None}
        for i, s in enumerate(steps)
    ]

    return {
        "steps": steps,
        "edges": edges,
        "decision_points": [{"step_id": s["id"], "text": s["text"]} for s in decisions],
        "preconditions": [s["text"] for s in steps if re.search(r"\b(before|prior to|precondition)\b", s["text"], re.I)],
        "branches": [{"step_id": s["id"], "branch_type": "conditional"} for s in decisions],
        "dependencies": dependencies,
        "alternate_flows": [s["text"] for s in steps if re.search(r"\b(alternative|alternate|fallback)\b", s["text"], re.I)],
        "exception_handling": [s["text"] for s in exceptions],
        "escalation_paths": [s["text"] for s in steps if re.search(r"\bescalat(e|ion)\b", s["text"], re.I)],
        "validation_steps": [s["text"] for s in validations],
        "completion_criteria": [s["text"] for s in validations],
        "workflow_graph": {
            "nodes": [{"id": s["id"], "label": s["text"]} for s in steps],
            "edges": edges,
        },
    }

def _extract_roles_and_actions(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    assignments = []
    for line in lines:
        for role in ROLE_TERMS:
            if re.search(rf"\b{re.escape(role)}\b", line, re.I):
                action_match = re.search(r"\b(shall|must|should|may|is required to|is responsible for)\b\s+(.+)", line, re.I)
                action = action_match.group(2).strip() if action_match else line
                handoff = bool(re.search(r"\b(hand off|handoff|submit to|forward to|escalate to)\b", line, re.I))
                assignments.append({
                    "actor": role,
                    "action": action,
                    "object": None,
                    "responsibility": line,
                    "handoff": handoff,
                    "ownership": bool(re.search(r"\bowner|accountable|responsible\b", line, re.I)),
                })

    actor_counts = {}
    for a in assignments:
        actor_counts[a["actor"]] = actor_counts.get(a["actor"], 0) + 1

    return {
        "responsibility_matrix": assignments[:150],
        "actors_detected": sorted(actor_counts.keys()),
        "actor_distribution": actor_counts,
    }

def _extract_tone_style_profile(text: str, sentences: list, modal_counts: dict) -> dict:
    total_sent = max(len(sentences), 1)
    avg_sentence_length = sum(len(s.split()) for s in sentences) / total_sent
    imperative_terms = (
        r"verify|ensure|record|maintain|document|review|approve|check|submit|"
        r"pruefen|prÃ¼fen|sicherstellen|dokumentieren|freigeben|genehmigen|"
        r"aufzeichnen|bewerten|kontrollieren|umsetzen|archivieren"
    )
    imperative_density = sum(1 for s in sentences if re.match(rf"^({imperative_terms})\b", s.strip(), re.I)) / total_sent
    passive_hits = len(re.findall(r"\b(is|are|was|were|be|been|being)\s+\w+ed\b", text, re.I))
    passive_ratio = passive_hits / total_sent
    cautionary_hits = len(re.findall(r"\b(caution|warning|hazard|danger|achtung|warnung|gefahr|risiko)\b", text, re.I))
    compliance_focus = modal_counts.get("mandatory", 0) + len(re.findall(r"\b(compliance|audit|regulatory|shall|sop|capa|abweichung|genehmigung|freigabe|aufbewahrung|pflicht|muss)\b", text, re.I))
    sentence_lengths = [len(s.split()) for s in sentences] or [0]
    variance = sum((x - avg_sentence_length) ** 2 for x in sentence_lengths) / max(len(sentence_lengths), 1)

    formal_level = "highly_formal" if avg_sentence_length > 17 and compliance_focus > 4 else "formal" if compliance_focus > 2 else "semi_formal"
    strictness_level = "high" if modal_counts.get("mandatory", 0) >= modal_counts.get("permitted", 0) + 1 else "moderate"
    authority_level = "high" if modal_counts.get("mandatory", 0) + cautionary_hits >= 4 else "medium"

    return {
        "formality": formal_level,
        "instructional_tone": imperative_density > 0.18 or compliance_focus > 5,
        "imperative_tone": imperative_density > 0.15,
        "voice_profile": {"active_ratio_estimate": round(max(1 - passive_ratio, 0.0), 3), "passive_ratio_estimate": round(min(passive_ratio, 1.0), 3)},
        "compliance_focused_tone": compliance_focus > 3,
        "cautionary_tone": cautionary_hits > 0,
        "conciseness": "concise" if avg_sentence_length < 14 else "verbose",
        "strictness_level": strictness_level,
        "authority_level": authority_level,
        "average_sentence_length": round(avg_sentence_length, 2),
        "sentence_complexity_variance": round(variance, 2),
        "passive_voice_ratio": round(min(passive_ratio, 1.0), 3),
        "imperative_density": round(imperative_density, 3),
        "modal_distribution": modal_counts,
        "punctuation_patterns": {
            "colon_count": text.count(":"),
            "semicolon_count": text.count(";"),
            "parentheses_count": text.count("(") + text.count(")"),
        },
        "repetition_patterns": {
            "repeated_warning_tokens": len(re.findall(r"\b(?:warning|ensure|verify)\b", text, re.I)),
        },
        "consistency_across_sections": "high" if variance < 40 else "medium",
    }

def _extract_vocabulary_profile(text: str) -> dict:
    lower = text.lower()
    mandatory = re.findall(r"\b(shall|must|required to|mandatory|muss|erforderlich|pflicht)\b", lower)
    optional = re.findall(r"\b(should|may|optional|recommended|sollte|kann)\b", lower)
    prohibition = re.findall(r"\b(prohibited|must not|shall not|may not|do not|verboten|darf nicht)\b", lower)
    conditional = re.findall(r"\b(if|when|unless|in case of|otherwise|falls|bei)\b", lower)
    compliance_phrases = [
        "ensure that", "required to", "in case of", "before proceeding", "upon completion", "verify that"
    ]
    phrase_hits = [p for p in compliance_phrases if p in lower]
    words = re.findall(r"\b[a-z]{3,}\b", lower)
    ngrams = re.findall(r"\b([a-z]{3,}\s+[a-z]{3,}(?:\s+[a-z]{3,})?)\b", lower)
    common_verbs = re.findall(r"\b(ensure|verify|record|document|review|approve|maintain|submit|check)\b", lower)

    top_phrases = {}
    for ng in ngrams:
        top_phrases[ng] = top_phrases.get(ng, 0) + 1
    top_phrases = sorted(top_phrases.items(), key=lambda x: x[1], reverse=True)[:20]

    # Compute term frequency weights (density baseline)
    term_counts = {}
    for w in words:
        term_counts[w] = term_counts.get(w, 0) + 1
    weighted_terms = sorted(term_counts.items(), key=lambda x: x[1], reverse=True)[:100]

    return {
        "common_verbs": sorted(set(common_verbs)),
        "standard_instruction_phrases": phrase_hits,
        "compliance_phrases": phrase_hits,
        "mandatory_language": sorted(set(mandatory)),
        "optional_language": sorted(set(optional)),
        "prohibition_language": sorted(set(prohibition)),
        "conditional_language": sorted(set(conditional)),
        "domain_terminology_candidates": sorted(set(w for w in words if len(w) > 7))[:50],
        "repeated_ngrams": top_phrases,
        "term_frequency_weights": dict(weighted_terms)
    }

def _extract_compliance_rules(text: str) -> dict:
    rules = []
    for line in [l.strip() for l in text.splitlines() if l.strip()]:
        if re.search(r"\b(shall|must|required|mandatory|muss|erforderlich|pflicht)\b", line, re.I):
            rules.append({"type": "mandatory", "priority": "high", "text": line})
        elif re.search(r"\b(shall not|must not|prohibited|forbidden|do not|verboten|darf nicht)\b", line, re.I):
            rules.append({"type": "prohibited", "priority": "critical", "text": line})
        elif re.search(r"\b(should|recommended|sollte)\b", line, re.I):
            rules.append({"type": "recommended", "priority": "medium", "text": line})
        elif re.search(r"\b(if|when|unless|in case of|falls|bei)\b", line, re.I):
            rules.append({"type": "conditional", "priority": "high", "text": line})
        elif re.search(r"\b(safety|warning|hazard|caution|sicherheit|warnung|gefahr)\b", line, re.I):
            rules.append({"type": "safety", "priority": "critical", "text": line})
        elif re.search(r"\b(audit|inspection|traceability|record|aufzeichnung|log)\b", line, re.I):
            rules.append({"type": "audit", "priority": "high", "text": line})
        elif re.search(r"\b(regulatory|fda|ema|iso|gmp|iec)\b", line, re.I):
            rules.append({"type": "regulatory", "priority": "high", "text": line})
        elif re.search(r"\b(quality|qc|qa|acceptance criteria)\b", line, re.I):
            rules.append({"type": "quality_control", "priority": "high", "text": line})
        elif re.search(r"\b(escalate|escalation|notify supervisor)\b", line, re.I):
            rules.append({"type": "escalation", "priority": "high", "text": line})
        elif re.search(r"\b(approval|approved by|sign-off)\b", line, re.I):
            rules.append({"type": "approval", "priority": "high", "text": line})
        elif re.search(r"\b(retention|retain|archive|records)\b", line, re.I):
            rules.append({"type": "retention", "priority": "medium", "text": line})
    counts = {}
    for r in rules:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    return {"rules": rules[:200], "rule_type_counts": counts}

def _extract_formatting_profile(text: str, lines: list) -> dict:
    numbering_style = "decimal" if re.search(r"^\s*\d+\.\d+\s+", text, re.M) else "numeric" if re.search(r"^\s*\d+[\.\)]\s+", text, re.M) else "none"
    bullet_style = "dash_or_star" if re.search(r"^\s*[-*]\s+", text, re.M) else "none"
    sub_bullet_style = "indented" if re.search(r"^\s{2,}[-*]\s+", text, re.M) else "flat"
    indentation_behavior = "mixed" if re.search(r"^\t|\s{2,}", text, re.M) else "minimal"
    note_blocks = len(re.findall(r"(?im)^\s*(note|warning|caution)\s*[:\-]", text))
    caps_headers = sum(1 for l in lines if l.strip() and l.strip().isupper())

    return {
        "numbering_style": numbering_style,
        "bullet_style": bullet_style,
        "sub_bullet_style": sub_bullet_style,
        "indentation_behavior": indentation_behavior,
        "table_usage": text.count("|") > 4,
        "note_warning_blocks": note_blocks,
        "bold_emphasis_markers": len(re.findall(r"\*\*[^*]+\*\*", text)),
        "italic_emphasis_markers": len(re.findall(r"_[^_]+_", text)),
        "all_caps_headers_count": caps_headers,
        "section_separators_count": len(re.findall(r"^\s*[-=_]{3,}\s*$", text, re.M)),
        "revision_table_layout_detected": bool(re.search(r"(?im)\b(revision|change)\b.*\b(date|version)\b", text)),
    }

def _detect_domain_context(text: str) -> dict:
    lower = text.lower()
    scores = {}
    for domain, terms in DOMAIN_TERM_BANKS.items():
        scores[domain] = sum(1 for t in terms if t in lower) / max(len(terms), 1)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return {
        "predicted_domain": ranked[0][0] if ranked and ranked[0][1] > 0 else "general",
        "domain_scores": dict(ranked),
    }

def _learn_sop_style_signature(text: str, vocabulary: dict, tone_style: dict, compliance: dict) -> dict:
    line_samples = [l.strip() for l in text.splitlines() if l.strip()]
    control_tokens = sorted(set(vocabulary.get("mandatory_language", []) + vocabulary.get("prohibition_language", [])))
    rule_total = sum(compliance.get("rule_type_counts", {}).values())
    style_class = "strict_compliance" if tone_style.get("strictness_level") == "high" and rule_total >= 4 else "procedural_formal"
    return {
        "style_class": style_class,
        "control_language_tokens": control_tokens[:30],
        "compliance_density": round(rule_total / max(len(line_samples), 1), 3),
        "tone_signature": {
            "formality": tone_style.get("formality"),
            "authority_level": tone_style.get("authority_level"),
            "instructional_tone": tone_style.get("instructional_tone"),
        },
        "formatting_signature": {
            "line_prefix_patterns": [l.split()[0] for l in line_samples[:30] if l.split()],
            "avg_line_length_words": round(sum(len(l.split()) for l in line_samples) / max(len(line_samples), 1), 2),
        },
    }

def build_structured_sop_profile(result: dict) -> dict:
    language = result.get("language", {})
    features = result.get("features", {})
    classification = result.get("classification", {})
    sop = result.get("sop_profile") or features.get("sop_intelligence", {})
    traceability = sop.get("traceability", {})
    
    # New v2.0 data
    writing_style = result.get("writing_style", {})
    tone_profile = result.get("tone_profile", {})
    domain_info = result.get("domain", {})
    quality_metrics = result.get("quality_metrics", {})
    symbol_table = result.get("symbol_table", {})
    
    profile = {
        "document_identity": {
            "language": language.get("lang_code"),
            "iso_code": language.get("iso_code"),
            "is_bilingual": language.get("is_bilingual"),
            "bilingual_pair": language.get("bilingual_pair"),
            "script_type": language.get("script_type"),
            "sop_id": (sop.get("document_metadata") or {}).get("sop_number"),
            "title": (sop.get("document_metadata") or {}).get("title"),
            "primary_domain": domain_info.get("primary_domain"),
            "domain_confidence": domain_info.get("domain_confidence"),
            "standards_detected": domain_info.get("applicable_standards", []),
        },
        "style_and_tone": {
            "primary_style": writing_style.get("primary_style"),
            "chunking_strategy": writing_style.get("chunking_strategy_selected"),
            "primary_tone": tone_profile.get("primary_tone"),
            "tone_modifiers": tone_profile.get("sub_tone_modifiers", []),
            "formality_level": tone_profile.get("formality_level"),
            "authority_level": tone_profile.get("authority_level"),
        },
        "classification": {
            "primary_genre": classification.get("primary_genre"),
            "genre_confidence": classification.get("genre_confidence"),
            "qms_sub_type": classification.get("qms_sub_type"),
            "legal_agreement_type": classification.get("legal_agreement_type"),
            "is_ambiguous": classification.get("is_ambiguous")
        },
        "compliance_categories": {
            "deviations": [c.get("title") for c in result.get("chunks", []) if c.get("section_type") == "DEVIATIONS"],
            "capas": [c.get("title") for c in result.get("chunks", []) if c.get("section_type") == "CAPA"],
            "audit_findings": [c.get("title") for c in result.get("chunks", []) if c.get("section_type") == "AUDIT_FINDINGS"],
            "decisions": [c.get("title") for c in result.get("chunks", []) if c.get("section_type") == "DECISIONS"],
            "sop_content": [c.get("title") for c in result.get("chunks", []) if c.get("section_type") not in ["DEVIATIONS", "CAPA", "AUDIT_FINDINGS", "DECISIONS"]]
        },
        "structure": {
            "sections": sop.get("section_structure", {}),
            "chunks": result.get("chunks", []),
            "writing_style_metrics": writing_style
        },
        "consistency": sop.get("consistency", {}),
        "workflow": {
            "workflow_steps": sop.get("workflow", {}),
            "step_completeness": sop.get("step_completeness", {}),
            "sla_register": sop.get("sla_register", [])
        },
        "traceability": traceability,
        "symbol_table": symbol_table,
        "compliance": {
            "rules": sop.get("compliance_rules", {}),
            "control_coverage": sop.get("control_coverage", {}),
            "legal_boilerplate": sop.get("legal_boilerplate", {}),
            "section_order": sop.get("section_order", {})
        },
        "vocabulary": {
            "general": sop.get("vocabulary_profile", {}),
            "precision_terms": sop.get("precision_terms", {}),
            "domain_vocabulary": domain_info.get("role_vocabulary_match", []),
            "bilingual_glossary": result.get("bilingual_glossary", {}),
            "domain_seeds": result.get("domain_seeds", [])
        },
        "precision_blocklist": result.get("precision_blocklist", ""),
        "quality_metrics": quality_metrics,
        "scores": {
            "overall_quality_score": quality_metrics.get("domain_quality_score", 0),
            "consistency_score": sop.get("consistency", {}).get("consistency_score", 0),
            "step_score": sop.get("step_completeness", {}).get("step_score", 0),
            "control_coverage_score": sop.get("control_coverage", {}).get("control_coverage_score", 0),
            "order_compliance_score": sop.get("section_order", {}).get("order_compliance_score", 0),
            "precision_coverage": sop.get("precision_terms", {}).get("controlled_vocabulary_coverage", 0)
        },
        "audit_report": sop.get("audit_report", {}),
        "guardrails": sop.get("style_guardrails", {}),
        "bilingual_glossary": result.get("bilingual_glossary", {}),
        "domain_seeds": result.get("domain_seeds", [])
    }
    return profile

LLM_TASK_DIRECTIVES = {
    "rewrite": "Rewrite the SOP while preserving meaning, traceability IDs, and procedural order.",
    "improve": "Improve clarity, consistency, and compliance quality without changing intent or control outcomes.",
    "create_new": "Generate a new SOP in the same domain/style using the learned structure and control language.",
    "summarize": "Summarize SOP controls and workflow without omitting critical compliance constraints.",
    "gap_analysis": "Identify missing sections, weak controls, and traceability gaps against the learned SOP profile.",
    "translate": "Translate the SOP while preserving IDs, compliance modality, and procedural hierarchy.",
}

def build_sop_llm_system_prompt(structured_profile: dict, task: str = "rewrite") -> str:
    directive = LLM_TASK_DIRECTIVES.get(task, LLM_TASK_DIRECTIVES["rewrite"])
    
    identity = structured_profile.get("classification", {})
    doc_identity = structured_profile.get("document_identity", {})
    style_tone = structured_profile.get("style_and_tone", {})
    
    primary_genre = identity.get("primary_genre", "sop_qms")
    domain = doc_identity.get("primary_domain", "general")
    language = doc_identity.get("language", "en")
    
    primary_style = style_tone.get("primary_style", "procedural")
    primary_tone = style_tone.get("primary_tone", "formal")
    
    # Raw Signal Calibration (Gap closure)
    tone_signals = style_tone.get("tone_signals", {})
    shall_ratio = tone_signals.get("SHALL_ratio", "0.0")
    passive_ratio = tone_signals.get("passive_ratio", "0.0")
    
    precision_blocklist = structured_profile.get("precision_blocklist", "")
    domain_seeds = structured_profile.get("domain_seeds", [])
    seed_block = f"\nDOMAIN VOCABULARY SEEDS (Use these for precision):\n{', '.join(domain_seeds[:30])}" if domain_seeds and task == "create_new" else ""

    return (
        "You are the Universal SOP Intelligence Engine — a self-adapting audit-grade NLP system.\n"
        f"CONTEXT: Domain={domain.upper()} | Genre={primary_genre.upper()} | Language={language.upper()}\n"
        f"DETECTED STYLE: {primary_style.upper()} | DETECTED TONE: {primary_tone.upper()}\n"
        f"SIGNAL CALIBRATION: SHALL_Ratio={shall_ratio} | Passive_Ratio={passive_ratio}\n\n"
        "STRICT ADHERENCE TO LEARNED SIGNALS:\n"
        "- NO HALLUCINATION: Do NOT invent information. Do NOT add 'Revision History', 'Audit Reports', or 'Definitions' unless they are in the provided text.\n"
        "- DATA INTEGRITY: Preserved ALL descriptions. If an ID (DEV-IT-xxx, CAPA-IT-xxx) has a description, you MUST keep the description.\n"
        "- NO GUESSING: Do NOT guess the meaning of IDs. Only use the labels provided in the source text.\n"
        "- TRACEABILITY: Never alter or remove compliance IDs (SOP/DEV/CAPA/AUD/DEC/ISO/REF).\n"
        "- REPETITION GUARD: Rewrite ONLY the provided section. Do NOT repeat content from other sections or the document header.\n"
        f"{precision_blocklist}\n"
        f"{seed_block}\n"
        f"\nTASK DIRECTIVE: {directive}"
    )

def build_sop_llm_prompt(structured_profile: dict, task: str = "rewrite") -> str:
    rules = structured_profile.get("guardrails", {})
    identity = structured_profile.get("document_identity", {})
    style_tone = structured_profile.get("style_and_tone", {})
    section_order = structured_profile.get("compliance", {}).get("section_order", {}).get("canonical_order", [])
    trace = structured_profile.get("traceability", {}).get("relation_counts", {})
    directive = LLM_TASK_DIRECTIVES.get(task, LLM_TASK_DIRECTIVES["rewrite"])
    
    workflow = structured_profile.get("workflow", {})
    step_details = workflow.get("step_completeness", {}).get("step_details", [])
    
    prompt_payload = {
        "task": task,
        "task_directive": directive,
        "document_identity": identity,
        "style_and_tone": style_tone,
        "required_section_order": section_order,
        "traceability_counts": trace,
        "audit_report": structured_profile.get("audit_report", {}),
        "granular_step_audit": step_details,
        "bilingual_glossary": structured_profile.get("bilingual_glossary", {}),
        "quality_metrics": structured_profile.get("quality_metrics", {}),
        "term_weights": structured_profile.get("vocabulary", {}).get("general", {}).get("term_frequency_weights", {}),
        "symbol_table_summary": {
            "roles": structured_profile.get("symbol_table", {}).get("roles", []),
            "ids": structured_profile.get("symbol_table", {}).get("traceability_ids", [])
        }
    }
    return (
        f"{build_sop_llm_system_prompt(structured_profile, task=task)}\n\n"
        "USE THE FOLLOWING STRUCTURED PROFILE AS HARD CONSTRAINTS FOR YOUR OUTPUT:\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}"
    )

def build_sop_llm_prompts(structured_profile: dict, tasks: list = None) -> dict:
    requested_tasks = tasks or ["rewrite", "improve", "create_new"]
    prompts = {}
    for task in requested_tasks:
        prompts[task] = {
            "system_prompt": build_sop_llm_system_prompt(structured_profile, task=task),
            "user_prompt": build_sop_llm_prompt(structured_profile, task=task),
        }
    return prompts

def extract_sop_intelligence(text: str, sentences: list, lines: list, modal_counts: dict, lang_info: dict = None, v2_tone_info: dict = None, v2_domain_info: dict = None) -> dict:
    metadata = _extract_metadata(text)
    domain = v2_domain_info or _detect_domain_context(text)
    section_structure = _extract_section_structure(lines, domain=domain.get("predicted_domain", "general"))
    workflow = extract_workflow(text)
    roles = _extract_roles_and_actions(text)
    tone_style = v2_tone_info or _extract_tone_style_profile(text, sentences, modal_counts)
    vocabulary = _extract_vocabulary_profile(text)
    compliance = _extract_compliance_rules(text)
    formatting = _extract_formatting_profile(text, lines)
    traceability = extract_traceability(text)
    detected_language = _infer_sop_language(text, (lang_info or {}).get("lang_code", "en"))
    style_learning = _learn_sop_style_signature(text, vocabulary, tone_style, compliance)

    sop_strength = (
        0.25 * min(len(section_structure["section_order"]) / 8, 1.0)
        + 0.20 * min(len(workflow["steps"]) / 10, 1.0)
        + 0.20 * min(sum(compliance["rule_type_counts"].values()) / 12, 1.0)
        + 0.10 * min(sum(traceability.get("id_counts", {}).values()) / 10, 1.0)
        + 0.15 * (1.0 if metadata["sop_number"] else 0.0)
        + 0.10 * (1.0 if tone_style.get("compliance_focused_tone") else 0.0)
    )
    style_guardrails = {
        "allow_tone": ["formal", "highly_formal", "instructional", "compliance-focused"],
        "avoid_tone": ["casual", "creative_poetic", "slang-heavy", "promotional"],
        "required_keywords": vocabulary["mandatory_language"][:10] + vocabulary["standard_instruction_phrases"][:10],
        "forbidden_patterns": ["yo", "ain't", "no cap", "metaphorical flourish", "storytelling digression"],
        "preserve_domain": domain.get("predicted_domain", "general"),
        "preserve_language": detected_language,
        "language_policy": f"rewrite must remain in detected SOP language: {detected_language}",
    }
    return {
        "document_metadata": metadata,
        "section_structure": section_structure,
        "workflow": workflow,
        "traceability": traceability,
        "roles_and_actors": roles,
        "tone_and_style": tone_style,
        "style_learning": style_learning,
        "vocabulary_profile": vocabulary,
        "compliance_rules": compliance,
        "formatting_profile": formatting,
        "domain_context": domain,
        "language_profile": {
            "detected_sop_language": detected_language,
            "language_pack": SOP_LANGUAGE_PACKS[detected_language],
        },
        "sop_strength_score": round(min(sop_strength, 1.0), 3),
        "style_guardrails": style_guardrails,
    }

def learn_sop_corpus_patterns(documents: list) -> dict:
    profiles = [extract_sop_intelligence(doc, [s.strip() for s in re.split(r"[.!?]+", doc) if s.strip()], doc.splitlines(), {
        "mandatory": len(re.findall(r"\b(shall|must)\b", doc, re.I)),
        "recommended": len(re.findall(r"\bshould\b", doc, re.I)),
        "permitted": len(re.findall(r"\b(may|can)\b", doc, re.I)),
        "prohibited": len(re.findall(r"\b(shall not|must not|cannot)\b", doc, re.I)),
    }) for doc in documents if doc and doc.strip()]
    if not profiles:
        return {"document_count": 0}

    section_orders = [" > ".join(p["section_structure"]["section_order"]) for p in profiles if p["section_structure"]["section_order"]]
    domain_votes = {}
    mandatory_terms = {}
    formatting_votes = {}
    relation_votes = {}
    style_classes = {}
    for p in profiles:
        dom = p["domain_context"]["predicted_domain"]
        domain_votes[dom] = domain_votes.get(dom, 0) + 1
        for term in p["vocabulary_profile"]["mandatory_language"]:
            mandatory_terms[term] = mandatory_terms.get(term, 0) + 1
        fmt = p["formatting_profile"]["numbering_style"]
        formatting_votes[fmt] = formatting_votes.get(fmt, 0) + 1
        sclass = p.get("style_learning", {}).get("style_class", "procedural_formal")
        style_classes[sclass] = style_classes.get(sclass, 0) + 1
        for rel, count in p.get("traceability", {}).get("relation_counts", {}).items():
            relation_votes[rel] = relation_votes.get(rel, 0) + count

    return {
        "document_count": len(profiles),
        "common_structural_pattern": max(section_orders, key=section_orders.count) if section_orders else "",
        "common_tone": max([p["tone_and_style"]["formality"] for p in profiles], key=[p["tone_and_style"]["formality"] for p in profiles].count),
        "common_vocabulary": sorted(mandatory_terms.items(), key=lambda x: x[1], reverse=True)[:20],
        "common_section_order": max(section_orders, key=section_orders.count) if section_orders else "",
        "common_formatting_style": max(formatting_votes, key=formatting_votes.get) if formatting_votes else "none",
        "common_procedure_logic": {
            "avg_step_count": round(sum(len(p["workflow"]["steps"]) for p in profiles) / max(len(profiles), 1), 2),
            "decision_frequency": round(sum(len(p["workflow"]["decision_points"]) for p in profiles) / max(len(profiles), 1), 2),
        },
        "common_compliance_language": sorted(mandatory_terms.items(), key=lambda x: x[1], reverse=True)[:10],
        "dominant_domain": max(domain_votes, key=domain_votes.get) if domain_votes else "general",
        "common_traceability_relations": sorted(relation_votes.items(), key=lambda x: x[1], reverse=True)[:20],
        "dominant_style_class": max(style_classes, key=style_classes.get) if style_classes else "procedural_formal",
    }

def extract_features(text: str, lang_info: dict, v2_tone_info: dict = None, v2_domain_info: dict = None) -> dict:
    lang_code = lang_info["lang_code"]
    nlp = _get_spacy(lang_code) or _get_spacy("en")
    
    total_words = len(text.split())
    lines = text.split('\n')
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    pos_dist = {"noun_ratio": 0.0, "verb_ratio": 0.0, "adj_ratio": 0.0, "adv_ratio": 0.0}
    voice = "active"
    org_density = 0.0
    date_density = 0.0
    ttr = 0.0
    formal_morph = 0
    
    if nlp:
        # Load full document might be too heavy, taking a sample if too long
        doc = nlp(text[:10000])
        doc_words = [t for t in doc if not t.is_punct and not t.is_space]
        total_doc_words = max(len(doc_words), 1)
        
        counts = {"NOUN": 0, "VERB": 0, "ADJ": 0, "ADV": 0}
        for t in doc_words:
            if t.pos_ in counts:
                counts[t.pos_] += 1
                
        pos_dist = {
            "noun_ratio": counts["NOUN"] / total_doc_words,
            "verb_ratio": counts["VERB"] / total_doc_words,
            "adj_ratio":  counts["ADJ"]  / total_doc_words,
            "adv_ratio":  counts["ADV"]  / total_doc_words,
        }
        
        passive_count = sum(1 for t in doc if t.dep_ == "auxpass")
        voice = "passive" if passive_count > len(list(doc.sents)) * 0.3 else "active"
        
        ents = [e.label_ for e in doc.ents]
        org_density = ents.count("ORG") / total_doc_words
        date_density = ents.count("DATE") / total_doc_words
        
        lemmas = set(t.lemma_.lower() for t in doc if not t.is_stop)
        ttr = len(lemmas) / total_doc_words
        
        FORMAL_SUFFIXES  = ["tion", "ment", "ance", "ence", "ize", "ise"]
        formal_morph = sum(1 for t in doc_words if any(t.text.lower().endswith(s) for s in FORMAL_SUFFIXES))

    flesch = textstat.flesch_reading_ease(text)
    fog    = textstat.gunning_fog(text)
    
    MODALS = {
        "mandatory":   r'\b(shall|must|required|mandatory|muss|muessen|mÃ¼ssen|pflicht|erforderlich|verpflichtend)\b',
        "recommended": r'\b(should|sollte|empfohlen)\b',
        "permitted":   r'\b(may|can|darf|kann)\b',
        "prohibited":  r'\b(shall not|must not|cannot|darf nicht|duerfen nicht|dÃ¼rfen nicht|verboten)\b',
    }
    modal_counts = {k: len(re.findall(v, text, re.I)) for k, v in MODALS.items()}
    
    short_line_ratio = sum(1 for l in lines if len(l.split()) < 8) / max(len(lines), 1)
    
    sop_features = sop_style_features(text, lines)
    legal_features = legal_style_features(text, lines)
    is_compliance_like = (
        sop_features.get("format_score", 0) >= 0.55
        or sop_features.get("compliance_id_count", 0) >= 3
        or legal_features.get("legalese_score", 0) >= 0.55
    )
    
    # Only run poetic features if not compliance-like
    poetic_features = poetic_style_features(text, lines, total_words, pos_dist, ttr) if not is_compliance_like else {
        "is_poetic": False, "is_lineated": False, "figurative_density": 0, "musicality_score": 0, "compression_score": 0
    }
    
    structure = {
        "has_numbered_steps": bool(re.search(r'^\s*\d+[\.\)]\s', text, re.MULTILINE)),
        "has_sub_steps":      bool(re.search(r'^\s*\d+\.\d+',    text, re.MULTILINE)),
        "has_headers":        any(l.isupper() and len(l.split()) < 10 for l in lines),
        "has_bullets":        any(l.strip().startswith(("-","â€¢","*","â€“")) for l in lines),
        "has_table":          text.count("|") > 4,
        "has_notes":          any(l.strip().upper().startswith(("NOTE:","WARNING:","CAUTION:")) for l in lines),
        "stanza_like":        (short_line_ratio > 0.55) and not is_compliance_like,
        "rhyme_score":        rhyme_score(lines),
        "poetic_lineation":   poetic_features["is_lineated"] and not is_compliance_like,
    }
    
    TERM_BANKS = {
        "sop":     ["shall", "ensure", "verify", "document", "record",
                    "maintain", "comply", "procedure", "protocol", "operator"],
        "legal":   ["hereby", "pursuant", "whereas", "notwithstanding",
                    "indemnify", "liable", "covenant", "jurisdiction"],
        "academic":["furthermore", "methodology", "hypothesis", "empirical",
                    "analysis", "et al", "ibid", "literature"],
        "rap":     ["yo", "gonna", "ain't", "no cap", "bars", "flow", "drip"],
        "poetry":  ["thee", "thou", "hath", "doth", "o'er", "ere", "midst"],
        "religious":["blessed", "almighty", "prayer", "mercy", "faith", "divine"],
    }

    terminology_scores = {
        genre: sum(1 for t in terms if t in text.lower()) / len(terms)
        for genre, terms in TERM_BANKS.items()
    }
    
    avg_sent_len = total_words / max(len(sentences), 1)
    avg_word_len = sum(len(w) for w in text.split()) / max(total_words, 1)
    sop_intelligence = extract_sop_intelligence(
        text, sentences, lines, modal_counts, 
        lang_info=lang_info, 
        v2_tone_info=v2_tone_info, 
        v2_domain_info=v2_domain_info
    )

    return {
        "pos_dist": pos_dist,
        "voice": voice,
        "org_density": org_density,
        "date_density": date_density,
        "ttr": ttr,
        "formal_morph": formal_morph,
        "readability": {"flesch": flesch, "fog": fog},
        "modal_verbs": modal_counts,
        "structure": structure,
        "poetic_style": poetic_features,
        "sop_style": sop_features,
        "sop_intelligence": sop_intelligence,
        "legal_style": legal_features,
        "terminology_scores": terminology_scores,
        "avg_sent_len": avg_sent_len,
        "avg_word_len": avg_word_len
    }

def classify_genre(features: dict, text: str = "") -> dict:
    t  = features["terminology_scores"]
    st = features["structure"]
    m  = features["modal_verbs"]
    fl = features["readability"]["flesch"] or 50
    sop_style = features["sop_style"]
    legal_style = features["legal_style"]
    sop_intelligence = features.get("sop_intelligence", {})

    scores = {
        "sop_qms":  t.get("sop",0)*0.25 + st["has_numbered_steps"]*0.20
                    + st["has_headers"]*0.10 + (m.get("mandatory",0)>3)*0.10
                    + features["sop_style"]["format_score"]*0.25
                    + features["sop_style"]["control_language_score"]*0.10
                    + sop_intelligence.get("sop_strength_score", 0)*0.15,

        "legal":    t.get("legal",0)*0.30 + (features["avg_sent_len"]>25)*0.10
                    + (features["avg_word_len"]>6)*0.10
                    + (features["voice"]=="passive")*0.05
                    + features["legal_style"]["legalese_score"]*0.30
                    + min(features["legal_style"]["clause_density"], 1.0)*0.15,

        "academic": t.get("academic",0)*0.50 + (fl<45)*0.30
                    + (features["voice"]=="passive")*0.20,

        "poetry":   (st["rhyme_score"]>0.3)*0.30 + st["stanza_like"]*0.20
                    + (features["avg_sent_len"]<10)*0.15
                    + (features["poetic_style"]["musicality_score"]>0.35)*0.20
                    + (features["poetic_style"]["figurative_density"]>0.8)*0.15,

        "policy":   (fl>50)*0.20 + (features["avg_sent_len"]<18)*0.20
                    + (m.get("mandatory",0)>1)*0.20 + st["has_headers"]*0.20,

        "technical_manual": (t.get("it",0)*0.40) + st["has_numbered_steps"]*0.30
                            + (features["sop_style"]["format_score"]*0.15),

        "general":  0.10
    }

    strong_sop = (
        sop_style["format_score"] >= 0.55
        and (sop_style["qms_term_hits"] >= 3 or sop_style["compliance_id_count"] >= 3)
    )
    strong_legal = legal_style["legalese_score"] >= 0.55 and (
        legal_style["numbered_clause_count"] >= 2 or legal_style["whereas_count"] > 0
    )

    if strong_sop:
        scores["sop_qms"] += 0.45
        scores["poetry"] *= 0.15
        scores["academic"] *= 0.15

    if strong_legal:
        scores["legal"] += 0.45
        scores["sop_qms"] *= 0.45 if not strong_sop else 1.0
        scores["poetry"] *= 0.10

    total = sum(scores.values()) or 1
    probs = {k: round(v/total, 3) for k, v in scores.items()}
    ranked = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    primary_genre = ranked[0][0]
    confidence = ranked[0][1]

    if primary_genre == "sop_qms" and strong_sop:
        confidence = max(confidence, 0.82)
    elif primary_genre == "legal" and strong_legal:
        confidence = max(confidence, 0.82)

    # Classify QMS Sub-type
    qms_sub_type = None
    qms_sub_type_confidence = 0.0
    if primary_genre in ("sop_qms", "policy", "technical_manual") or confidence < 0.70:
        lower_text = text.lower()
        sub_scores = {}
        for sub, terms in QMS_SUB_TYPE_BANKS.items():
            sub_scores[sub] = sum(1 for t in terms if t in lower_text) / max(len(terms), 1)
        sub_ranked = sorted(sub_scores.items(), key=lambda x: x[1], reverse=True)
        if sub_ranked and sub_ranked[0][1] > 0:
            qms_sub_type = sub_ranked[0][0]
            qms_sub_type_confidence = round(min(sub_ranked[0][1] * 2.5, 1.0), 3)

    # Classify Legal Agreement Type
    legal_agreement_type = None
    if primary_genre == "legal" or confidence < 0.70:
        lower_text = text.lower()[:5000] # Usually in the preamble/title
        for ltype, patterns in LEGAL_AGREEMENT_PATTERNS.items():
            if any(p in lower_text for p in patterns):
                legal_agreement_type = ltype
                break

    ambiguous = confidence < 0.70

    return {
        "primary_genre": primary_genre,
        "genre_confidence": confidence,
        "qms_sub_type": qms_sub_type,
        "qms_sub_type_confidence": qms_sub_type_confidence,
        "legal_agreement_type": legal_agreement_type,
        "runner_up_genre": ranked[1][0],
        "all_genre_scores": dict(ranked),
        "is_ambiguous": ambiguous
    }

def run_cross_section_consistency(text: str, section_structure: dict, traceability: dict, metadata: dict) -> dict:
    violations = []
    
    # 1. Role coverage (are responsibilities mentioned in procedure?)
    roles = section_structure.get("responsibilities", [])
    procedure = " ".join(section_structure.get("procedure", [])).lower()
    for role in roles:
        if role.lower() not in procedure and len(role.split()) < 5:
            violations.append({"type": "qms_role_missing_in_procedure", "detail": f"Role '{role}' listed but not found in procedure steps."})
            
    # 2. Traceability completeness (DEV/CAPA mentioned but no traceability section?)
    if re.search(r"\b(DEV|CAPA)-[A-Z]{2,}-\d{3,}\b", text, re.I) and not traceability.get("id_counts"):
        violations.append({"type": "qms_traceability_orphaned", "detail": "Traceability IDs found in text but no official traceability or references section present."})
        
    # 3. Empty sections
    for sec_name, content in section_structure.items():
        if not content and sec_name not in ["section_order", "responsibilities", "procedure", "exceptions"]:
            violations.append({"type": "qms_empty_section", "detail": f"Section '{sec_name}' is declared but empty."})

    score = max(1.0 - (len(violations) * 0.15), 0.0)
    return {
        "consistency_score": round(score, 3),
        "violations": violations,
        "passed": len(violations) == 0,
        "failed": len(violations) > 0
    }

def check_step_completeness(steps: list, lang_code: str) -> dict:
    nlp = _get_spacy(lang_code) or _get_spacy("en")
    if not nlp:
        return {"step_score": 0.0, "step_completeness_ratio": 0.0, "actorless_obligations": []}
    total_steps = len(steps)
    if total_steps == 0:
        return {"step_score": 0.0, "step_completeness_ratio": 0.0, "actorless_obligations": []}
        
    step_details = []
    complete_count = 0
    actorless = []
    for i, step in enumerate(steps):
        doc = nlp(step[:1000])
        has_actor = any(t.dep_ in ("nsubj", "nsubjpass") and not t.is_stop for t in doc)
        has_action = any(t.pos_ == "VERB" for t in doc)
        has_object = any(t.dep_ in ("dobj", "pobj") for t in doc)
        is_passive_obligation = any(t.dep_ == "auxpass" for t in doc) and any(w in step.lower() for w in ["shall", "must", "required"])
        
        step_score = 0
        if has_actor: step_score += 1
        if has_action: step_score += 1
        if has_object: step_score += 1
        if not is_passive_obligation or has_actor: step_score += 1
        
        if step_score >= 3:
            complete_count += 1
            
        if is_passive_obligation and not has_actor:
            actorless.append(step)
            
        step_details.append({
            "step_index": i + 1,
            "text": step[:100],
            "completeness_score": f"{step_score}/4",
            "missing_elements": [
                e for e, present in [("actor", has_actor), ("action", has_action), ("object", has_object)] if not present
            ]
        })
            
    ratio = complete_count / total_steps
    score = ratio * 4.0
    return {
        "step_score": round(score, 2),
        "step_completeness_ratio": round(ratio, 3),
        "actorless_obligations": actorless,
        "step_details": step_details[:50] # Limit to top 50 steps for prompt safety
    }

def extract_sla_register(text: str) -> list:
    if not DATEPARSER_AVAILABLE:
        return []
        
    sla_register = []
    # Simplified regex for temporal deadlines
    patterns = [
        r"(within\s+\d+\s+(?:days|hours|weeks|months))",
        r"(no later than\s+\d+\s+(?:days|hours|weeks|months))",
        r"(by\s+(?:the\s+)?end of\s+(?:the\s+)?(?:day|week|month))"
    ]
    
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for p in patterns:
            match = re.search(p, line, re.I)
            if match:
                sla_register.append({
                    "deadline_phrase": match.group(1),
                    "context": line.strip(),
                    "line_number": i + 1,
                    "deadline_type": "relative" if "within" in match.group(1).lower() else "absolute"
                })
    return sla_register

def build_control_coverage_matrix(roles: list, steps: list, traceability: dict) -> dict:
    gap_findings = []
    covered = 0
    total = max(len(roles), 1)
    
    proc_text = " ".join(steps).lower()
    
    for role in roles:
        role_lower = role.lower()
        if role_lower in proc_text:
            covered += 1
        else:
            gap_findings.append(f"Role '{role}' lacks explicit procedure coverage.")
            
    if not traceability.get("id_counts") and "record" not in proc_text:
        gap_findings.append("No record retention or traceability mechanism found for procedure controls.")
        
    score = covered / total
    return {
        "control_coverage_score": round(score, 3),
        "gap_findings": gap_findings
    }

def check_legal_boilerplate(text: str, agreement_type: str) -> dict:
    inventory = {
        "NDA": ["confidentiality", "term", "return of materials", "injunctive relief", "governing law"],
        "MSA": ["services", "payment", "term", "termination", "liability", "indemnification", "governing law"],
        "Employment": ["position", "compensation", "benefits", "termination", "at-will", "confidentiality"]
    }
    expected = inventory.get(agreement_type, ["term", "termination", "governing law"])
    
    lower = text.lower()
    present = []
    missing = []
    
    for clause in expected:
        if clause in lower:
            present.append(clause)
        else:
            missing.append(clause)
            
    score = len(present) / max(len(expected), 1)
    
    return {
        "boilerplate_score": round(score, 3),
        "expected_clauses": expected,
        "present_clauses": present,
        "missing_clauses": missing,
        "flags": [{"level": "major" if c in ["governing law", "liability"] else "minor", "clause": c} for c in missing]
    }

def validate_section_order(sections_found: list, primary_genre: str, qms_sub_type: str, domain: str = "general") -> dict:
    """Stage 8: Domain-adaptive canonical section order validation."""

    # Domain-adaptive canonical orders
    CANONICAL_ORDERS = {
        "QMS_general": ["PURPOSE", "SCOPE", "DEFINITIONS", "RESPONSIBILITIES", "PROCEDURE", "RECORDS", "REFERENCES", "REVISION_HISTORY"],
        "legal_regulatory": ["PURPOSE", "DEFINITIONS", "REPRESENTATIONS", "CONFIDENTIALITY", "INDEMNIFICATION", "TERMINATION", "GOVERNING_LAW"],
        "pharma_gmp": ["PURPOSE", "SCOPE", "DEFINITIONS", "RESPONSIBILITIES", "PROCEDURE", "BATCH_RECORD", "DEVIATIONS", "REFERENCES"],
        "cybersecurity": ["PURPOSE", "SCOPE", "THREAT_MODEL", "ACCESS_CONTROL", "PROCEDURE", "INCIDENT_RESPONSE", "RECORDS", "REFERENCES"],
        "healthcare_clinical": ["PURPOSE", "SCOPE", "PATIENT_SAFETY", "RESPONSIBILITIES", "PROCEDURE", "RECORDS", "REFERENCES"],
        "IT_OT_security": ["PURPOSE", "SCOPE", "ACCESS_CONTROL", "PROCEDURE", "INCIDENT_RESPONSE", "RECORDS", "REFERENCES"],
        "medical_devices": ["PURPOSE", "SCOPE", "DEFINITIONS", "RESPONSIBILITIES", "PROCEDURE", "RECORDS", "DEVIATIONS", "REFERENCES"],
        "general": ["PURPOSE", "SCOPE", "DEFINITIONS", "RESPONSIBILITIES", "PROCEDURE", "REFERENCES"],
    }

    # Normalize sections_found using universal labels
    actual_labels = []
    for s in sections_found:
        label = assign_universal_section_labels(str(s), domain)
        actual_labels.append(label)

    # Select canonical order
    canonical = CANONICAL_ORDERS.get(domain, CANONICAL_ORDERS.get("general"))
    if primary_genre == "legal" or "legal" in (qms_sub_type or ""):
        canonical = CANONICAL_ORDERS["legal_regulatory"]
    elif primary_genre == "sop_qms":
        canonical = CANONICAL_ORDERS.get(domain, CANONICAL_ORDERS["QMS_general"])

    # LCS (Longest Common Subsequence) to compute ordering compliance
    lcs = 0
    i, j = 0, 0
    while i < len(canonical) and j < len(actual_labels):
        if canonical[i] == actual_labels[j]:
            lcs += 1
            i += 1
            j += 1
        else:
            j += 1
            if j == len(actual_labels):
                i += 1
                j = 0

    score = lcs / max(len(canonical), 1)

    violations = []
    for idx, c in enumerate(canonical):
        if c not in actual_labels:
            violations.append({"section": c, "expected_position": idx + 1, "actual_position": None})

    # Detect out-of-order pairs
    order_violations = []
    for i in range(len(canonical) - 1):
        a, b = canonical[i], canonical[i + 1]
        if a in actual_labels and b in actual_labels:
            if actual_labels.index(a) > actual_labels.index(b):
                order_violations.append(f"{a} appears after {b} (expected before)")

    return {
        "order_compliance_score": round(score, 3),
        "canonical_order": canonical,
        "actual_order": actual_labels,
        "missing_sections": violations,
        "out_of_order_pairs": order_violations,
        "violations": violations + [{"section": v, "expected_position": None, "actual_position": None} for v in order_violations]
    }

def check_precision_terms(text: str, domain: str, primary_genre: str) -> dict:
    violations = []
    if not RAPIDFUZZ_AVAILABLE or primary_genre != "sop_qms":
        return {"controlled_vocabulary_coverage": 1.0, "precision_violations": violations}
        
    controlled_vocab = QMS_SUB_TYPE_BANKS.get(domain, DOMAIN_TERM_BANKS.get("it", []))
    words = set(re.findall(r'\b[a-z]{5,}\b', text.lower()))
    
    for word in words:
        if word in controlled_vocab: continue
        
        # Check against controlled vocab for near-matches
        for cv in controlled_vocab:
            if len(cv) < 5: continue
            score = _rfuzz.token_sort_ratio(word, cv)
            if 85 <= score < 100:
                violations.append({
                    "found_term": word,
                    "approved_term": cv,
                    "similarity_score": round(score, 1)
                })
                break
                
    coverage = 1.0 - (len(violations) / max(len(words), 1))
    return {
        "controlled_vocabulary_coverage": round(max(coverage, 0.0), 3),
        "precision_violations": violations[:20]
    }

def extract_bilingual_glossary(text: str, langs: list) -> dict:
    """Extract potential term equivalents in bilingual documents."""
    if len(langs) < 2: return {}
    
    # Heuristic: look for patterns like "Term (Ubersetzung)" or "Term / Ubersetzung"
    glossary = {}
    patterns = [
        r"\b([A-Z][a-z]+)\s+[\(/]\s*([A-Z][a-z]+)\s*[\)/]",
        r"([A-Z][a-z]+):\s*([A-Z][a-z]+)"
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for m1, m2 in matches:
            glossary[m1] = m2
            
    return glossary

def get_domain_seed_terms(domain: str) -> list:
    """Provide a list of domain-specific terms to seed the CREATE task."""
    return QMS_SUB_TYPE_BANKS.get(domain, DOMAIN_TERM_BANKS.get(domain, []))

def build_precision_blocklist(violations: list) -> str:
    """Convert precision violations into a blocklist string for the LLM."""
    if not violations: return ""
    block_lines = ["PRECISION BLOCKLIST (NEVER USE THE 'FOUND' ALTERNATIVE):"]
    for v in violations:
        block_lines.append(f"- Avoid: '{v['found_term']}' -> Use instead: '{v['approved_term']}'")
    return "\n".join(block_lines)

def _extract_sop_source_lines(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    purpose = ""
    scope = ""
    responsibilities = []
    procedure = []
    exceptions = []
    records = []
    references = []

    for line in lines:
        low = line.lower()
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip().lower()
            value = v.strip()
            if any(t in key for t in ["purpose", "zweck"]):
                purpose = value
            elif any(t in key for t in ["scope", "geltungsbereich"]):
                scope = value
            elif any(t in key for t in ["responsibil", "verantwort"]):
                responsibilities.append(value)
            elif any(t in key for t in ["reference", "referenz"]):
                references.append(value)
            elif any(t in key for t in ["record", "aufzeichnung", "log"]):
                records.append(value)
            elif any(t in key for t in ["deviation", "abweich", "exception", "ausnahme"]):
                exceptions.append(value)
            elif len(value.split()) >= 4:
                procedure.append(f"{k.strip()}: {value}")
        elif re.search(r"^\d+[\.\)]\s+", line):
            procedure.append(re.sub(r"^\d+[\.\)]\s*", "", line))
        elif re.search(r"\b(DEV|CAPA|AUD|DEC)-", line, re.I):
            exceptions.append(line)
        elif any(t in low for t in ["log", "record", "aufbewahrung", "retention"]):
            records.append(line)
        elif len(line.split()) >= 6 and len(procedure) < 12:
            procedure.append(line)

    all_traceability = []
    for line in lines:
        if re.search(r"\b(?:DEV|CAPA|AUD|DEC)-[A-Z]{2,}-\d{3,}\b", line, re.I):
            all_traceability.append(line)

    return {
        "purpose": purpose,
        "scope": scope,
        "responsibilities": responsibilities[:6],
        "procedure": procedure[:12],
        "exceptions": exceptions[:8],
        "traceability": all_traceability,
        "records": records[:6],
        "references": references[:6],
    }

def rewrite_sop_same_language(text: str) -> dict:
    analysis = process_document(text)
    sop_profile = analysis.get("sop_profile") or {}
    language = ((sop_profile.get("language_profile") or {}).get("detected_sop_language")) or _infer_sop_language(text, analysis["language"]["lang_code"])
    pack = SOP_LANGUAGE_PACKS.get(language, SOP_LANGUAGE_PACKS["en"])
    src = _extract_sop_source_lines(text)
    meta = sop_profile.get("document_metadata", {})

    title = meta.get("title") or (meta.get("sop_number") or "SOP")
    sop_id = meta.get("sop_number") or "SOP-UNSPECIFIED"
    headers = pack["section_headers"]
    mandatory = pack["phrases"]["mandatory"]

    if language == "de":
        rewritten_lines = [
            f"SOP ID: {sop_id}",
            f"{headers['title']}: {title}",
            "",
            f"{headers['purpose']}:",
            src["purpose"] or "Diese SOP beschreibt verbindliche Kontrollen fuer sicheren und regelkonformen Zugriff.",
            "",
            f"{headers['scope']}:",
            src["scope"] or "Diese SOP gilt fuer alle beteiligten Rollen, Systeme und externen Dienstleister.",
            "",
            f"{headers['responsibilities']}:",
        ]
        resp = src["responsibilities"] or [
            f"Die verantwortliche Rolle {mandatory} den Zugriff freigeben und pruefen.",
            f"Die durchfuehrende Rolle {mandatory} alle Zugriffsschritte dokumentieren.",
        ]
        rewritten_lines.extend([f"- {r}" for r in resp])
        rewritten_lines.extend(["", f"{headers['procedure']}:", ""])
        proc = src["procedure"] or [
            f"Zugriffsanforderung klassifizieren und Identitaet verifizieren.",
            f"Nur autorisierte Berechtigungen vergeben und jede Aktion dokumentieren.",
            f"Bei Abweichungen sofort eskalieren und CAPA einleiten.",
        ]
        rewritten_lines.extend([f"{i}. {p}" for i, p in enumerate(proc, 1)])
        traceability_lines = src["traceability"] or src["exceptions"] or ["Abweichungen sind als DEV-Eintrag zu dokumentieren und nachzuverfolgen."]
        rewritten_lines.extend(["", f"{headers['exceptions']}:", *traceability_lines, ""])
        rewritten_lines.extend([f"{headers['records']}:", *(src["records"] or ["Alle Logs und Freigaben sind gemaess Aufbewahrungsfrist zu archivieren."]), ""])
        rewritten_lines.extend([f"{headers['references']}:", *(src["references"] or ["Relevante Normen und interne Richtlinien sind verbindlich anzuwenden."])])
    else:
        rewritten_lines = [
            f"SOP ID: {sop_id}",
            f"{headers['title']}: {title}",
            "",
            f"{headers['purpose']}:",
            src["purpose"] or "This SOP defines mandatory controls for compliant and secure access handling.",
            "",
            f"{headers['scope']}:",
            src["scope"] or "This SOP applies to all involved roles, systems, and external providers.",
            "",
            f"{headers['responsibilities']}:",
        ]
        resp = src["responsibilities"] or [
            f"The responsible role {mandatory} approve and verify access.",
            f"The executing role {mandatory} document all access activities.",
        ]
        rewritten_lines.extend([f"- {r}" for r in resp])
        rewritten_lines.extend(["", f"{headers['procedure']}:", ""])
        proc = src["procedure"] or [
            "Classify the access request and verify identity.",
            "Grant only authorized permissions and document each action.",
            "Escalate deviations immediately and initiate CAPA tracking.",
        ]
        rewritten_lines.extend([f"{i}. {p}" for i, p in enumerate(proc, 1)])
        traceability_lines = src["traceability"] or src["exceptions"] or ["Deviations shall be logged as DEV records and reviewed."]
        rewritten_lines.extend(["", f"{headers['exceptions']}:", *traceability_lines, ""])
        rewritten_lines.extend([f"{headers['records']}:", *(src["records"] or ["All logs and approvals shall be retained according to policy."]), ""])
        rewritten_lines.extend([f"{headers['references']}:", *(src["references"] or ["Applicable standards and internal policies are mandatory."])])

    rewritten_text = "\n".join(rewritten_lines).strip() + "\n"
    rewritten_analysis = process_document(rewritten_text)
    rewritten_lang = (rewritten_analysis.get("sop_profile", {}).get("language_profile", {}).get("detected_sop_language")) or _infer_sop_language(rewritten_text, rewritten_analysis["language"]["lang_code"])

    return {
        "detected_language": language,
        "rewritten_language": rewritten_lang,
        "language_preserved": language == rewritten_lang,
        "rewritten_text": rewritten_text,
        "source_analysis": analysis,
        "rewritten_analysis": rewritten_analysis,
    }

def process_document(text: str):
    """
    Universal SOP Intelligence Engine — 9-Stage Pipeline Orchestrator.
    Stage 1 → Language Detection
    Stage 2 → Writing Style Fingerprinting
    Stage 3 → Tone Profile Classification
    Stage 4 → Domain Auto-Discovery
    Stage 5 → Adaptive Semantic Chunking
    Stage 6 → Deep Feature Extraction + Quality Metrics
    Stage 7 → Universal Symbol Table + Cross-Section Consistency
    Stage 8 → Compliance Intelligence + Section Order Validation
    Stage 9 → Structured Output + LLM Prompt Construction
    """

    # ── Stage 1: Language + Script Detection ──────────────────────────────────
    lang_info = detect_language(text)
    lang_code = lang_info["lang_code"]
    is_bilingual = lang_info.get("is_bilingual", False)

    # ── Stage 2: Writing Style Fingerprinting ─────────────────────────────────
    style_info = detect_writing_style(text)

    # ── Stage 3: Tone Profile Classification ──────────────────────────────────
    nlp = _get_spacy(lang_code)
    spacy_doc = nlp(text[:50000]) if nlp else None
    tone_info = classify_tone_profile(text, is_bilingual, spacy_doc)

    # ── Stage 4: Domain Auto-Discovery ────────────────────────────────────────
    domain_info = auto_discover_domain(text, lang_code=lang_code)
    primary_domain = domain_info["primary_domain"]

    # ── Stage 5: Adaptive Semantic Chunking ───────────────────────────────────
    chunks = adaptive_semantic_chunk(text, style_info)

    # ── Stage 6: Deep Feature Extraction + Domain Quality Metrics ─────────────
    features = extract_features(text, lang_info, v2_tone_info=tone_info, v2_domain_info=domain_info)
    classification = classify_genre(features, text)
    sop_profile = features.get("sop_intelligence") or {}

    quality_metrics = compute_domain_quality_metrics(text, chunks, tone_info, primary_domain)

    # ── Stage 7: Universal Symbol Table + Cross-Section Consistency ───────────
    symbol_table = build_universal_symbol_table(text, chunks, primary_domain)

    consistency = run_cross_section_consistency(
        text,
        sop_profile.get("section_structure", {}),
        sop_profile.get("traceability", {}),
        sop_profile.get("document_metadata", {})
    )

    roles = symbol_table["roles"] or sop_profile.get("roles_and_actors", {}).get("actors", [])
    steps = [s["text"] for s in sop_profile.get("workflow", {}).get("steps", [])]

    step_completeness = check_step_completeness(steps, lang_code)
    sla_register = extract_sla_register(text)
    control_coverage = build_control_coverage_matrix(roles, steps, sop_profile.get("traceability", {}))

    # ── Stage 8: Compliance Intelligence + Domain-Adaptive Section Order ───────
    primary_genre = classification.get("primary_genre", "general")
    qms_sub_type = classification.get("qms_sub_type", "")

    legal_boilerplate = {}
    if primary_genre == "legal":
        legal_boilerplate = check_legal_boilerplate(
            text, classification.get("legal_agreement_type") or "NDA"
        )

    section_order = validate_section_order(
        sop_profile.get("section_structure", {}).get("section_order", []),
        primary_genre,
        qms_sub_type,
        domain=primary_domain
    )

    precision_terms = check_precision_terms(text, qms_sub_type, primary_genre)
    precision_blocklist = build_precision_blocklist(precision_terms.get("precision_violations", []))

    bilingual_glossary = {}
    if is_bilingual:
        bilingual_glossary = extract_bilingual_glossary(text, [lang_code, "en"])

    domain_seeds = get_domain_seed_terms(primary_domain)

    # ── Stage 9: Build Audit Report + Structured Output ───────────────────────
    audit_findings = []
    for v in consistency.get("violations", []):
        detail = v.get("detail", str(v))
        audit_findings.append({"level": "major", "finding": detail})
    for g in control_coverage.get("gap_findings", []):
        audit_findings.append({"level": "major", "finding": g})
    for a in step_completeness.get("actorless_obligations", []):
        audit_findings.append({"level": "minor", "finding": f"Actorless obligation: {str(a)[:60]}..."})
    for v in section_order.get("missing_sections", []):
        audit_findings.append({"level": "minor", "finding": f"Missing section: {v['section']}"})
    for v in section_order.get("out_of_order_pairs", []):
        audit_findings.append({"level": "minor", "finding": f"Order violation: {v}"})
    if legal_boilerplate:
        for f in legal_boilerplate.get("flags", []):
            audit_findings.append({"level": f.get("level", "minor"), "finding": f"Missing legal clause: {f['clause']}"})

    audit_report = {
        "status": "passed" if not any(f["level"] in ["critical", "major"] for f in audit_findings) else "failed",
        "total_findings": len(audit_findings),
        "major_count": sum(1 for f in audit_findings if f["level"] == "major"),
        "minor_count": sum(1 for f in audit_findings if f["level"] == "minor"),
        "findings": audit_findings
    }

    # Enrich sop_profile with all stage outputs
    sop_profile["consistency"] = consistency
    sop_profile["step_completeness"] = step_completeness
    sop_profile["sla_register"] = sla_register
    sop_profile["control_coverage"] = control_coverage
    sop_profile["legal_boilerplate"] = legal_boilerplate
    sop_profile["section_order"] = section_order
    sop_profile["precision_terms"] = precision_terms
    sop_profile["audit_report"] = audit_report

    base_result = {
        # Stage outputs accessible at top level
        "language": lang_info,
        "writing_style": style_info,
        "tone_profile": tone_info,
        "domain": domain_info,
        "chunks": chunks,
        "quality_metrics": quality_metrics,
        "symbol_table": symbol_table,
        # Gap Closure data
        "precision_blocklist": precision_blocklist,
        "bilingual_glossary": bilingual_glossary,
        "domain_seeds": domain_seeds,
        # Legacy compatibility
        "features": features,
        "classification": classification,
        "sop_profile": sop_profile,
    }

    structured_profile = build_structured_sop_profile(base_result)
    llm_prompts = build_sop_llm_prompts(
        structured_profile,
        tasks=["rewrite", "improve", "create_new", "summarize", "gap_analysis", "translate"]
    )

    base_result["structured_sop_profile"] = structured_profile
    base_result["llm_prompt_rewrite"] = llm_prompts["rewrite"]["user_prompt"]
    base_result["llm_system_prompt_rewrite"] = llm_prompts["rewrite"]["system_prompt"]
    base_result["llm_prompts"] = llm_prompts
    
    import numpy as np
    def _clean_types(obj):
        if isinstance(obj, dict):
            return {k: _clean_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_clean_types(i) for i in obj]
        elif isinstance(obj, (np.float32, np.float64)):
            return float(obj)
        elif isinstance(obj, (np.int32, np.int64)):
            return int(obj)
        return obj

    return _clean_types(base_result)
