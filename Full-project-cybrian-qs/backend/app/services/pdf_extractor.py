import io
import logging
import os
import re
import uuid
import zipfile
from html import unescape
from io import BytesIO
from typing import Any, Callable, Dict, List, Tuple

import pdfplumber
from .sop_metadata_extractor import strip_invalid_control_chars

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore

try:
    import pytesseract
    from pdf2image import convert_from_bytes

    HAS_OCR_DEPS = True
except ImportError:
    pytesseract = None  # type: ignore
    convert_from_bytes = None  # type: ignore
    HAS_OCR_DEPS = False

logger = logging.getLogger(__name__)

TESSERACT_CMD = os.getenv("TESSERACT_CMD")
if HAS_OCR_DEPS and TESSERACT_CMD and os.path.exists(TESSERACT_CMD):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

POPPLER_PATH = os.getenv("POPPLER_PATH")
if POPPLER_PATH and not os.path.exists(POPPLER_PATH):
    POPPLER_PATH = None


def check_ocr_setup() -> Dict[str, Any]:
    status = {
        "pytesseract": HAS_OCR_DEPS,
        "tesseract_binary": False,
        "poppler_binaries": False,
        "tesseract_path": pytesseract.pytesseract.tesseract_cmd if HAS_OCR_DEPS else None,
        "poppler_path": POPPLER_PATH,
    }

    if HAS_OCR_DEPS:
        try:
            pytesseract.get_tesseract_version()
            status["tesseract_binary"] = True
        except Exception:
            pass

    try:
        import subprocess

        pdftoppm = os.path.join(POPPLER_PATH, "pdftoppm.exe") if POPPLER_PATH else "pdftoppm"
        subprocess.run([pdftoppm, "-v"], capture_output=True, check=False)
        status["poppler_binaries"] = True
    except Exception:
        pass

    return status


def _run_ocr_on_page(file_bytes: bytes, page_num: int) -> str:
    lines = _run_ocr_lines_on_page(file_bytes, page_num)
    return "\n".join(lines)


def _run_ocr_lines_on_page(file_bytes: bytes, page_num: int) -> List[str]:
    """OCR with line grouping (preserves list/paragraph breaks better than plain image_to_string)."""
    if not HAS_OCR_DEPS:
        logger.warning("OCR dependencies are not installed; scanned PDF page skipped.")
        return []

    setup = check_ocr_setup()
    if not setup["tesseract_binary"]:
        logger.error("Tesseract binary not found at %s; OCR skipped.", setup["tesseract_path"])
        return []
    if not setup["poppler_binaries"]:
        logger.error("Poppler binaries not found; OCR skipped.")
        return []

    try:
        images = convert_from_bytes(
            file_bytes,
            first_page=page_num,
            last_page=page_num,
            poppler_path=POPPLER_PATH,
        )
        if not images:
            return []
        config = "--psm 6 -c preserve_interword_spaces=1"
        data = pytesseract.image_to_data(images[0], config=config, output_type=pytesseract.Output.DICT)
        line_map: Dict[tuple[int, int], List[str]] = {}
        n = len(data.get("text") or [])
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            conf = int(float(data["conf"][i])) if str(data["conf"][i]).isdigit() or data["conf"][i] != "-1" else -1
            if conf >= 0 and conf < 35:
                continue
            key = (int(data["block_num"][i]), int(data["line_num"][i]))
            line_map.setdefault(key, []).append(txt)
        lines: List[str] = []
        for key in sorted(line_map.keys()):
            merged = _clean_line(" ".join(line_map[key]))
            if merged:
                lines.append(merged)
        if lines:
            return lines
        fallback = pytesseract.image_to_string(images[0], config=config) or ""
        return [_clean_line(ln) for ln in fallback.splitlines() if _clean_line(ln)]
    except Exception as exc:
        logger.exception("OCR failed for page %s: %s", page_num, exc)
        return []


def extract_traceable_text(file_path_or_obj) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    file_bytes = _read_pdf_source_bytes(file_path_or_obj)

    with pdfplumber.open(file_path_or_obj) as pdf:
        for i, page in enumerate(pdf.pages):
            page_num = i + 1
            text = page.extract_text() or ""
            if not text.strip() and file_bytes:
                text = _run_ocr_on_page(file_bytes, page_num)
            if not text:
                continue

            current_section = "Unknown"
            for idx, para in enumerate(re.split(r"\n\s*\n", text)):
                para = para.strip()
                if not para:
                    continue
                if _is_likely_heading(para):
                    current_section = para
                results.append(
                    {
                        "text": para,
                        "page": page_num,
                        "paragraph_index": idx,
                        "section": current_section,
                        "traceability_id": str(uuid.uuid4()),
                    }
                )

    return results


