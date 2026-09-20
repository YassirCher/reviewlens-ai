# Runtime Architecture

> **Current implementation:** The runtime diagram below describes the V2 architecture now served at `/`. `/research` remains a no-index V2 alias, and owner progress and unlisted reports live at `/analysis/{run_id}` and `/r/{public_token}`. The legacy diagram later in this file remains only as a compatibility reference.

Phases 1 through 10 implement the shared platform, restart-safe orchestration, OpenRouter accounting, knowledge graph, typed research tools, bounded analysis DAG, public lifecycle, public UI, admin control plane, and whole-system hardening. Phase 11 makes V2 the default root, routes the temporary V1 wire contracts through V2, records compatibility telemetry, and adds durable stable-window observations. Phase 12 may remove legacy code only after a passed non-test production observation.

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

Phase 6 orchestrator --> validate + bounded query plan + deterministic discovery
                    --> per-source transcript/review (+ optional comments/audience)
                    --> evidence graph curation --> consensus --> quality audit
                    --> at most one declared correction/re-audit cycle
                    `--> immutable internal report or unpublished failed run

Phase 8 browser --> credentialed preflight/create/status/cancel + sequence-replayed SSE
                   --> polling fallback when a stream disconnects
                   --> owner-only report handoff; unlisted /r/* uses server-only API origin
                   --> public-safe evidence graph, paginated links, keyboard list alternative
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
