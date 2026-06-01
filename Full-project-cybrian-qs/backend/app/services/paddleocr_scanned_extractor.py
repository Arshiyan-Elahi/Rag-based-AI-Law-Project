"""
Scanned PDF extraction via PyMuPDF page rendering + PaddleOCR PPStructure.

Produces unified sequential elements for the SOP editor pipeline.
Falls back to legacy fitz/tesseract OCR when Paddle is disabled or unavailable.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from html.parser import HTMLParser
from typing import Any, Dict, List, Tuple

from .pdf_extractor import (
    _clean_line,
    _is_bullet_item,
    _is_key_value_line,
    _is_likely_heading,
)
from .sop_metadata_extractor import strip_invalid_control_chars

logger = logging.getLogger(__name__)

# SOP / quality-record section titles (DE + EN) detected in scanned OCR output
_PADDLE_SECTION_HEADING_RE = re.compile(
    r"^(?:"
    r"Zweck|Geltungsbereich|Geltung|Verantwortlichkeiten|Verfahren|Dokumentation|"
    r"Anhang|Historie|Freigabe|Genehmigung|Änderungshistorie|"
    r"DEVIATIONS?|CAPAs?|AUDIT\s+FINDINGS?|DECISIONS?|"
    r"PURPOSE|SCOPE|RESPONSIBILITIES|PROCEDURE|REFERENCES|APPROVAL|HISTORY"
    r")(?:\s*[:.\-–—]?\s*.*)?$",
    re.IGNORECASE,
)
# DEV/CAPA/AUD/DEC tracking items — each line becomes its own block
_PADDLE_TRACKING_ITEM_RE = re.compile(
    r"^(?:DEV|CAPA|AUD|DEC)[-_]?\d{1,8}\b",
    re.IGNORECASE,
)
# Split merged OCR blobs when PPStructure returns one long string
_ITEM_BOUNDARY_RE = re.compile(
    r"(?<=\S)\s+(?=(?:DEV|CAPA|AUD|DEC)[-_]?\d{1,8}\b)",
    re.IGNORECASE,
)
_SECTION_BOUNDARY_RE = re.compile(
    r"(?<=\S)\s+(?="
    r"(?:Zweck|Geltungsbereich|DEVIATIONS?|CAPAs?|AUDIT\s+FINDINGS?|DECISIONS?|"
    r"PURPOSE|SCOPE|RESPONSIBILITIES|PROCEDURE|REFERENCES|APPROVAL)\b"
    r")",
    re.IGNORECASE,
)
# Vertical gap (px) between OCR boxes treated as a paragraph break when grouping
_PARAGRAPH_GAP_PX = float(os.getenv("SOP_PADDLE_PARAGRAPH_GAP_PX", "14") or "14")

EXTRACTION_ENGINE_PADDLE = "paddleocr_ppstructure"

_PADDLE_ENGINE: Any = None
_PADDLE_LOCK = threading.Lock()

_HEADING_TYPES = frozenset(
    {
        "title",
        "paragraph_title",
        "doc_title",
        "document_title",
        "figure_title",
        "table_title",
        "caption",
        "section",
        "heading",
    }
)
_TEXT_TYPES = frozenset({"text", "paragraph", "abstract", "reference", "equation"})
_LIST_TYPES = frozenset({"list", "list_item"})
_TABLE_TYPES = frozenset({"table"})
_SKIP_TYPES = frozenset({"figure", "image", "header", "footer", "page_number", "seal"})


def is_paddle_ocr_enabled() -> bool:
    return os.getenv("SOP_PADDLE_OCR_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_paddle_ocr_available() -> bool:
    if not is_paddle_ocr_enabled():
        return False
    try:
        from paddleocr import PPStructure  # noqa: F401

        return True
    except Exception:
        return False


def check_paddle_ocr_setup() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "enabled": is_paddle_ocr_enabled(),
        "available": False,
        "lang": os.getenv("SOP_PADDLE_OCR_LANG", "en"),
        "max_pages": int(os.getenv("SOP_PADDLE_OCR_MAX_PAGES", "0") or "0"),
        "render_dpi": int(os.getenv("SOP_PADDLE_OCR_RENDER_DPI", "150") or "150"),
    }
    if is_paddle_ocr_available():
        out["available"] = True
    return out


def _skip_header_footer() -> bool:
    return os.getenv("SOP_PADDLE_SKIP_HEADER_FOOTER", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _max_pages() -> int:
    try:
        return max(0, int(os.getenv("SOP_PADDLE_OCR_MAX_PAGES", "0") or "0"))
    except ValueError:
        return 0


def _render_dpi() -> int:
    try:
        return max(72, min(300, int(os.getenv("SOP_PADDLE_OCR_RENDER_DPI", "150") or "150")))
    except ValueError:
        return 150


def render_pdf_pages_rgb(file_bytes: bytes) -> List[Tuple[int, Any]]:
    """Render PDF pages to RGB numpy arrays using PyMuPDF (fitz)."""
    import fitz  # type: ignore
    import numpy as np

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    zoom = _render_dpi() / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pages: List[Tuple[int, Any]] = []
    limit = _max_pages()
    try:
        for idx, page in enumerate(doc):
            if limit and idx >= limit:
                break
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:
                arr = arr[:, :, :3]
            pages.append((idx + 1, arr))
    finally:
        doc.close()
    return pages


def _get_ppstructure_engine() -> Any:
    global _PADDLE_ENGINE
    with _PADDLE_LOCK:
        if _PADDLE_ENGINE is not None:
            return _PADDLE_ENGINE
        from paddleocr import PPStructure

        lang = os.getenv("SOP_PADDLE_OCR_LANG", "en").strip() or "en"
        show_log = os.getenv("SOP_PADDLE_OCR_VERBOSE", "false").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        _PADDLE_ENGINE = PPStructure(
            show_log=show_log,
            lang=lang,
            layout=True,
            table=True,
            ocr=True,
            recovery=False,
        )
        logger.info("[paddle-ocr] PPStructure engine initialized lang=%s", lang)
        return _PADDLE_ENGINE


def _rgb_to_bgr(image: Any) -> Any:
    import cv2  # type: ignore

    if image is None:
        return image
    if len(getattr(image, "shape", ())) == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    return image


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self._current_row: List[str] = []
        self._cell_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            text = _clean_line("".join(self._cell_parts))
            self._current_row.append(text)
            self._cell_parts = []
        elif tag == "tr" and self._current_row:
            if any(c.strip() for c in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = []

    def handle_data(self, data: str) -> None:
        self._cell_parts.append(data)


def html_table_to_rows(html: str) -> List[List[str]]:
    if not html or not str(html).strip():
        return []
    parser = _TableHTMLParser()
    try:
        parser.feed(str(html))
        parser.close()
    except Exception:
        return []
    return [row for row in parser.rows if any(cell.strip() for cell in row)]


def _clean_ocr_line(text: str) -> str:
    """Normalize OCR text without collapsing line breaks (horizontal space only)."""
    if not text:
        return ""
    raw = strip_invalid_control_chars(str(text)).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", ln).strip() for ln in raw.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def _paddle_is_section_heading(text: str) -> bool:
    line = _clean_ocr_line(text)
    if not line or len(line) > 120:
        return False
    if _PADDLE_SECTION_HEADING_RE.match(line):
        return True
    if line.isupper() and len(line.split()) <= 6 and len(line) < 80:
        return True
    return False


def _paddle_is_tracking_item(text: str) -> bool:
    return bool(_PADDLE_TRACKING_ITEM_RE.match(_clean_ocr_line(text)))


def _bbox_from_points(box: Any) -> Tuple[float, float, float, float]:
    """Accept [x0,y0,x1,y1] or quad [[x,y],...]."""
    if not box:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        if isinstance(box, (list, tuple)) and len(box) == 4 and all(
            isinstance(v, (int, float)) for v in box
        ):
            x0, y0, x1, y1 = [float(v) for v in box]
            return (x0, y0, x1, y1)
        if isinstance(box, (list, tuple)) and box and isinstance(box[0], (list, tuple)):
            xs = [float(p[0]) for p in box if len(p) >= 2]
            ys = [float(p[1]) for p in box if len(p) >= 2]
            if xs and ys:
                return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, ValueError):
        pass
    return (0.0, 0.0, 0.0, 0.0)


def _text_from_ocr_item(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return _clean_ocr_line(item)
    if isinstance(item, dict):
        if item.get("html"):
            return ""
        for key in ("text", "rec_text", "content"):
            val = item.get(key)
            if val:
                return _clean_ocr_line(str(val))
        return ""
    if isinstance(item, (list, tuple)):
        if len(item) >= 2 and isinstance(item[1], (list, tuple)) and item[1]:
            return _clean_ocr_line(str(item[1][0]))
        if len(item) >= 2 and isinstance(item[1], str):
            return _clean_ocr_line(item[1])
        if item and isinstance(item[0], str) and not isinstance(item[0], (list, tuple)):
            return _clean_ocr_line(str(item[0]))
    return _clean_ocr_line(str(item))


def _bbox_from_ocr_item(item: Any, fallback: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    if isinstance(item, dict):
        for key in ("text_region", "bbox", "box", "points"):
            if item.get(key):
                bb = _bbox_from_points(item[key])
                if bb != (0.0, 0.0, 0.0, 0.0):
                    return bb
    if isinstance(item, (list, tuple)) and item:
        first = item[0]
        if isinstance(first, (list, tuple)):
            bb = _bbox_from_points(first)
            if bb != (0.0, 0.0, 0.0, 0.0):
                return bb
        bb = _bbox_from_points(item)
        if bb != (0.0, 0.0, 0.0, 0.0):
            return bb
    return fallback


def _split_merged_ocr_blob(text: str) -> List[str]:
    """Recover line structure when PPStructure merges multiple lines into one string."""
    cleaned = _clean_ocr_line(text)
    if not cleaned:
        return []
    if "\n" in cleaned:
        return [ln for ln in (_clean_ocr_line(x) for x in cleaned.split("\n")) if ln]

    # Long single-line blobs: split on tracking items and known section titles
    parts: List[str] = [cleaned]
    for splitter in (_ITEM_BOUNDARY_RE, _SECTION_BOUNDARY_RE):
        next_parts: List[str] = []
        for part in parts:
            next_parts.extend(p.strip() for p in splitter.split(part) if p.strip())
        parts = next_parts

    if len(parts) == 1 and len(cleaned) > 100:
        # Last resort: break on ". " before uppercase tokens (common OCR run-on)
        chunks = re.split(r"(?<=\.)\s+(?=[A-ZÄÖÜ])", cleaned)
        if len(chunks) > 1:
            return [_clean_ocr_line(c) for c in chunks if _clean_ocr_line(c)]
    return [_clean_ocr_line(p) for p in parts if _clean_ocr_line(p)]


def _ocr_lines_from_res(
    res: Any,
    region_bbox: Tuple[float, float, float, float],
) -> List[Tuple[str, float, float]]:
    """
    Extract OCR lines with vertical position for reading order.
    Returns [(text, y_center, x0), ...].
    """
    rx0, ry0, rx1, ry1 = region_bbox
    region_h = max(1.0, ry1 - ry0)
    line_step = region_h / 40.0
    entries: List[Tuple[str, float, float, float]] = []

    def add_line(text: str, bbox: Tuple[float, float, float, float], order: int) -> None:
        t = _clean_ocr_line(text)
        if not t:
            return
        for sub in t.split("\n") if "\n" in t else [t]:
            for piece in _split_merged_ocr_blob(sub) if len(sub) > 100 and "\n" not in sub else [sub]:
                ln = _clean_ocr_line(piece)
                if not ln:
                    continue
                x0, y0, x1, y1 = bbox
                y_center = (y0 + y1) / 2.0
                entries.append((ln, y_center, x0, order))

    if res is None:
        return []

    if isinstance(res, str):
        for i, piece in enumerate(_split_merged_ocr_blob(res)):
            add_line(piece, region_bbox, i)
        return [(t, y, x) for t, y, x, _ in sorted(entries, key=lambda e: (e[1], e[2], e[3]))]

    if isinstance(res, dict):
        if res.get("html"):
            return []
        text = _text_from_ocr_item(res)
        if text:
            for i, piece in enumerate(_split_merged_ocr_blob(text)):
                add_line(piece, region_bbox, i)
        return [(t, y, x) for t, y, x, _ in sorted(entries, key=lambda e: (e[1], e[2], e[3]))]

    if isinstance(res, list):
        for idx, item in enumerate(res):
            if isinstance(item, list) and item and isinstance(item[0], list):
                rows = _region_table_rows(res)
                if rows:
                    return []
            text = _text_from_ocr_item(item)
            if not text:
                continue
            bb = _bbox_from_ocr_item(item, region_bbox)
            y_off = ry0 + idx * line_step
            if bb == (0.0, 0.0, 0.0, 0.0) or bb == region_bbox:
                bb = (rx0, y_off, rx1, y_off + line_step)
            add_line(text, bb, idx)
        if entries:
            return [(t, y, x) for t, y, x, _ in sorted(entries, key=lambda e: (e[1], e[2], e[3]))]
        return []

    text = _clean_ocr_line(str(res))
    if text:
        for i, piece in enumerate(_split_merged_ocr_blob(text)):
            add_line(piece, region_bbox, i)
    return [(t, y, x) for t, y, x, _ in sorted(entries, key=lambda e: (e[1], e[2], e[3]))]


def _classify_ocr_line(line: str, *, region_is_heading: bool = False) -> str:
    if region_is_heading or _paddle_is_section_heading(line) or _is_likely_heading(line):
        return "heading"
    return "paragraph"


def _lines_to_text_elements(
    lines: List[Tuple[str, float, float]],
    *,
    region_is_heading: bool = False,
) -> List[Dict[str, Any]]:
    """Map positioned OCR lines to sequential text elements (one block per line)."""
    if not lines:
        return []

    elements: List[Dict[str, Any]] = []
    prev_y: float | None = None

    for text, y_center, _x0 in lines:
        if prev_y is not None and (y_center - prev_y) > _PARAGRAPH_GAP_PX * 2:
            # Preserve paragraph spacing as an empty paragraph marker (skipped by downstream)
            pass
        style = _classify_ocr_line(text, region_is_heading=region_is_heading)
        if _is_bullet_item(text) or _paddle_is_tracking_item(text) or _is_key_value_line(text):
            style = "paragraph"
        elements.append({"type": "text", "style": style, "content": text})
        prev_y = y_center

    return elements


def ocr_text_to_elements(raw_text: str) -> List[Dict[str, Any]]:
    """
    Structure raw OCR page text (legacy fitz/tesseract path) into sequential
    elements WITHOUT flattening: every OCR line becomes its own text element,
    line breaks are preserved, and SOP/quality section titles + DEV/CAPA/AUD/DEC
    tracking items are detected.

    This is the structuring layer shared by the PaddleOCR path and the legacy
    OCR fallback so both produce identical structured output.

    Returns: [{"type": "text", "style": "heading"|"paragraph", "content": str}, ...]
    """
    if not raw_text:
        return []

    normalized = (
        strip_invalid_control_chars(str(raw_text))
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    elements: List[Dict[str, Any]] = []
    for raw_line in normalized.split("\n"):
        # Collapse only horizontal whitespace; the newline split already
        # preserved the OCR line structure.
        line = re.sub(r"[^\S\n]+", " ", raw_line).strip()
        if not line:
            continue
        # A single OCR "line" may still be a run-on blob (no spaces between
        # records); recover individual lines/items before classifying.
        for piece in _split_merged_ocr_blob(line) if len(line) > 100 else [line]:
            content = _clean_ocr_line(piece)
            if not content:
                continue
            if (
                _is_bullet_item(content)
                or _paddle_is_tracking_item(content)
                or _is_key_value_line(content)
            ):
                style = "paragraph"
            elif _paddle_is_section_heading(content) or _is_likely_heading(content):
                style = "heading"
            else:
                style = "paragraph"
            elements.append({"type": "text", "style": style, "content": content})

    return elements


def _region_bbox(region: Dict[str, Any]) -> Tuple[float, float, float, float]:
    bbox = region.get("bbox") or region.get("box") or [0, 0, 0, 0]
    try:
        x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)
    return (x0, y0, x1, y1)


def _region_text_from_res(res: Any) -> str:
    if res is None:
        return ""
    if isinstance(res, str):
        return _clean_line(res)
    if isinstance(res, dict):
        if res.get("html"):
            return ""
        for key in ("text", "rec_text", "content"):
            val = res.get(key)
            if val:
                return _clean_line(str(val))
        return ""
    if not isinstance(res, list):
        return _clean_line(str(res))

    lines: List[str] = []
    for item in res:
        if isinstance(item, dict):
            t = item.get("text") or item.get("rec_text") or ""
            if t:
                lines.append(_clean_line(str(t)))
        elif isinstance(item, (list, tuple)):
            if len(item) >= 2 and isinstance(item[1], (list, tuple)) and item[1]:
                lines.append(_clean_line(str(item[1][0])))
            elif len(item) >= 2 and isinstance(item[1], str):
                lines.append(_clean_line(item[1]))
            elif item and isinstance(item[0], str):
                lines.append(_clean_line(item[0]))
    return "\n".join([ln for ln in lines if ln])


def _region_table_rows(res: Any) -> List[List[str]]:
    if isinstance(res, dict):
        html = res.get("html") or ""
        rows = html_table_to_rows(str(html))
        if rows:
            return rows
        cell_text = res.get("cell_bbox") or res.get("cells")
        if isinstance(cell_text, list):
            flat: List[str] = []
            for cell in cell_text:
                if isinstance(cell, dict):
                    t = cell.get("text") or ""
                    if t:
                        flat.append(_clean_line(str(t)))
            if flat:
                return [flat]
    if isinstance(res, list) and res and isinstance(res[0], list):
        out: List[List[str]] = []
        for row in res:
            if isinstance(row, list):
                cells = [_clean_line(str(c)) for c in row]
                if any(cells):
                    out.append(cells)
        if out:
            return out
    return []


def _normalize_region_type(raw: str) -> str:
    t = (raw or "text").strip().lower().replace(" ", "_")
    if t in _TABLE_TYPES:
        return "table"
    if t in _LIST_TYPES:
        return "list"
    if t in _HEADING_TYPES:
        return "heading"
    if t in _TEXT_TYPES:
        return "text"
    return t


def ppstructure_regions_to_elements(
    regions: List[Dict[str, Any]],
    *,
    page_num: int = 1,
) -> List[Dict[str, Any]]:
    """Convert PPStructure page regions into sequential structured elements."""
    skip_hf = _skip_header_footer()
    sortable: List[Tuple[float, float, Dict[str, Any]]] = []
    for region in regions or []:
        if not isinstance(region, dict):
            continue
        raw_type = str(region.get("type") or "text")
        norm = _normalize_region_type(raw_type)
        if skip_hf and norm in _SKIP_TYPES:
            continue
        if norm in _SKIP_TYPES and norm not in _TABLE_TYPES:
            continue
        x0, y0, _, _ = _region_bbox(region)
        sortable.append((y0, x0, region))

    sortable.sort(key=lambda item: (item[0], item[1]))
    elements: List[Dict[str, Any]] = []

    for _, __, region in sortable:
        raw_type = str(region.get("type") or "text")
        norm = _normalize_region_type(raw_type)
        res = region.get("res")
        region_bbox = _region_bbox(region)

        if norm == "table":
            rows = _region_table_rows(res)
            if rows:
                elements.append({"type": "table", "content": rows})
            continue

        region_is_heading = norm == "heading"
        ocr_lines = _ocr_lines_from_res(res, region_bbox)

        if not ocr_lines:
            text = _region_text_from_res(res)
            if not text:
                continue
            ocr_lines = [(ln, region_bbox[1], region_bbox[0]) for ln in _split_merged_ocr_blob(text)]

        if norm == "list":
            for text, y_center, x0 in ocr_lines:
                for line in text.splitlines():
                    ln = _clean_ocr_line(line)
                    if ln:
                        elements.append(
                            {
                                "type": "text",
                                "style": "paragraph",
                                "content": ln,
                            }
                        )
            continue

        elements.extend(
            _lines_to_text_elements(
                ocr_lines,
                region_is_heading=region_is_heading,
            )
        )

    if not elements and regions:
        logger.debug(
            "[paddle-ocr] page %s produced no elements from %s regions",
            page_num,
            len(regions),
        )
    return elements


def _run_ppstructure_on_pages(
    pages: List[Tuple[int, Any]],
) -> List[Dict[str, Any]]:
    engine = _get_ppstructure_engine()
    all_elements: List[Dict[str, Any]] = []
    for page_num, rgb in pages:
        try:
            bgr = _rgb_to_bgr(rgb)
            page_result = engine(bgr)
        except Exception as exc:
            logger.warning(
                "[paddle-ocr] PPStructure failed on page %s: %s", page_num, exc
            )
            continue
        if not page_result:
            continue
        regions = page_result
        if isinstance(page_result, dict):
            regions = (
                page_result.get("layout")
                or page_result.get("regions")
                or [page_result]
            )
        if not isinstance(regions, list):
            regions = [regions] if regions else []
        if logger.isEnabledFor(logging.DEBUG):
            try:
                region_types = [str(r.get("type")) for r in regions if isinstance(r, dict)]
            except Exception:
                region_types = []
            logger.debug(
                "[paddle-ocr] page %s raw PPStructure regions=%s types=%s",
                page_num,
                len(regions),
                region_types,
            )
        page_elements = ppstructure_regions_to_elements(regions, page_num=page_num)
        logger.info(
            "[paddle-ocr] page %s regions=%s -> elements=%s",
            page_num,
            len(regions),
            len(page_elements),
        )
        if logger.isEnabledFor(logging.DEBUG):
            for i, el in enumerate(page_elements[:50]):
                logger.debug(
                    "[paddle-ocr] page %s el[%s] type=%s style=%s content=%r",
                    page_num,
                    i,
                    el.get("type"),
                    el.get("style"),
                    str(el.get("content"))[:120],
                )
        all_elements.extend(page_elements)
    return all_elements


def extract_scanned_pdf_paddle(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Primary scanned PDF path: fitz render → PPStructure → sequential elements.
    Raises on hard failures so callers can fall back to legacy OCR.
    """
    if not is_paddle_ocr_available():
        raise RuntimeError("PaddleOCR/PPStructure is not available")

    pages = render_pdf_pages_rgb(file_bytes)
    if not pages:
        raise ValueError("PDF has no pages to render")

    logger.info("[paddle-ocr] rendering complete pages=%s dpi=%s", len(pages), _render_dpi())
    elements = _run_ppstructure_on_pages(pages)
    if not elements:
        raise ValueError("PaddleOCR PPStructure produced no extractable elements")
    logger.info("[paddle-ocr] extracted elements=%s", len(elements))
    return elements
