from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contract_parser import extract_text_from_contract
from embedding_utils import (
    load_embeddings_from_mongo,
    load_and_chunk_text,
    store_embeddings_to_mongo,
    build_faiss_index
)
from groq_analyzer import analyze_contract_text
from config import TXT_DIR
from sentence_transformers import SentenceTransformer
import numpy as np
from contextlib import asynccontextmanager
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

chunks = []
embeddings = []
index = None
embed_model = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global chunks, embeddings, index, embed_model

    try:
        logger.info("Initializing embeddings...")
        chunks, embeddings = load_embeddings_from_mongo()

        if not chunks:
            logger.info("No existing embeddings. Creating new...")
            chunks = load_and_chunk_text(TXT_DIR)
            store_embeddings_to_mongo(chunks)
            chunks, embeddings = load_embeddings_from_mongo()

        logger.info("Building FAISS index...")
        index = build_faiss_index(np.array(embeddings))

        logger.info("Loading sentence transformer model...")
        embed_model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

        if not os.path.exists("cpi_data.json"):
            logger.warning("cpi_data.json not found. VPI validation will be limited.")
        else:
            logger.info("CPI data file found. VPI validation will be fully functional.")

        logger.info("Initialization complete.")
    except Exception as e:
        logger.error(f"Error during initialization: {e}")
        raise

    yield
    
    logger.info("Cleanup complete.")

app = FastAPI(
    title="Austrian Rental Contract Analyzer",
    description="API for analyzing Austrian rental contracts for legal compliance and market comparison",
    version="1.0.0",
    lifespan=lifespan
)

origins = [
    "https://ai-analying-tool-dqt5.vercel.app",
    "https://contractanalyzer.duckdns.org",
    "http://localhost:3000",      
    "http://localhost:5173",      
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/analyze")
async def analyze_contract(file: UploadFile = File(...)):
    try:
        if not file.filename.endswith((".pdf", ".docx", ".txt")):
            raise HTTPException(
                status_code=400, 
                detail="Unsupported file format. Please upload PDF, DOCX, or TXT files."
            )

        content = await file.read()
        if len(content) > 10 * 1024 * 1024:  
            raise HTTPException(
                status_code=413,
                detail="File too large. Maximum file size is 10MB."
            )

        if len(content) == 0:
            raise HTTPException(
                status_code=400,
                detail="File is empty. Please upload a valid contract file."
            )

        logger.info(f"Processing file: {file.filename}, size: {len(content)} bytes")

        try:
            contract_text = extract_text_from_contract(file.filename, content)
        except Exception as e:
            logger.error(f"Error extracting text from {file.filename}: {e}")
            raise HTTPException(
                status_code=422,
                detail="Could not extract text from the uploaded file. Please ensure the file is not corrupted."
            )

        if len(contract_text.strip()) < 100:
            raise HTTPException(
                status_code=400,
                detail="Extracted text is too short. Please upload a valid contract document."
            )

        try:
            query_embedding = embed_model.encode([contract_text[:1000]], convert_to_numpy=True)
            _, I = index.search(query_embedding, k=5)
            context = "\n\n".join([chunks[i] for i in I[0]])
        except Exception as e:
            logger.warning(f"Error creating embeddings context: {e}")
            context = ""  

        logger.info("Starting contract analysis...")

        try:
            analysis_result = analyze_contract_text(contract_text, context)
        except Exception as e:
            logger.error(f"Error during contract analysis: {e}")
            raise HTTPException(
                status_code=500,
                detail="Error occurred during contract analysis. Please try again or contact support."
            )

        if "rent_comparison" not in analysis_result or not isinstance(analysis_result["rent_comparison"], dict):
            analysis_result["rent_comparison"] = {
                "percent": "N/A", 
                "text": "Keine Vergleichsdaten verfügbar"
            }
        else:
            analysis_result["rent_comparison"].setdefault("percent", "N/A")
            analysis_result["rent_comparison"].setdefault("text", "Keine Vergleichsdaten verfügbar")

        if "indexation_clause_analysis" not in analysis_result:
            analysis_result["indexation_clause_analysis"] = {
                "info": "Keine Wertsicherungsklausel gefunden oder analysiert."
            }
        
        if "vpi_validation" not in analysis_result:
            analysis_result["vpi_validation"] = {
                "comment": "VPI Validierung nicht verfügbar.",
                "indexation_valid": "Keine Angabe"
            }
        
        if "richtwert_validation" not in analysis_result:
            analysis_result["richtwert_validation"] = {
                "applicable": False,
                "max_rent_allowed": "Nicht verfügbar",
                "valid": "Keine Angabe",
                "comment": "Richtwert Validierung nicht verfügbar."
            }

        analysis_result["metadata"] = {
            "filename": file.filename,
            "file_size_bytes": len(content),
            "text_length": len(contract_text),
            "analysis_timestamp": datetime.now().isoformat(),
            "context_used": len(context) > 0
        }

        logger.info("Contract analysis completed successfully")
        return JSONResponse(analysis_result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in analyze_contract: {e}")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later."
        )

@app.get("/")
async def root():
    return {
        "message": "Austrian Rental Contract Analyzer API is running",
        "version": "1.0.0",
        "endpoints": {
            "analyze": "/analyze - POST - Upload contract for analysis",
            "health": "/health - GET - Check API health status"
        }
    }

@app.get("/health")
async def health_check():
    try:
        embeddings_status = len(chunks) > 0
        model_status = embed_model is not None
        index_status = index is not None
        cpi_data_status = os.path.exists("cpi_data.json")
        
        return {
            "status": "healthy" if all([embeddings_status, model_status, index_status]) else "degraded",
            "components": {
                "embeddings_loaded": embeddings_status,
                "embeddings_count": len(chunks),
                "model_loaded": model_status,
                "faiss_index_ready": index_status,
                "cpi_data_available": cpi_data_status
            },
            "features": {
                "contract_analysis": embeddings_status and model_status,
                "vpi_validation": cpi_data_status,
                "richtwert_validation": True,
                "context_retrieval": embeddings_status and index_status
            }
        }
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e)
            }
        )

@app.get("/status")
async def system_status():
    """Detailed system status endpoint for debugging"""
    try:
        return {
            "system": {
                "cpi_data_exists": os.path.exists("cpi_data.json"),
                "txt_dir_exists": os.path.exists(TXT_DIR) if TXT_DIR else False,
                "chunks_loaded": len(chunks),
                "embeddings_shape": np.array(embeddings).shape if embeddings else "No embeddings",
                "model_loaded": embed_model is not None,
                "index_ready": index is not None
            },
            "config": {
                "txt_dir": TXT_DIR,
                "allowed_origins": origins
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Status check failed: {str(e)}"}
        )