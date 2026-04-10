from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contract_parser import extract_text_from_contract
from contract_parser import extract_contract_content
from contract_parser import extract_contract_content  # NEW import


app = FastAPI(
    title="Local Contract OCR API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Local OCR API running"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    if not file.filename.endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty")

    # 🔥 NEW: use structured extractor
    result = extract_contract_content(file.filename, content)

    text = result.get("text", "")
    blocks = result.get("blocks", [])

    if not text.strip() and not blocks:
        raise HTTPException(status_code=422, detail="Could not extract text from file")

    return JSONResponse({
        "filename": result.get("filename"),
        "text": text,
        "blocks": blocks   # 🔥 NEW FIELD
    })


