import pdfplumber
import re
import uuid
<<<<<<< HEAD
from io import BytesIO
from typing import List, Dict, Any, Tuple

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore
=======
import os
import io
import logging
from typing import List, Dict, Any, Optional

try:
    import pytesseract
    from pdf2image import convert_from_bytes
    from PIL import Image
    HAS_OCR_DEPS = True
except ImportError:
    HAS_OCR_DEPS = False

logger = logging.getLogger(__name__)

# Configure Tesseract and Poppler paths for Windows environments
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
if TESSERACT_CMD and os.path.exists(TESSERACT_CMD):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

POPPLER_PATH = os.getenv("POPPLER_PATH")
if POPPLER_PATH and not os.path.exists(POPPLER_PATH):
    POPPLER_PATH = None

def check_ocr_setup() -> Dict[str, Any]:
    """
    Diagnostic tool to check OCR readiness.
    """
    status = {
        "pytesseract": HAS_OCR_DEPS,
        "tesseract_binary": False,
        "poppler_binaries": False,
        "tesseract_path": pytesseract.pytesseract.tesseract_cmd if HAS_OCR_DEPS else None,
        "poppler_path": POPPLER_PATH
    }
    
    if HAS_OCR_DEPS:
        try:
            pytesseract.get_tesseract_version()
            status["tesseract_binary"] = True
        except Exception:
            pass
            
    # Check poppler by trying to find pdftoppm in the path or configured path
    try:
        import subprocess
        pdftoppm = "pdftoppm"
        if POPPLER_PATH:
            pdftoppm = os.path.join(POPPLER_PATH, "pdftoppm.exe")
        
        subprocess.run([pdftoppm, "-v"], capture_output=True, check=False)
        status["poppler_binaries"] = True
    except Exception:
        pass
        
    return status

def _run_ocr_on_page(file_bytes: bytes, page_num: int) -> str:
    """
    Fallback OCR for scanned PDF pages.
    """
    if not HAS_OCR_DEPS:
        logger.warning("OCR dependencies (pytesseract, pdf2image) not installed.")
        return ""
    
    setup = check_ocr_setup()
    if not setup["tesseract_binary"]:
        logger.error(f"Tesseract binary not found at {setup['tesseract_path']}. OCR skipped.")
        return "[Error: Tesseract OCR binary not found. Please install Tesseract-OCR.]"
    
    if not setup["poppler_binaries"]:
        logger.error("Poppler binaries (pdftoppm) not found. OCR skipped.")
        return "[Error: Poppler binaries not found. Required for PDF to Image conversion.]"

    try:
        # Convert specific PDF page to image
        images = convert_from_bytes(
            file_bytes,
            first_page=page_num,
            last_page=page_num,
            poppler_path=POPPLER_PATH
        )
        if not images:
            return ""
        
        # Run Tesseract OCR on the image
        text = pytesseract.image_to_string(images[0])
        return text or ""
    except Exception as e:
        logger.error(f"OCR failed for page {page_num}: {e}")
        return ""

>>>>>>> c79857e1a6411c0dba3277d0d34266acf508094d

def extract_traceable_text(file_path_or_obj) -> List[Dict[str, Any]]:
    """
    Extracts text from a PDF with page and paragraph traceability.
    """
    results = []
    
    file_bytes = None
    if HAS_OCR_DEPS:
        if hasattr(file_path_or_obj, "read"):
            file_bytes = file_path_or_obj.read()
            file_path_or_obj.seek(0)
        elif isinstance(file_path_or_obj, (str, os.PathLike)):
            with open(file_path_or_obj, "rb") as f:
                file_bytes = f.read()

    with pdfplumber.open(file_path_or_obj) as pdf:
        for i, page in enumerate(pdf.pages):
            page_num = i + 1
            text = page.extract_text()
            
            # Fallback to OCR if no text found
            if not text or not text.strip():
                if file_bytes:
                    text = _run_ocr_on_page(file_bytes, page_num)
                
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

    file_bytes = None
    if HAS_OCR_DEPS:
        if hasattr(file_path_or_obj, "read"):
            file_bytes = file_path_or_obj.read()
            file_path_or_obj.seek(0)
        elif isinstance(file_path_or_obj, (str, os.PathLike)):
            with open(file_path_or_obj, "rb") as f:
                file_bytes = f.read()

    with pdfplumber.open(file_path_or_obj) as pdf:
<<<<<<< HEAD
        for page in pdf.pages:
            blocks.extend(_extract_page_blocks(page))
=======
        for p_idx, page in enumerate(pdf.pages):
            page_num = p_idx + 1
            text = page.extract_text() or ""
            
            # Fallback to OCR if no text found
            if not text.strip():
                if file_bytes:
                    text = _run_ocr_on_page(file_bytes, page_num)
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
>>>>>>> c79857e1a6411c0dba3277d0d34266acf508094d

    return blocks
