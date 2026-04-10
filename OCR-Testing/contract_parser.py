import os
import re
import tempfile
import pytesseract
from pdf2image import convert_from_bytes
from docx import Document
from subprocess import run, PIPE

# Windows local path for Tesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def extract_text_from_contract(filename, content: bytes) -> str:
    if filename.endswith(".pdf"):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                tmp_pdf.write(content)
                tmp_pdf_path = tmp_pdf.name

            text = extract_text_with_pdftotext(tmp_pdf_path)
            os.remove(tmp_pdf_path)

            if len(text.strip()) < 100:
                print("Low text detected, falling back to OCR")
                text = extract_text_from_scanned_pdf(content)

            return text

        except Exception as e:
            print("PDF parsing failed, using OCR fallback:", str(e))
            return extract_text_from_scanned_pdf(content)

    elif filename.endswith(".docx"):
        return extract_text_from_docx(content)

    elif filename.endswith(".txt"):
        return content.decode("utf-8", errors="ignore")

    else:
        return ""


def extract_text_with_pdftotext(pdf_path):
    result = run(["pdftotext", pdf_path, "-"], stdout=PIPE, stderr=PIPE)
    return result.stdout.decode("utf-8", errors="ignore")


def extract_text_from_docx(content: bytes) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_docx:
        tmp_docx.write(content)
        tmp_docx_path = tmp_docx.name

    doc = Document(tmp_docx_path)
    full_text = "\n".join([para.text for para in doc.paragraphs])
    os.remove(tmp_docx_path)
    return full_text


def extract_text_from_scanned_pdf(content: bytes) -> str:
    try:
        images = convert_from_bytes(content, dpi=200)

        text = ""
        for img in images:
            ocr_text = pytesseract.image_to_string(img, lang="eng")
            text += ocr_text + "\n"

        return text
    except Exception as e:
        print("OCR failed:", str(e))
        return ""


def is_title_line(line: str) -> bool:
    clean = line.strip()
    return bool(clean) and clean.isupper() and len(clean) <= 80


def is_section_heading(line: str) -> bool:
    return bool(re.match(r"^§\s*\d+[a-zA-Z0-9\.\-]*", line.strip()))


def is_heading_line(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return False
    if is_title_line(clean):
        return False
    return clean.isupper() and len(clean) <= 60


def is_list_item(line: str) -> bool:
    clean = line.strip()
    return bool(
        re.match(r"^[-–•]\s+", clean)
        or re.match(r"^\d+[\.\)]\s+", clean)
        or re.match(r"^[a-zA-Z][\.\)]\s+", clean)
    )


def looks_like_left_label(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return False
    if len(clean) > 70:
        return False
    if is_section_heading(clean) or is_title_line(clean) or is_heading_line(clean):
        return False
    return clean.endswith(":") or (
        clean[0].islower() and len(clean.split()) <= 8
    )


def normalize_ocr_lines(text: str):
    raw_lines = [
        line.strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]

    merged = []

    for line in raw_lines:
        if not line:
            if merged and merged[-1] != "":
                merged.append("")
            continue

        if not merged:
            merged.append(line)
            continue

        prev = merged[-1]

        if prev == "":
            merged.append(line)
            continue

        should_merge = False

        if prev.endswith((",", ";", ":", "-", "—")):
            should_merge = True
        elif line[:1].islower():
            should_merge = True
        elif len(prev) < 45 and not prev.endswith((".", "?", "!")):
            should_merge = True

        if looks_like_left_label(prev):
            should_merge = False
        if is_section_heading(prev) or is_section_heading(line):
            should_merge = False
        if is_title_line(prev) or is_title_line(line):
            should_merge = False
        if is_heading_line(prev) or is_heading_line(line):
            should_merge = False

        if should_merge:
            merged[-1] = f"{prev.rstrip('-—').strip()} {line}".strip()
        else:
            merged.append(line)

    return merged


def parse_ocr_text_to_blocks(text: str):
    lines = normalize_ocr_lines(text)
    blocks = []
    paragraph_buffer = []
    list_buffer = []

    def flush_paragraph():
        nonlocal paragraph_buffer
        if paragraph_buffer:
            blocks.append({
                "type": "paragraph",
                "text": " ".join(paragraph_buffer).strip()
            })
            paragraph_buffer = []

    def flush_list():
        nonlocal list_buffer
        if list_buffer:
            blocks.append({
                "type": "list",
                "items": list_buffer
            })
            list_buffer = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if not line:
            flush_paragraph()
            flush_list()
            i += 1
            continue

        if is_section_heading(line):
            flush_paragraph()
            flush_list()
            blocks.append({
                "type": "section_heading",
                "text": line
            })
            i += 1
            continue

        if is_title_line(line):
            flush_paragraph()
            flush_list()
            blocks.append({
                "type": "title",
                "text": line
            })
            i += 1
            continue

        if is_heading_line(line):
            flush_paragraph()
            flush_list()
            blocks.append({
                "type": "heading",
                "text": line
            })
            i += 1
            continue

        if is_list_item(line):
            flush_paragraph()
            cleaned = re.sub(r"^([-–•]\s+|\d+[\.\)]\s+|[a-zA-Z][\.\)]\s+)", "", line).strip()
            list_buffer.append(cleaned)
            i += 1
            continue

        # simple two-column row detection:
        # short left label followed by content line
        if looks_like_left_label(line) and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line and not any([
                is_section_heading(next_line),
                is_title_line(next_line),
                is_heading_line(next_line),
                is_list_item(next_line),
            ]):
                flush_paragraph()
                flush_list()
                blocks.append({
                    "type": "two_column_row",
                    "left": line.rstrip(":"),
                    "right": next_line
                })
                i += 2
                continue

        paragraph_buffer.append(line)
        i += 1

    flush_paragraph()
    flush_list()

    return blocks


def extract_contract_content(filename, content: bytes):
    text = extract_text_from_contract(filename, content)
    blocks = parse_ocr_text_to_blocks(text) if text else []
    return {
        "filename": filename,
        "text": text,
        "blocks": blocks,
    }