def _read_pdf_source_bytes(file_path_or_obj) -> bytes | None:
    try:
        if isinstance(file_path_or_obj, (bytes, bytearray)):
            return bytes(file_path_or_obj)
        if hasattr(file_path_or_obj, "read"):
            pos = file_path_or_obj.tell() if hasattr(file_path_or_obj, "tell") else None
            data = file_path_or_obj.read()
            if hasattr(file_path_or_obj, "seek") and pos is not None:
                file_path_or_obj.seek(pos)
            return data
        if isinstance(file_path_or_obj, (str, os.PathLike)):
            with open(file_path_or_obj, "rb") as f:
                return f.read()
    except Exception:
        return None
    return None


def _is_likely_heading(text: str) -> bool:
    if len(text) > 100:
        return False
    if re.match(r"^\d+[\)\.]\s+", text):
        return _is_numbered_section_heading(text)
    if re.match(r"^(\d+\.)+\s+[A-ZÄÖÜ]", text):
        return True
    if text.isupper() and len(text.split()) < 6:
        return True
    keywords = ["PURPOSE", "SCOPE", "RESPONSIBILITIES", "PROCEDURE", "REFERENCES", "HISTORY", "APPROVAL"]
    return any(k in text.upper() for k in keywords) and len(text.split()) < 4


def _clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", strip_invalid_control_chars(text or "")).strip()


def sanitize_extracted_text(text: str) -> str:
    """Sanitize OCR/PDF text before downstream metadata parsing and DB writes."""
    return strip_invalid_control_chars(text or "")


def _is_numbered_section_heading(line: str) -> bool:
    """Single-number lines like '1. Purpose' / '2. Scope' (not procedural steps)."""
    m = re.match(r"^(\d+(?:\.\d+)*)\.\s+(.+)$", line.strip())
    if not m:
        return False
    title = m.group(2).strip()
    words = title.split()
    if len(title) > 72 or len(words) > 7:
        return False
    upper = title.upper()
    section_keywords = (
        "PURPOSE", "SCOPE", "RESPONSIBILITIES", "PROCEDURE", "REFERENCES",
        "DEFINITIONS", "APPROVAL", "HISTORY", "ZWECK", "GELTUNG", "VERFAHREN",
        "DOKUMENTATION", "ANHANG", "PURPOSE AND", "SCOPE AND",
    )
    if any(k in upper for k in section_keywords):
        return True
    return title.isupper() and len(words) <= 5


def _is_numbered_heading(line: str) -> bool:
    if re.match(r"^\d+\.\d+", line):
        return bool(re.match(r"^\d+(?:\.\d+)+[\)\.]?\s+\S+", line))
    return _is_numbered_section_heading(line)


def _is_bullet_item(line: str) -> bool:
    return bool(re.match(r"^(?:[-*•]\s+).+", line))


def _is_numbered_item(line: str) -> bool:
    if not re.match(r"^\d+[\)\.]\s+.+", line):
        return False
    return not _is_numbered_section_heading(line)


