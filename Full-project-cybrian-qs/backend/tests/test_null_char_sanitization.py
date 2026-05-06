from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pdf_extractor import sanitize_extracted_text
from app.services.sop_metadata_extractor import (
    extract_sop_metadata_from_text,
    strip_invalid_control_chars,
)


def test_strip_invalid_control_chars_removes_null_preserves_unicode():
    raw = "Titel:\x00 Notfallzugriff äöü ✅\nLine\tTwo"
    clean = strip_invalid_control_chars(raw)
    assert "\x00" not in clean
    assert "Notfallzugriff äöü ✅" in clean
    assert "\n" in clean
    assert "\t" in clean


def test_pdf_text_sanitizer_removes_null():
    raw = "SOP ID: SOP-IT-NULL-001\x00 Title: Test"
    clean = sanitize_extracted_text(raw)
    assert "\x00" not in clean
    assert "SOP-IT-NULL-001" in clean


def test_metadata_extraction_handles_null_characters():
    text = (
        "SOP ID: SOP-IT-NULL-001\n"
        "Title: Test\x00 Procedure\n"
        "Status: Under Review\n"
        "Version: 1.0\n"
        "Department: IT/Operations\n"
        "Effective Date: 15.10.2026\n"
        "Review Date: 15.10.2027\n"
    )
    extracted = extract_sop_metadata_from_text(text, use_llm_fallback=False)
    assert extracted["sop_id"] == "SOP-IT-NULL-001"
    assert extracted["title"] == "Test Procedure"
    assert extracted["status"] == "under_review"
