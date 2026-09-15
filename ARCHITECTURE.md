# Runtime Architecture

> **Current implementation:** The original analysis diagram below describes legacy V1. The Target V2 architecture is specified in [context/03_SYSTEM_ARCHITECTURE.md](./context/03_SYSTEM_ARCHITECTURE.md). Its Phase 1 platform, Phase 2 durable runtime, and Phase 3 OpenRouter/LLMOps foundations are implemented; the still-default V1 product flow has not been cut over.

Phase 1 implements the shared FastAPI image, PostgreSQL/Alembic, Redis, Celery worker and scheduler, Neo4j, Qdrant, Markdown volumes, admin-session persistence, audit history, and truthful health boundaries. Phase 2 adds immutable run snapshots, deterministic task DAGs, ordered attempts, leases, cancellation/retry recovery, a transactional dispatch/progress outbox, and Redis Stream progress repaired from PostgreSQL. Phase 3 adds the separate V2 OpenRouter client, versioned model/endpoint/provider catalogs, endpoint-aware routing validation, account health, transactional run/task/agent/daily reservations, and native token/cost reconciliation. It intentionally does not expose public V2 analysis or model-management APIs yet.

```text
Runtime service ---> PostgreSQL transaction
                         |-- immutable configuration snapshot
                         |-- run, DAG tasks, dependencies, attempts
                         |-- run budget state and progress events
                         `-- dispatch/progress outbox

Celery scheduler ---> outbox relay ---> Redis broker / progress streams
Celery worker ------> leased task ----> committed result ---> next outbox work
                          `-----------> stale lease recovery after interruption

V2 paid call -------> PostgreSQL reservation + pending usage
                          |-- OpenRouter-only chat or embedding request
                          |-- actual model/provider/native usage reconciliation
                          `-- scheduled generation lookup when usage is pending
```

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
