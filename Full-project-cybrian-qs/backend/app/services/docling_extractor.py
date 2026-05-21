"""
PDF extraction via Docling (layout + OCR), mapped to SOP import block format.

Native PDFs: prefer embedded text (force_backend_text) with table structure.
Scanned PDFs: full Docling OCR pipeline (do_ocr).
Falls back to pdfplumber/pypdf when Docling is disabled, times out, or yields poor output.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from io import BytesIO
from typing import Any, Dict, List, Tuple

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling_core.types.doc.document import (
    ListItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)
from docling_core.types.doc.labels import DocItemLabel
from docling_core.types.io import DocumentStream

from .pdf_extractor import _clean_line, _flatten_blocks_text, _typed_block_count, sanitize_extracted_text

logger = logging.getLogger(__name__)

_CONVERTER_CACHE: Dict[str, DocumentConverter] = {}

_MIN_TEXT_CHARS = int(os.getenv("SOP_DOCLING_MIN_TEXT_CHARS", "24"))
_MIN_TYPED_BLOCKS = int(os.getenv("SOP_DOCLING_MIN_TYPED_BLOCKS", "1"))
_DEFAULT_TIMEOUT_SEC = float(os.getenv("DOCLING_PDF_TIMEOUT_SEC", "180"))


def _docling_enabled() -> bool:
    return os.getenv("SOP_DOCLING_PDF_ENABLED", "true").lower() != "false"


def _pdf_has_native_text_layer(pdf_bytes: bytes, min_chars: int = 40) -> bool:
    """Fast probe: enough selectable text on first pages => native (non-scanned) PDF."""
    try:
        import pdfplumber
    except ImportError:
        return False

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            total = 0
            for page in pdf.pages[: min(3, len(pdf.pages))]:
                total += len((page.extract_text() or "").strip())
                if total >= min_chars:
                    return True
    except Exception:
        return False
    return False


def _get_converter(*, scanned: bool) -> DocumentConverter:
    key = "scanned" if scanned else "native"
    cached = _CONVERTER_CACHE.get(key)
    if cached is not None:
        return cached
    cached = _build_converter(scanned=scanned)
    _CONVERTER_CACHE[key] = cached
    return cached


def _build_converter(*, scanned: bool) -> DocumentConverter:
    """
    scanned=False: native PDF — use PDF text layer when available.
    scanned=True: image-only PDF — rely on Docling OCR + layout models.
    """
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        force_backend_text=not scanned,
        document_timeout=_DEFAULT_TIMEOUT_SEC if _DEFAULT_TIMEOUT_SEC > 0 else None,
        generate_page_images=scanned,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


def _table_block(table: TableItem) -> Dict[str, Any] | None:
    from ..utils.table_blocks import table_block_from_rows

    rows: List[List[str]] = []
    try:
        grid = table.data.grid
    except Exception:
        return None
    for row in grid:
        cells = [_clean_line(getattr(cell, "text", "") or "") for cell in row]
        if any(cells):
            rows.append(cells)
    header_rows = None
    try:
        if hasattr(table, "num_header_rows"):
            header_rows = int(table.num_header_rows)
    except Exception:
        header_rows = None
    return table_block_from_rows(rows, header_rows=header_rows)


def _text_from_item(item: Any) -> str:
    return _clean_line(getattr(item, "text", "") or "")


def _heading_block(item: Any, level_hint: int) -> Dict[str, Any] | None:
    text = _text_from_item(item)
    if not text:
        return None
    level = level_hint
    if isinstance(item, SectionHeaderItem):
        try:
            level = max(1, min(6, int(item.level)))
        except Exception:
            level = level_hint
    block_type = "section_heading" if level <= 2 else "heading"
    return {"type": block_type, "text": text, "level": min(3, level)}


def _flush_list(
    buffer: List[Dict[str, Any]],
    blocks: List[Dict[str, Any]],
) -> None:
    if not buffer:
        return
    numbered = sum(1 for x in buffer if x.get("enumerated"))
    list_type = "numbered_list" if numbered >= len(buffer) / 2 else "bullet_list"
    items = [_clean_line(x.get("text", "")) for x in buffer if _clean_line(x.get("text", ""))]
    if items:
        blocks.append({"type": list_type, "items": items})
    buffer.clear()


def _blocks_from_docling_document(doc: Any) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    list_buffer: List[Dict[str, Any]] = []

    for item, level in doc.iterate_items():
        if isinstance(item, TableItem):
            _flush_list(list_buffer, blocks)
            table_block = _table_block(item)
            if table_block:
                blocks.append(table_block)
            continue

        if isinstance(item, ListItem):
            text = _text_from_item(item)
            if text:
                list_buffer.append({"text": text, "enumerated": bool(getattr(item, "enumerated", False))})
            continue

        _flush_list(list_buffer, blocks)

        if isinstance(item, (TitleItem, SectionHeaderItem)):
            heading = _heading_block(item, max(1, min(3, level + 1)))
            if heading:
                blocks.append(heading)
            continue

        label = getattr(item, "label", None)
        if label in {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.PICTURE, DocItemLabel.FORMULA}:
            continue

        if isinstance(item, TextItem) or label in {
            DocItemLabel.PARAGRAPH,
            DocItemLabel.TEXT,
            DocItemLabel.CAPTION,
            DocItemLabel.FOOTNOTE,
            DocItemLabel.CODE,
        }:
            text = _text_from_item(item)
            if text:
                blocks.append({"type": "paragraph", "text": text})

    _flush_list(list_buffer, blocks)
    return blocks


def _docling_output_acceptable(blocks: List[Dict[str, Any]], text: str) -> bool:
    plain = (text or "").strip()
    if len(plain) < _MIN_TEXT_CHARS:
        return False
    if not blocks:
        return False
    if _typed_block_count(blocks) >= _MIN_TYPED_BLOCKS:
        return True
    # Single rich paragraph is still acceptable if long enough
    if len(blocks) == 1 and blocks[0].get("type") == "paragraph":
        return len(str(blocks[0].get("text", "")).strip()) >= _MIN_TEXT_CHARS
    return len(blocks) >= 2


def extract_pdf_bytes_docling(pdf_bytes: bytes) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extract PDF using Docling. Chooses native vs scanned pipeline from text-layer probe.
    Raises on hard failures/timeouts so callers can fall back to pdfplumber.
    """
    if not _docling_enabled():
        raise RuntimeError("Docling PDF extraction is disabled (SOP_DOCLING_PDF_ENABLED=false)")

    scanned = not _pdf_has_native_text_layer(pdf_bytes)
    mode = "scanned+ocr" if scanned else "native"
    logger.info("[docling] starting PDF conversion mode=%s bytes=%s", mode, len(pdf_bytes))

    converter = _get_converter(scanned=scanned)
    stream = DocumentStream(name="upload.pdf", stream=BytesIO(pdf_bytes))

    def _run_convert():
        return converter.convert(stream)

    result = None
    if _DEFAULT_TIMEOUT_SEC > 0:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_convert)
            try:
                result = future.result(timeout=_DEFAULT_TIMEOUT_SEC + 30)
            except FuturesTimeoutError as exc:
                raise TimeoutError(
                    f"Docling PDF conversion exceeded {_DEFAULT_TIMEOUT_SEC}s"
                ) from exc
    else:
        result = _run_convert()

    status = getattr(result, "status", None)
    if status in {ConversionStatus.FAILURE}:
        raise RuntimeError(f"Docling conversion failed with status={status}")

    doc = getattr(result, "document", None)
    if doc is None:
        raise RuntimeError("Docling returned no document")

    blocks = _blocks_from_docling_document(doc)
    text = sanitize_extracted_text(_flatten_blocks_text(blocks))

    if not _docling_output_acceptable(blocks, text):
        raise RuntimeError(
            f"Docling output insufficient (blocks={len(blocks)}, chars={len(text)}, typed={_typed_block_count(blocks)})"
        )

    logger.info(
        "[docling] PDF conversion ok mode=%s blocks=%s typed=%s chars=%s status=%s",
        mode,
        len(blocks),
        _typed_block_count(blocks),
        len(text),
        status,
    )
    return blocks, text
