# Runtime Architecture

> **Current implementation:** The original analysis diagram below describes legacy V1. The Target V2 architecture is specified in [context/03_SYSTEM_ARCHITECTURE.md](./context/03_SYSTEM_ARCHITECTURE.md). Only its Phase 1 platform foundation is implemented so far.

Phase 1 implements the V2 platform beneath the still-default V1 product flow. It adds the shared FastAPI image, PostgreSQL/Alembic, Redis, Celery worker and scheduler, Neo4j, Qdrant, Markdown volumes, admin-session persistence, audit history, and truthful health boundaries. It does not yet implement the durable run engine or V2 analysis API.

```text
FastAPI API ─────── PostgreSQL (authoritative foundation)
     │                  └── admin/session/config versions/audit
     ├──────────── Redis (broker, throttling, health heartbeat)
     ├──────────── Neo4j (available projection dependency)
     └──────────── Qdrant (available projection dependency)

Shared backend image
     ├── API process
     ├── Celery worker
     ├── Celery scheduler
     └── one-shot Alembic migration/admin seed
```

```text
Next.js UI
   |
   | POST /api/analyze/stream (SSE)
   v
FastAPI
   |
   +-- YouTubeService --------> YouTube Data API v3
   |
   +-- TranscriptService -----> youtube-transcript-api
   |
   +-- CommentService --------> YouTube Data API v3 (opt-in)
   |
   +-- AIService
         |
         +-- OpenRouterProvider --> /api/v1/chat/completions
         +-- XAIProvider --------> /v1/chat/completions
         +-- OpenAIProvider -----> /v1/chat/completions (optional)
```

The orchestrator is deterministic: discovery, transcript acquisition, analysis, aggregation. The LLM does not control arbitrary tools.
