import pdfplumber
import re
import uuid
from typing import List, Dict, Any

def extract_traceable_text(file_path_or_obj) -> List[Dict[str, Any]]:
    """
    Extracts text from a PDF with page and paragraph traceability.
    """
    results = []
    
    with pdfplumber.open(file_path_or_obj) as pdf:
        for i, page in enumerate(pdf.pages):
            page_num = i + 1
            text = page.extract_text()
            if not text:
                continue
                
            # Split by double newlines to identify paragraphs
            # We also handle single newlines that might be part of the same paragraph
            paragraphs = re.split(r'\n\s*\n', text)
            
            current_section = "Unknown"
            
            for idx, para in enumerate(paragraphs):
                para = para.strip()
                if not para:
                    continue
                
                # Simple section detection: usually short lines, maybe starting with numbers
                # or all caps, followed by a newline (now the start of a paragraph)
                if _is_likely_heading(para):
                    current_section = para
                
                results.append({
                    "text": para,
                    "page": page_num,
                    "paragraph_index": idx,
                    "section": current_section,
                    "traceability_id": str(uuid.uuid4())
                })
                
    return results

def _is_likely_heading(text: str) -> bool:
    """
    Heuristic to detect if a paragraph is actually a section heading.
    """
    # Headings are usually short
    if len(text) > 100:
        return False
    
    # Common SOP heading patterns:
    # 1. Purpose, 2. Scope, 3. Responsibilities, etc.
    # 1.1, 1.2, 2.1.1
    if re.match(r'^(\d+\.)+\s+[A-Z]', text):
        return True
    
    # All caps short titles
    if text.isupper() and len(text.split()) < 6:
        return True
        
    # Just numeric prefixes like "1.", "2."
    if re.match(r'^\d+\.\s+[A-Z]', text):
        return True
        
    # Keywords
    keywords = ["PURPOSE", "SCOPE", "RESPONSIBILITIES", "PROCEDURE", "REFERENCES", "HISTORY", "APPROVAL"]
    if any(k in text.upper() for k in keywords) and len(text.split()) < 4:
        return True
        
    return False


def _clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_numbered_heading(line: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+)*[\)\.]?\s+[A-ZÄÖÜ]", line))


def _is_bullet_item(line: str) -> bool:
    return bool(re.match(r"^(?:[-*•]\s+).+", line))


def _is_numbered_item(line: str) -> bool:
    return bool(re.match(r"^\d+[\)\.]\s+.+", line))


def _to_heading_level(line: str) -> int:
    m = re.match(r"^(\d+(?:\.\d+)*)", line)
    if not m:
        return 2
    depth = len(m.group(1).split("."))
    # Keep heading levels inside TipTap-supported range.
    return max(1, min(3, depth + 1))


def extract_structured_blocks(file_path_or_obj) -> List[Dict[str, Any]]:
    """
    Extract structured SOP-like blocks from PDF:
    - section headings / headings
    - numbered/bullet lists
    - tables
    - paragraphs
    """
    blocks: List[Dict[str, Any]] = []

    def flush_paragraph(lines: List[str]) -> None:
        text = _clean_line(" ".join(lines))
        if text:
            blocks.append({"type": "paragraph", "text": text})

    with pdfplumber.open(file_path_or_obj) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            raw_lines = [_clean_line(line) for line in text.splitlines()]
            lines = [line for line in raw_lines if line]
            para_buffer: List[str] = []

            i = 0
            while i < len(lines):
                line = lines[i]

                # Headings / section headers
                if _is_numbered_heading(line) or _is_likely_heading(line):
                    flush_paragraph(para_buffer)
                    para_buffer = []
                    level = _to_heading_level(line)
                    block_type = "section_heading" if level <= 2 else "heading"
                    blocks.append({"type": block_type, "text": line, "level": level})
                    i += 1
                    continue

                # Bullet list
                if _is_bullet_item(line):
                    flush_paragraph(para_buffer)
                    para_buffer = []
                    items: List[str] = []
                    while i < len(lines) and _is_bullet_item(lines[i]):
                        items.append(_clean_line(re.sub(r"^[-*•]\s+", "", lines[i])))
                        i += 1
                    if items:
                        blocks.append({"type": "bullet_list", "items": items})
                    continue

                # Numbered list
                if _is_numbered_item(line):
                    flush_paragraph(para_buffer)
                    para_buffer = []
                    items = []
                    while i < len(lines) and _is_numbered_item(lines[i]):
                        items.append(_clean_line(re.sub(r"^\d+[\)\.]\s+", "", lines[i])))
                        i += 1
                    if items:
                        blocks.append({"type": "numbered_list", "items": items})
                    continue

                para_buffer.append(line)
                i += 1

            flush_paragraph(para_buffer)

            # Extract tables with basic normalization.
            tables = page.extract_tables() or []
            for table in tables:
                rows = []
                for row in table or []:
                    normalized = [_clean_line(cell or "") for cell in (row or [])]
                    if any(normalized):
                        rows.append(normalized)
                if rows:
                    blocks.append({"type": "table", "rows": rows})

    return blocks
