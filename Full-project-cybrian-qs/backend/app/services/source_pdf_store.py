"""
Persist original uploaded PDFs for scanned imports (separate from editable TipTap content).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict

SOURCE_PDF_DIR = Path(os.getenv("SOP_SOURCE_PDF_DIR", "data/sop_source_pdfs"))


def source_pdf_path(version_id: uuid.UUID | str) -> Path:
    vid = str(version_id)
    return SOURCE_PDF_DIR / f"{vid}.pdf"


def archive_source_pdf(version_id: uuid.UUID | str, pdf_bytes: bytes) -> Path:
    path = source_pdf_path(version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    return path


def source_pdf_exists(version_id: uuid.UUID | str) -> bool:
    return source_pdf_path(version_id).is_file()


def read_source_pdf(version_id: uuid.UUID | str) -> bytes | None:
    path = source_pdf_path(version_id)
    if not path.is_file():
        return None
    return path.read_bytes()


def source_pdf_metadata(version_id: uuid.UUID | str) -> Dict[str, Any]:
    path = source_pdf_path(version_id)
    return {
        "available": path.is_file(),
        "version_id": str(version_id),
        "path": str(path),
        "bytes": path.stat().st_size if path.is_file() else 0,
    }
