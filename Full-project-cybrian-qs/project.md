# Cybrain Quality System (Cybrain QS)

**Full-project-cybrian-qs** — A quality-management platform for Standard Operating Procedures (SOPs) and related GxP entities (deviations, CAPAs, audit findings, decisions). It combines a **TipTap document editor**, **hybrid RAG search**, **semantic linking**, and **AI-assisted authoring** in one stack.

---

## Table of contents

1. [Purpose and scope](#purpose-and-scope)
2. [High-level architecture](#high-level-architecture)
3. [Technology stack](#technology-stack)
4. [Repository layout](#repository-layout)
5. [Core domain model](#core-domain-model)
6. [Backend (FastAPI)](#backend-fastapi)
7. [Frontend (React + Vite)](#frontend-react--vite)
8. [SOP import and extraction pipeline](#sop-import-and-extraction-pipeline)
9. [Semantic pipeline and RAG](#semantic-pipeline-and-rag)
10. [AI, chat, and editor actions](#ai-chat-and-editor-actions)
11. [Authentication and security](#authentication-and-security)
12. [Database and migrations](#database-and-migrations)
13. [Background workers and jobs](#background-workers-and-jobs)
14. [Environment configuration](#environment-configuration)
15. [Running the project](#running-the-project)
16. [API surface (overview)](#api-surface-overview)
17. [Recent implementation notes](#recent-implementation-notes)
18. [Testing and scripts](#testing-and-scripts)

---

## Purpose and scope

| Capability | Description |
|------------|-------------|
| **SOP lifecycle** | Create, version, edit, and soft-delete SOPs with workflow status (draft, under review, effective, obsolete, etc.). |
| **Rich editor** | TipTap-based WYSIWYG editor with headings, lists, tables, links, version history, diff view, and metadata panels. |
| **Document import** | Upload PDF, DOCX, or TXT; structure preserved via Docling / pdfplumber / python-docx; async background processing for large/scanned files. |
| **Quality graph** | Link SOPs ↔ deviations ↔ CAPAs ↔ audits ↔ decisions (deterministic + AI-suggested links). |
| **Hybrid RAG** | Dense + sparse retrieval in Qdrant, reranking, and LLM-grounded answers with citations. |
| **Assistant** | Dashboard chat, in-editor AI (rewrite, gap check, improve), and optional SOP create/update/delete intents. |
| **Compliance-oriented NLP** | Profile detection, style/tone analysis, and prompt injection for editor AI actions. |

Primary users: quality / regulatory teams managing controlled documents and investigating related records.

---

## High-level architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                     React frontend (Vite, port 5173)                     │
│  Dashboard │ SOPs + embedded Editor │ Chat │ Knowledge │ Entities       │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTP /api/*
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              FastAPI backend (uvicorn, default port 8001)                │
│  routes │ ai_routes │ auth │ chat_history │ profile │ webhooks │ public  │
└───────┬─────────────────────────────┬───────────────────┬───────────────┘
        │                             │                   │
        ▼                             ▼                   ▼
┌───────────────┐            ┌────────────────┐    ┌───────────────────┐
│  PostgreSQL   │            │    Qdrant      │    │  LLM (OpenAI-compat │
│  (editor_db)  │            │ vector store   │    │  / Gemini / local)  │
└───────────────┘            └────────────────┘    └───────────────────┘

Background (threads / separate processes):
  • sop_import_worker   — PDF/DOCX extraction, metadata, TipTap JSON
  • semantic_jobs       — chunk → embed → Qdrant → NLP → linking
  • embedding_worker    — polls embedding_jobs queue
```

**Startup phases** (`app/main.py`):

1. **DB** — schema ensure + performance indexes (fast).
2. **HTTP ready** — `/api/health` responds immediately.
3. **Background** — Qdrant probe, RAG prewarm, optional stale-chunk reconcile, webhook config validation.

---

## Technology stack

| Layer | Technologies |
|-------|----------------|
| **Language** | Python 3.12+ (`uv`, `.python-version`) |
| **API** | FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2.x |
| **DB** | PostgreSQL 16, JSONB for `content_json` / `metadata_json` |
| **Vectors** | Qdrant, `qdrant-client`, LangChain-Qdrant |
| **Embeddings** | Sentence Transformers / HuggingFace (`BAAI/bge-small-en-v1.5`, pipeline may use BGE-M3) |
| **Reranking** | Cross-encoder (`ms-marco-MiniLM-L-6-v2` class models) |
| **LLM** | Configurable: Google Gemini, OpenAI-compatible local server, etc. |
| **PDF/DOCX** | Docling 2.x, pdfplumber, pypdf, pytesseract, Poppler, python-docx |
| **Frontend** | React 19, Vite 8, React Router 7, TipTap 3, Tailwind 4, Sass |
| **Auth** | JWT (access + refresh), bcrypt, `python-jose` |
| **Migrations** | Alembic (`database/alembic/`) |
| **Containers** | Docker Compose (Postgres, Qdrant, backend, frontend, optional workers) |

---

## Repository layout

```text
Full-project-cybrian-qs/
├── project.md                 ← this document
├── README.md                  ← quick start (RAG-focused)
├── pyproject.toml             ← Python dependencies (uv)
├── uv.lock
├── .python-version
├── docker-compose.yml
├── nlp_pipeline.py            ← standalone NLP utilities (optional extra)
│
├── backend/
│   ├── app/
│   │   ├── main.py            ← FastAPI entry, CORS, routers, startup
│   │   ├── routes.py          ← editor + domain CRUD, import, semantic APIs
│   │   ├── ai_routes.py       ← chat, RAG query, editor AI actions (wraps chatbot)
│   │   ├── auth_routes.py
│   │   ├── chat_history_routes.py
│   │   ├── profile_routes.py
│   │   ├── public_routes.py   ← webhook ingestion targets
│   │   ├── webhook_routes.py
│   │   ├── schemas.py         ← Pydantic request/response models
│   │   ├── models.py          ← SQLAlchemy ORM (primary app models)
│   │   ├── database.py
│   │   └── services/          ← business logic (see below)
│   │   └── utils/             ← tiptap_builder, table_blocks, tiptap_text
│   ├── chatbot/               ← RAG chain, retrieval, LLM provider, actions
│   ├── action/                ← SOP action prompts + runtime
│   ├── run_embedding_worker.py
│   ├── data/sop_imports/      ← temp upload files for async import (runtime)
│   └── .env                   ← secrets (not committed; see .env.example)
│
├── frontend/
│   ├── src/
│   │   ├── pages/             ← Dashboard, SOPs, Editor, Chat, Knowledge, Entities
│   │   ├── components/        ← Editor, Dashboard, Layout, SOP table, etc.
│   │   ├── api/editorApi.js   ← HTTP client for backend
│   │   ├── utils/             ← import, editor, AI bridges, workflow
│   │   └── context/           ← i18n, SOP config
│   └── vite.config.js         ← dev proxy → backend :8001
│
└── database/
    ├── alembic/               ← migrations 0001–0005
    └── database/              ← shared DB config helpers
```

---

## Core domain model

### Primary entities

| Entity | Table | Role |
|--------|-------|------|
| **SOP** | `sops` | Controlled document header (`sop_number`, title, department, `current_version_id`). |
| **SOPVersion** | `sop_versions` | Versioned body: `content_json` (TipTap), `metadata_json`, status, dates. |
| **Deviation** | `deviations` | Quality deviation records. |
| **CAPA** | `capas` | Corrective/preventive actions. |
| **AuditFinding** | `audit_findings` | Audit findings. |
| **Decision** | `decisions` | Decision records. |

### Link tables (quality graph)

`sop_deviation_links`, `deviation_capa_links`, `capa_audit_links`, `audit_decision_links`, `decision_sop_links` — tenant-scoped edges with optional rationale and confidence.

### Knowledge & AI support

| Table | Purpose |
|-------|---------|
| `knowledge_chunks` | Text chunks per entity/version for RAG and traceability. |
| `embedding_jobs` | Background pipeline stages: chunking, embeddings, Qdrant, NLP, semantic linking. |
| `ai_link_suggestions` | Pending/accepted/rejected semantic link proposals. |
| `profile_detections` | NLP/style snapshot per SOP version for editor AI. |
| `chat_sessions` / `chat_messages` | Persisted assistant conversations. |
| `users` | Auth accounts. |

### Editor content shape

- **`content_json`** — TipTap document: `{ type: "doc", content: [...] }` with `paragraph`, `heading`, `bulletList`, `orderedList`, `table`, etc.
- **`metadata_json`** — “Thick shell”: `sopStatus`, `sopMetadata` (documentId, title, version, dates, department, …), `auditTrail`, and internal keys such as `_import_job`, `_import_context_hash`.

---

## Backend (FastAPI)

### Router map

| Module | Prefix / examples | Responsibility |
|--------|---------------------|----------------|
| `routes.py` | `/api/editor/*`, `/api/sops`, `/api/extract-text`, `/api/semantic/*`, `/api/links`, … | Editor compatibility layer, CRUD, import, search, linking. |
| `ai_routes.py` | `/api/ai/*`, chat query | RAG, chat persistence, bubble-menu actions, assistant intents. |
| `auth_routes.py` | `/api/auth/*` | Register, login, refresh. |
| `chat_history_routes.py` | `/api/chat/*` | Sessions and messages. |
| `profile_routes.py` | Profile detection uploads | Client/SOP profile analysis. |
| `public_routes.py` | `/api/public/*` | External/webhook-friendly SOP payloads. |
| `webhook_routes.py` | Webhook sync triggers | Pull remote entities, schedule reindex. |

### Key services (`backend/app/services/`)

| Service | Role |
|---------|------|
| `sop_import_worker.py` | **Async import**: thread pool, job status in `_import_job`, extraction → TipTap → DB → indexing. |
| `pdf_extractor.py` | Layout-aware PDF, tables, OCR fallback, Docling orchestration. |
| `docling_extractor.py` | Native vs scanned PDF pipelines (Docling OCR/table structure). |
| `docx_extractor.py` | Structured DOCX via python-docx. |
| `document_structure.py` | Line/block heuristics, `refine_blocks`, structured document JSON. |
| `sop_metadata_extractor.py` | SOP ID, title, version, status from text + optional LLM fallback. |
| `semantic_pipeline.py` | Chunking, embeddings, Qdrant upsert, NLP, link suggestions. |
| `semantic_jobs.py` | Thread-pool scheduler for reindex jobs. |
| `embedding_worker.py` | DB-polled worker for `embedding_jobs`. |
| `webhook_service.py` | External sync + enqueue semantic jobs. |
| `profile_detection_store.py` | Persist/query editor NLP profiles. |
| `editor_action_nlp.py` | NLP layer for in-editor AI context. |

### Utilities (`backend/app/utils/`)

| Module | Role |
|--------|------|
| `tiptap_builder.py` | Map extraction blocks → TipTap JSON; merge text without destroying tables (AI updates). |
| `table_blocks.py` | Normalize table rows, header detection, paragraph→table recovery. |
| `tiptap_text.py` | Plain-text extraction from TipTap trees. |

### Chatbot package (`backend/chatbot/`)

| Area | Role |
|------|------|
| `rag/rag_chain.py` | Smart RAG chain, citation handling. |
| `retrieval/` | Hybrid, federated, reranker, query router, context builder. |
| `llm/provider.py` | LLM abstraction (OpenAI-compatible, etc.). |
| `actions/` | Rewrite, improve, gap-check prompts and runtime. |
| `assistant/intent_classifier.py` | Dashboard assistant intents (create/update/delete SOP). |

`app/ai_routes.py` re-exports and extends `chatbot.routes.ai_routes` for backward-compatible imports.

---

## Frontend (React + Vite)

### Routes (`App.jsx`)

| Path | Page | Notes |
|------|------|-------|
| `/` | Dashboard | KPIs, AI search, relevant SOPs. |
| `/sops` | SOPsPage | List, upload, **tabbed embedded EditorPage**. |
| `/knowledge` | KnowledgePage | Knowledge exploration. |
| `/chat` | ChatPage | Full-page assistant. |
| `/deviations`, `/capa`, `/audits`, `/decisions` | EntitiesPage | Entity lists. |
| `/editor`, `/editor/:id` | EditorPage | Standalone editor route. |

### SOPs page + editor integration

- **Upload**: `POST /api/editor/sops/import-async` (background) with status polling (`GET /api/editor/import-jobs/{job_id}`).
- **Modal states**: uploading → extracting → ocr_processing → indexing → success / failed.
- **Editor tab** receives `initialDocId`, `initialDocJson`, `initialMetadataJson`, `openRequestKey` for hydration without wiping content (autosave guards during import).

### Editor stack

- **TipTap** — StarterKit, tables, hard breaks, links, underline, inline AI suggestion extension.
- **Components** — `EditorToolbarSection`, `EditorTypingSurface`, `SOPMetadataPanel`, `RelatedContextSidebar`, `SideBySideViewer` (diff), `AIAssistantBubbleMenu`, `EditorAIBridge`.
- **API client** — `frontend/src/api/editorApi.js` (documents, versions, extract, async import, AI actions, semantic reindex).

### Important frontend utilities

| File | Role |
|------|------|
| `sopImportService.js` | File validation, async import, polling, TipTap mapping. |
| `editorUtils.js` | TipTap helpers, `mapBlocksToTipTapDoc`, `sanitizeTipTapDoc`, table normalization. |
| `editorAiBridge.js` / `editorActionsBridge.js` | Connect editor selection to backend AI. |
| `sopConstants.js` / `sopStateMachine.js` | Workflow statuses and transitions. |

---

## SOP import and extraction pipeline

### Supported formats

- **PDF** — native text and scanned (OCR).
- **DOCX** — structured extraction.
- **TXT** — line-based structure detection.

Only `.pdf`, `.docx`, `.txt` are accepted (frontend and backend); other MIME types are blocked.

### Sync path (in-editor toolbar)

1. `POST /api/extract-text` — full extraction in request thread (long timeout on client).
2. Frontend `mapBlocksToTipTapDoc` → user saves via `POST /api/editor/docs`.

### Async path (SOPs page upload) — recommended for large files

```text
1. POST /api/editor/sops/import-async
   → Creates SOP + SOPVersion shell (placeholder TipTap doc)
   → Stores file under backend/data/sop_imports/{job_id}.ext
   → Enqueues sop_import_worker thread
   → Returns { job_id, import_status, document }

2. Background worker stages (metadata._import_job.status):
   uploading → extracting → ocr_processing (scanned PDF) → indexing → completed | failed

3. Worker:
   - Extract blocks + text (Docling → pdfplumber fallback)
   - Metadata (rules + optional LLM if document not huge)
   - map_blocks_to_tiptap_doc (backend tiptap_builder)
   - Update content_json + metadata_json
   - _upsert_import_context_entities + schedule_semantic_reindex

4. Frontend polls GET /api/editor/import-jobs/{job_id}
   → On completed, refreshes document into editor tab
```

### PDF extraction strategy

| PDF type | Primary | Fallback |
|----------|---------|------------|
| Native (text layer) | Docling (`force_backend_text=True`) | pdfplumber / pypdf |
| Scanned | Docling OCR (`do_ocr=True`) | pdfplumber + Tesseract |
| Poor Docling output | — | Legacy pdfplumber layout + table finders |

### Table preservation

- Extraction emits `{ type: "table", rows: string[][], header_rows?: number }`.
- `table_blocks.py` normalizes column counts and infers header rows.
- `tiptap_builder.py` / `editorUtils.js` emit valid TipTap `table` → `tableRow` → `tableHeader` / `tableCell`.

---

## Semantic pipeline and RAG

### Ingestion flow (per entity/version)

1. **Chunk** — Split TipTap/plain text into `knowledge_chunks`.
2. **Embed** — Dense vectors to Qdrant collections (`docs_sops`, deviations, capas, etc.).
3. **NLP** — Optional spaCy/langdetect pipeline (`nlp` extra).
4. **Semantic linking** — Suggest cross-entity links stored in `ai_link_suggestions`.

Jobs tracked in `embedding_jobs` with per-stage status columns.

### Hybrid RAG query (chat / dashboard)

1. Embed user query.
2. Hybrid retrieval (dense + BM25-style sparse in Qdrant).
3. Cross-encoder rerank.
4. LLM answer with strict context + citations.

Configurable via `CHATBOT_USE_LOCAL_DB`, `QDRANT_URL`, `LLM_PROVIDER`, token limits in `.env`.

### Import side effects

On SOP save/import, `_upsert_import_context_entities` may:

- Extract DEV/CAPA/AUD/DEC/SOP tokens from text.
- Create stub entities and deterministic links.
- Run semantic similarity for link suggestions.
- Schedule `import_reindex` semantic jobs (skips unchanged content via `_import_context_hash`).

---

## AI, chat, and editor actions

### Dashboard / full chat

- **Endpoint family**: `/api/ai/query` (and related) via `ai_routes.py`.
- **Persistence**: optional chat sessions when JWT present.
- **Context**: federated retrieval across entity collections.

### In-editor AI (bubble menu / bridge)

- **Actions**: rewrite, improve, gap check (schemas in `backend/chatbot/schemas/sop_actions.py`).
- **Scope**: section-only edits enforced server-side where configured.
- **Profile injection**: `profile_detections` + NLP prompt block for style-aligned output.

### Assistant SOP intents

- Classifier can trigger create / update / delete SOP (with safeguards).
- **Update** uses `merge_text_preserving_tables` so existing tables are not flattened to plain paragraphs.

---

## Authentication and security

- **JWT** access + refresh tokens (`JWT_SECRET_KEY`, `JWT_REFRESH_SECRET_KEY`).
- **Optional auth** on some AI routes (`get_current_user_optional`) for chat history when logged in.
- **Editor mutations** gated by `MOCK_EDITOR_MODE` (default `true` in dev — set explicitly for production read-only vs write).
- **Webhooks** — `WEBHOOK_SECRET` for inbound sync verification.
- **CORS** — configured in `main.py` for local Vite origin.

---

## Database and migrations

- **Default DB name**: `editor_db` (local example port `5433`, Docker internal `5432`).
- **ORM**: `backend/app/models.py` (runtime tables).
- **Alembic**: `database/alembic/versions/`  
  - `0001_initial_schema`  
  - `0002_add_audit_vault_fields`  
  - `0003_add_ai_suggestions`  
  - `0004_chat_sessions_user_nullable`  
  - `0005_sop_pipeline_job_stages`

Apply migrations:

```bash
cd database
uv run --project .. alembic upgrade head
```

---

## Background workers and jobs

| Process | Command | Role |
|---------|---------|------|
| **API server** | `cd backend && uv run --directory .. uvicorn app.main:app --host 127.0.0.1 --port 8001` | HTTP API. |
| **Embedding worker** | `uv run python backend/run_embedding_worker.py` | Processes `embedding_jobs` queue. |
| **Import worker** | In-process `ThreadPoolExecutor` in `sop_import_worker` | No separate process; threads inside API. |
| **Semantic jobs** | In-process threads via `semantic_jobs.py` | Triggered after saves/imports. |

---

## Environment configuration

Copy `backend/.env.example` → `backend/.env`. Important groups:

| Group | Variables (examples) |
|-------|---------------------|
| **Database** | `DATABASE_URL_LOCAL`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` |
| **Qdrant** | `QDRANT_URL`, `QDRANT_API_KEY`, `COLLECTION_*` |
| **LLM** | `LLM_PROVIDER`, `GEMINI_API_KEY`, `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, token limits |
| **RAG / chat** | `CHATBOT_USE_LOCAL_DB`, `CHAT_QUERY_TIMEOUT_SECONDS`, `RAG_MAX_OUTPUT_TOKENS` |
| **Auth** | `JWT_SECRET_KEY`, `JWT_REFRESH_SECRET_KEY` |
| **OCR** | `TESSERACT_CMD`, `POPPLER_PATH` |
| **Import** | `SOP_IMPORT_WORKER_THREADS`, `SOP_IMPORT_UPLOAD_DIR`, `SOP_IMPORT_MAX_BYTES`, `SOP_DOCLING_PDF_ENABLED`, `DOCLING_PDF_TIMEOUT_SEC` |
| **Semantic** | `SEMANTIC_WORKER_THREADS`, `SEMANTIC_RECONCILE_ON_STARTUP`, `EMBEDDING_WORKER_POLL_SECONDS` |
| **Webhooks** | `WEBHOOK_*`, `API_BASE_URL` |
| **Editor** | `MOCK_EDITOR_MODE` |

Frontend: `VITE_API_BASE` (optional) — Vite proxy defaults to `http://127.0.0.1:8001`.

---

## Running the project

### Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 18+ (22 LTS recommended)
- PostgreSQL running
- Qdrant (local Docker or cloud)
- Optional: Tesseract + Poppler for OCR PDFs on Windows

### Backend

```bash
uv sync
# optional: uv sync --extra nlp
copy backend\.env.example backend\.env
cd database && uv run --project .. alembic upgrade head && cd ..
cd backend
uv run --directory .. uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Second terminal (recommended):

```bash
uv run python backend/run_embedding_worker.py
```

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

Open the Vite URL (typically `http://localhost:5173`).

### Docker Compose

```bash
docker compose up --build
```

Uses bundled Postgres, Qdrant, backend, and frontend per `docker-compose.yml`.

---

## API surface (overview)

### Health & import

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health + OCR readiness hint. |
| POST | `/api/extract-text` | Sync extract PDF/DOCX/TXT (multipart). |
| POST | `/api/editor/sops/import-async` | Fast async upload + job id. |
| GET | `/api/editor/import-jobs/{job_id}` | Poll import status. |

### Editor / documents

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/editor/docs` | Create SOP + v1. |
| GET | `/api/editor/docs/{doc_id}` | Load current version (`doc_json`). |
| PUT | `/api/editor/docs/{doc_id}` | Update current version in place. |
| DELETE | `/api/editor/docs/{doc_id}` | Soft-delete SOP. |
| GET/POST | `/api/editor/docs/{doc_id}/versions` | Version list / create. |
| PUT | `/api/editor/docs/.../status` | Workflow status transition. |

### Domain

| Resource | Base path |
|----------|-----------|
| SOPs | `/api/sops` |
| Deviations | `/api/deviations` |
| CAPAs | `/api/capas` |
| Audits | `/api/audits` |
| Decisions | `/api/decisions` |
| Links | `/api/links` |
| Semantic | `/api/semantic/*` (reindex, suggestions, status, maintenance) |
| Search | `/api/search` |

### AI & chat

- `/api/ai/*` — query, actions (see `ai_routes.py` for full list).
- `/api/chat/sessions`, `/api/chat/sessions/{id}/messages`

### Auth

- `/api/auth/register`, `/api/auth/login`, refresh endpoints

---

## Recent implementation notes

These behaviors reflect the current codebase (2025–2026 maintenance):

1. **Async SOP upload** — Shell record returned immediately; extraction runs in `sop_import_worker` threads.
2. **Import job status** — Stored in `metadata_json._import_job`; API builds status from ORM after commit (not only JSONB SQL).
3. **Editor hydration safety** — `pendingInitialHydrationRef` blocks autosave from overwriting content during import; `initialDocJson` applied on tab open.
4. **Table-aware mapping** — Shared logic in `table_blocks.py`, `tiptap_builder.py`, `editorUtils.js`.
5. **AI update preservation** — `merge_text_preserving_tables` in `ai_routes` update path.
6. **Semantic skip** — Unchanged import content skipped via `_import_context_hash` when reindexing.

---

## Testing and scripts

| Path | Purpose |
|------|---------|
| `testsprite_tests/` | Pytest configuration (`pyproject.toml` testpaths). |
| `backend/scripts/` | DB repair, maintenance (optional `uv sync --extra scripts`). |
| `nlp_pipeline.py` | Root-level NLP experiments (`uv sync --extra nlp`). |

---

## Related documentation

- [README.md](./README.md) — Hybrid RAG setup and quick start.
- [frontend/README.md](./frontend/README.md) — Frontend-specific notes.
- [backend/.env.example](./backend/.env.example) — Environment template.

---

## Project identity

| Item | Value |
|------|--------|
| **Package name** | `cybrain-qs` |
| **Description** | Hybrid RAG SOP search — FastAPI backend, Qdrant, PostgreSQL |
| **Default API port** | `8001` |
| **Default tenant (dev)** | Fixed UUID in routes (`FIXED_TENANT_ID`) for seed/dev |

For questions about a specific module, start from the service or page named in this document and follow imports into `backend/app/services/` or `frontend/src/`.
