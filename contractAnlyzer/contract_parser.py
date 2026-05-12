import os
import tempfile
import pytesseract
from pdf2image import convert_from_bytes
from docx import Document
from subprocess import run, PIPE

def extract_text_from_contract(filename, content: bytes) -> str:
    if filename.endswith(".pdf"):
        try:
            # Try to extract text directly (for digitally created PDFs)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                tmp_pdf.write(content)
                tmp_pdf_path = tmp_pdf.name

            text = extract_text_with_pdftotext(tmp_pdf_path)
            os.remove(tmp_pdf_path)

            if len(text.strip()) < 100:  # Likely a scanned PDF (not enough text)
                print("Low text detected, falling back to OCR")
                text = extract_text_from_scanned_pdf(content)

            return text

        except Exception as e:
            print("PDF parsing failed, using OCR fallback:", str(e))
            return extract_text_from_scanned_pdf(content)

    elif filename.endswith(".docx"):
        return extract_text_from_docx(content)

    elif filename.endswith(".txt"):
        return content.decode("utf-8")

    else:
        return ""

def extract_text_with_pdftotext(pdf_path):
    result = run(["pdftotext", pdf_path, "-"], stdout=PIPE, stderr=PIPE)
    return result.stdout.decode("utf-8")

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
        # Explicitly specify poppler_path for pdf2image
        images = convert_from_bytes(content, dpi=300, poppler_path="/usr/bin")

        text = ""
        for img in images:
            ocr_text = pytesseract.image_to_string(img, lang='deu+eng')
            text += ocr_text + "\n"

        return text
    except Exception as e:
        print("OCR failed:", str(e))
        return ""
