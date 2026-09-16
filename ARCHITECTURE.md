# Runtime Architecture

> **Current implementation:** The original analysis diagram below describes legacy V1. The Target V2 architecture is specified in [context/03_SYSTEM_ARCHITECTURE.md](./context/03_SYSTEM_ARCHITECTURE.md). Its Phase 1 platform, Phase 2 durable runtime, Phase 3 OpenRouter/LLMOps, Phase 4 context graph/retrieval, and Phase 5 typed research-tool foundations are implemented; the still-default V1 product flow has not been cut over.

Phase 1 implements the shared FastAPI image, PostgreSQL/Alembic, Redis, Celery worker and scheduler, Neo4j, Qdrant, Markdown volumes, admin-session persistence, audit history, and truthful health boundaries. Phase 2 adds immutable run snapshots, deterministic task DAGs, ordered attempts, leases, cancellation/retry recovery, a transactional dispatch/progress outbox, and Redis Stream progress repaired from PostgreSQL. Phase 3 adds the separate V2 OpenRouter client, versioned model/endpoint/provider catalogs, endpoint-aware routing validation, account health, transactional run/task/agent/daily reservations, and native token/cost reconciliation. Phase 4 makes PostgreSQL relations and validated, versioned Markdown bodies authoritative; Neo4j and Qdrant are rebuildable projections, and immutable retrieval manifests record bounded provenance-labelled context packets. Phase 5 adds a fixed executable tool registry, immutable run-snapshot authorization, durable invocation audits, Pacific-window YouTube quota reservations, deterministic source selection, transcript/comment lineage, evidence validation, and V2-only scoring. These phases intentionally expose no public V2 analysis or model-management APIs yet.

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

Authoritative context write --> temporary validated Markdown --> atomic replace
                                      |--> PostgreSQL node/version/edge + projection outbox
                                      |--> Neo4j metadata/typed-edge projection
                                      `--> Qdrant versioned embedding projection

Task retrieval --> required seeds + PostgreSQL graph + Qdrant semantic + lexical candidates
              --> authorize/rerank/budget whole nodes --> immutable context manifest

Phase 5 tool call --> immutable run snapshot + agent/task allowlist
                       |--> durable invocation identity and quota reservation
                       |--> fixed YouTube/graph/vector/evidence/scoring handler
                       `--> terminal audit before task progress

YouTube research --> query variants --> raw source nodes --> deterministic ranking
                 --> transcript fallback --> timestamped chunks + lineage
                 `--> optional secondary-trust comment sets
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
