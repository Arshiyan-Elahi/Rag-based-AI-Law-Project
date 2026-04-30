import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from .database import Base, engine
from .routes import router
from .public_routes import public_router
from .ai_routes import ai_router, CHATBOT_USE_LOCAL_DB, _get_smart_rag_chain
from .auth_routes import router as auth_router
from .chat_history_routes import router as chat_history_router
from .profile_routes import router as profile_router

app = FastAPI(
    title="Cybrain QS API",
    description="SOP Editor + Stage 1 Public Chatbot Data Provisioning API",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)
def _ensure_performance_indexes() -> None:
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_sops_is_active_updated ON sops (is_active, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_sop_versions_sop_created ON sop_versions (sop_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_sop_versions_sop_version ON sop_versions (sop_id, version_number)",
        "CREATE INDEX IF NOT EXISTS idx_sop_deviation_links_sop ON sop_deviation_links (sop_id)",
        "CREATE INDEX IF NOT EXISTS idx_sop_deviation_links_dev ON sop_deviation_links (deviation_id)",
        "CREATE INDEX IF NOT EXISTS idx_deviation_capa_links_dev ON deviation_capa_links (deviation_id)",
        "CREATE INDEX IF NOT EXISTS idx_deviation_capa_links_capa ON deviation_capa_links (capa_id)",
        "CREATE INDEX IF NOT EXISTS idx_capa_audit_links_capa ON capa_audit_links (capa_id)",
        "CREATE INDEX IF NOT EXISTS idx_capa_audit_links_audit ON capa_audit_links (audit_finding_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_decision_links_audit ON audit_decision_links (audit_finding_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_decision_links_decision ON audit_decision_links (decision_id)",
        "CREATE INDEX IF NOT EXISTS idx_decision_sop_links_decision ON decision_sop_links (decision_id)",
        "CREATE INDEX IF NOT EXISTS idx_decision_sop_links_sop ON decision_sop_links (sop_id)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_entity ON knowledge_chunks (entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_entity_version ON knowledge_chunks (entity_type, entity_id, entity_version_id)",
        "CREATE INDEX IF NOT EXISTS idx_ai_link_suggestions_source_status ON ai_link_suggestions (source_entity_type, source_entity_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_ai_link_suggestions_target_status ON ai_link_suggestions (target_entity_type, target_entity_id, status)",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


_ensure_performance_indexes()

app.include_router(router)
app.include_router(public_router)
app.include_router(ai_router)
app.include_router(chat_history_router)
app.include_router(auth_router)
app.include_router(profile_router)


@app.on_event("startup")
def prewarm_rag_runtime() -> None:
    if CHATBOT_USE_LOCAL_DB:
        return

    def _warm() -> None:
        try:
            _get_smart_rag_chain()
        except Exception as exc:
            print(f"[startup] RAG prewarm skipped: {exc}")

    threading.Thread(target=_warm, daemon=True).start()


@app.get("/", tags=["Root"])
def root():
    return {
        "status": "ok",
        "message": "Cybrain QS API is running",
        "version": "1.0.0",
        "docs": "/api/docs",
        
    }


