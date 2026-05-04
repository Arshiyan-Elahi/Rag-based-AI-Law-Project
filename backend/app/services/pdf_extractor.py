import pdfplumber
import re
import uuid
from io import BytesIO
from typing import List, Dict, Any, Tuple

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore

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


def _flatten_blocks_text(blocks: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for block in blocks:
        btype = str(block.get("type", "")).lower()
        if btype in {"section_heading", "heading", "paragraph", "note", "line"}:
            value = str(block.get("text", "")).strip()
            if value:
                parts.append(value)
        elif btype in {"bullet_list", "numbered_list", "list", "ordered_list"}:
            for item in block.get("items", []) or []:
                value = str(item).strip()
                if value:
                    parts.append(value)
        elif btype == "table":
            for row in block.get("rows", []) or []:
                for cell in row or []:
                    value = str(cell).strip()
                    if value:
                        parts.append(value)
    return "\n\n".join(parts).strip()


def _pypdf_full_text(pdf_bytes: bytes) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        chunks: List[str] = []
        for page in reader.pages:
            t = page.extract_text()
            if t and t.strip():
                chunks.append(t)
        return "\n\n".join(chunks).strip()
    except Exception:
        return ""


def extract_pdf_bytes_robust(pdf_bytes: bytes) -> Tuple[List[Dict[str, Any]], str]:
    """
    Prefer structured pdfplumber blocks; merge with pypdf plain text when pdfplumber output is sparse
    (common for odd PDF encodings or lightly malformed files).
    """
    blocks: List[Dict[str, Any]] = []
    plumber_text = ""

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_blocks = _extract_page_blocks(page)
                blocks.extend(page_blocks)
        plumber_text = _flatten_blocks_text(blocks)
    except Exception:
        blocks = []
        plumber_text = ""

    pypdf_text = _pypdf_full_text(pdf_bytes)
    wc_plumber = len(plumber_text.split())
    wc_pypdf = len(pypdf_text.split())

    if wc_pypdf > max(wc_plumber, 20) and wc_pypdf > wc_plumber * 1.15:
        # Rebuild light paragraph blocks from the richer pypdf extraction
        paras = [p.strip() for p in re.split(r"\n\s*\n", pypdf_text) if p.strip()]
        if not paras:
            lines = [ln.strip() for ln in pypdf_text.splitlines() if ln.strip()]
            paras = lines
        blocks = [{"type": "paragraph", "text": p} for p in paras] if paras else blocks
        return blocks, pypdf_text.strip()

    if plumber_text.strip():
        return blocks, plumber_text

    if pypdf_text.strip():
        paras = [p.strip() for p in re.split(r"\n\s*\n", pypdf_text) if p.strip()]
        blocks = [{"type": "paragraph", "text": p} for p in paras]
        return blocks, pypdf_text.strip()

    return blocks, ""


def _extract_page_blocks(page) -> List[Dict[str, Any]]:
    """Single-page structured extraction (shared layout logic with extract_structured_blocks)."""
    blocks: List[Dict[str, Any]] = []

    def flush_paragraph(lines: List[str]) -> None:
        text = _clean_line(" ".join(lines))
        if text:
            blocks.append({"type": "paragraph", "text": text})

    text = page.extract_text() or ""
    raw_lines = [_clean_line(line) for line in text.splitlines()]
    lines = [line for line in raw_lines if line]
    para_buffer: List[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        if _is_numbered_heading(line) or _is_likely_heading(line):
            flush_paragraph(para_buffer)
            para_buffer = []
            level = _to_heading_level(line)
            block_type = "section_heading" if level <= 2 else "heading"
            blocks.append({"type": block_type, "text": line, "level": level})
            i += 1
            continue

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


def extract_structured_blocks(file_path_or_obj) -> List[Dict[str, Any]]:
    """
    Extract structured SOP-like blocks from PDF:
    - section headings / headings
    - numbered/bullet lists
    - tables
    - paragraphs
    """
    if isinstance(file_path_or_obj, (bytes, bytearray)):
        try:
            blocks, _text = extract_pdf_bytes_robust(bytes(file_path_or_obj))
            return blocks
        except Exception:
            pass

    blocks: List[Dict[str, Any]] = []

    def flush_paragraph(lines: List[str]) -> None:
        text = _clean_line(" ".join(lines))
        if text:
            blocks.append({"type": "paragraph", "text": text})

    with pdfplumber.open(file_path_or_obj) as pdf:
        for page in pdf.pages:
            blocks.extend(_extract_page_blocks(page))

    return blocks