def _is_key_value_line(line: str) -> bool:
    return bool(re.match(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s/&()\-]{1,40}:\s+\S+", line))


def _to_heading_level(line: str) -> int:
    m = re.match(r"^(\d+(?:\.\d+)*)", line)
    if not m:
        return 2
    depth = len(m.group(1).split("."))
    return max(1, min(3, depth + 1))


_TYPED_BLOCK_TYPES = {
    "section_heading",
    "heading",
    "two_column_row",
    "bullet_list",
    "numbered_list",
    "table",
}


def _typed_block_count(blocks: List[Dict[str, Any]]) -> int:
    return sum(1 for b in blocks if str(b.get("type", "")).lower() in _TYPED_BLOCK_TYPES)


def _flatten_blocks_text(blocks: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for block in blocks:
        btype = str(block.get("type", "")).lower()
        if btype in {"section_heading", "heading", "paragraph", "note", "line"}:
            value = str(block.get("text", "")).strip()
            if value:
                parts.append(value)
        elif btype in {"bullet_list", "numbered_list", "list", "ordered_list"}:
            parts.extend(str(item).strip() for item in block.get("items", []) or [] if str(item).strip())
        elif btype == "table":
            for row in block.get("rows", []) or []:
                cells = [str(cell).strip() for cell in row or [] if str(cell).strip()]
                if cells:
                    parts.append(" | ".join(cells))
    return sanitize_extracted_text("\n\n".join(parts).strip())


def _pypdf_full_text(pdf_bytes: bytes) -> str:
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        chunks: List[str] = []
        for page in reader.pages:
            text = page.extract_text()
            if text and text.strip():
                chunks.append(text)
        return sanitize_extracted_text("\n\n".join(chunks).strip())
    except Exception:
        return ""


def _extract_pdf_bytes_legacy(pdf_bytes: bytes) -> Tuple[List[Dict[str, Any]], str]:
    """pdfplumber + pypdf fallback pipeline (pre-Docling path)."""
    blocks: List[Dict[str, Any]] = []
    plumber_text = ""

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for idx, page in enumerate(pdf.pages):
                page_blocks = _extract_page_blocks(page, pdf_bytes=pdf_bytes, page_num=idx + 1)
                blocks.extend(page_blocks)
        plumber_text = _flatten_blocks_text(blocks)
    except Exception:
        logger.exception("pdfplumber extraction failed.")
        blocks = []
        plumber_text = ""

    pypdf_text = _pypdf_full_text(pdf_bytes)
    wc_plumber = len(plumber_text.split())
    wc_pypdf = len(pypdf_text.split())

    from .document_structure import refine_blocks, structure_blocks_from_text

    plumber_typed = _typed_block_count(blocks)
    if (
        wc_pypdf > max(wc_plumber, 20)
        and wc_pypdf > wc_plumber * 1.15
        and plumber_typed < 2
    ):
        final_text = sanitize_extracted_text(pypdf_text.strip())
        blocks = refine_blocks(structure_blocks_from_text(pypdf_text), final_text)
        return blocks, final_text

    if plumber_text.strip():
        final_text = sanitize_extracted_text(plumber_text.strip())
        blocks = refine_blocks(blocks, final_text)
        return blocks, final_text
    if pypdf_text.strip():
        final_text = sanitize_extracted_text(pypdf_text.strip())
        blocks = refine_blocks(structure_blocks_from_text(pypdf_text), final_text)
        return blocks, final_text
    return blocks, ""


def _pdf_has_native_text_layer(pdf_bytes: bytes, min_chars: int = 40) -> bool:
    """Probe first pages for selectable text (native vs scanned PDF routing for Marker OCR)."""
    if PdfReader is None:
        return False
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        total = 0
        for page in reader.pages[: min(3, len(reader.pages))]:
            total += len((page.extract_text() or "").strip())
            if total >= min_chars:
                return True
    except Exception:
        return False
    return False


def _pdf_is_scanned(pdf_bytes: bytes) -> bool:
    return not _pdf_has_native_text_layer(pdf_bytes)


def extract_pdf_bytes_robust(
    pdf_bytes: bytes,
    *,
    job_id: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_phase: Callable[[str, str], None] | None = None,
    use_local_marker: bool | None = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    PDF extraction for SOP import — local Marker PDF only (native and scanned).

    Flow: PDF → Marker markdown/JSON → marker_blocks → normalized blocks.
    No Docling, pdfplumber, pypdf, or Tesseract fallbacks for document import.
    """
    del use_local_marker  # always local Marker; kept for call-site compatibility

    from .local_marker_extractor import (
        LocalMarkerError,
        convert_pdf_bytes_local_marker,
        is_local_marker_available,
        is_local_marker_enabled,
    )

    scanned = _pdf_is_scanned(pdf_bytes)

    if not is_local_marker_enabled():
        raise LocalMarkerError(
            "Local Marker PDF is disabled (SOP_USE_LOCAL_MARKER=false). "
            "PDF import requires local Marker; set SOP_USE_LOCAL_MARKER=true."
        )
    if not is_local_marker_available():
        raise LocalMarkerError(
            "marker-pdf is not installed. Install with: pip install marker-pdf"
        )

    blocks, text, _result = convert_pdf_bytes_local_marker(
        pdf_bytes,
        "upload.pdf",
        scanned=scanned,
        job_id=job_id,
        on_progress=on_progress,
        on_phase=on_phase,
    )
    if not blocks and not (text or "").strip():
        raise LocalMarkerError("Local Marker PDF produced no extractable content.")
    return blocks, text


def _paragraph_blocks_from_text(text: str) -> List[Dict[str, Any]]:
    text = sanitize_extracted_text(text or "")
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if not paras:
        paras = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return [{"type": "paragraph", "text": p} for p in paras]


def _crop_page_text(page, top: float, bottom: float) -> str:
    """Extract layout-preserving text from a vertical slice of the page."""
    if bottom <= top:
        return ""
    try:
        cropped = page.crop((0, top, page.width, bottom))
        return (
            cropped.extract_text(
                layout=True,
                x_tolerance=2,
                y_tolerance=3,
                strip=False,
            )
            or ""
        )
    except Exception:
        return page.extract_text() or ""


def _table_rows_from_finder(table) -> List[List[str]]:
    from ..utils.table_blocks import normalize_table_rows

    rows: List[List[str]] = []
    try:
        extracted = table.extract() or []
    except Exception:
        extracted = []
    for row in extracted:
        normalized = [_clean_line(cell or "") for cell in row or []]
        if any(normalized):
            rows.append(normalized)
    return normalize_table_rows(rows)


def _extract_page_blocks(page, pdf_bytes: bytes | None = None, page_num: int = 1) -> List[Dict[str, Any]]:
    """
    Extract page content in reading order: text regions and tables interleaved by vertical position.
    """
    from .document_structure import structure_lines_to_blocks

    flattened_label_pattern = re.compile(
        r"\s+(?=(?:SOP\s*ID|ID|Titel|Title|Status|Version|Department|Bereich|"
        r"Effective\s*Date|Review\s*Date)\s*:)",
        re.IGNORECASE,
    )

    def _lines_to_blocks(raw_text: str) -> List[Dict[str, Any]]:
        normalized_text = flattened_label_pattern.sub("\n", raw_text or "")
        raw_lines = [_clean_line(ln) for ln in normalized_text.splitlines() if _clean_line(ln)]
        return structure_lines_to_blocks(raw_lines)

    table_finders: List[Any] = []
    try:
        table_finders = list(page.find_tables() or [])
    except Exception:
        table_finders = []
    table_finders.sort(key=lambda t: float(t.bbox[1]))

    has_native_text = bool((page.extract_text() or "").strip())

    # Scanned/image-only pages: one OCR pass, then append detected tables (avoids duplicate OCR).
    if not has_native_text and pdf_bytes:
        ocr_lines = _run_ocr_lines_on_page(pdf_bytes, page_num)
        blocks: List[Dict[str, Any]] = _lines_to_blocks("\n".join(ocr_lines)) if ocr_lines else []
        for table in table_finders:
            rows = _table_rows_from_finder(table)
            if rows:
                blocks.append({"type": "table", "rows": rows})
        if blocks:
            return blocks

    blocks = []
    page_top = float(page.bbox[1])
    page_bottom = float(page.bbox[3])
    cursor = page_top

    for table in table_finders:
        _x0, top, _x1, bottom = (float(v) for v in table.bbox)
        if top > cursor + 1:
            region_text = _crop_page_text(page, cursor, top)
            if region_text.strip():
                blocks.extend(_lines_to_blocks(region_text))
        rows = _table_rows_from_finder(table)
        if rows:
            blocks.append({"type": "table", "rows": rows})
        cursor = max(cursor, bottom)

    if cursor < page_bottom - 1:
        region_text = _crop_page_text(page, cursor, page_bottom)
        if region_text.strip():
            blocks.extend(_lines_to_blocks(region_text))

    if not blocks:
        text = page.extract_text(layout=True, x_tolerance=2, y_tolerance=3) or ""
        if not text.strip() and pdf_bytes:
            text = "\n".join(_run_ocr_lines_on_page(pdf_bytes, page_num))
        if text.strip():
            blocks = _lines_to_blocks(text)

    return blocks


def extract_structured_blocks(file_path_or_obj) -> List[Dict[str, Any]]:
    pdf_bytes = (
        bytes(file_path_or_obj)
        if isinstance(file_path_or_obj, (bytes, bytearray))
        else _read_pdf_source_bytes(file_path_or_obj)
    )
    if not pdf_bytes:
        raise ValueError("Could not read PDF bytes for structured extraction.")
    blocks, _text = extract_pdf_bytes_robust(pdf_bytes)
    return blocks


def extract_docx_bytes(docx_bytes: bytes) -> Tuple[List[Dict[str, Any]], str]:
    """Extract DOCX with structure preserved (headings, lists, tables, order)."""
    from .docx_extractor import extract_docx_bytes as extract_docx_structured

    return extract_docx_structured(docx_bytes)
