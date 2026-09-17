# Runtime Architecture

> **Current implementation:** The original analysis diagram below describes legacy V1. The Target V2 architecture is specified in [context/03_SYSTEM_ARCHITECTURE.md](./context/03_SYSTEM_ARCHITECTURE.md). Beside the still-default V1 product flow at `/`, Phase 8 exposes the separate V2 preview at `/research`, owner progress at `/analysis/{run_id}`, and unlisted reports/evidence at `/r/{public_token}`.

Phase 1 implements the shared platform. Phase 2 adds restart-safe orchestration. Phase 3 adds the separate V2 OpenRouter gateway and exact accounting. Phase 4 makes PostgreSQL and versioned Markdown authoritative for context. Phase 5 adds fixed typed research tools and YouTube quota accounting. Phase 6 adds seven immutable role definitions, deterministic source fan-out/fan-in, and audited internal reports. Phase 7 adds signed anonymous ownership, transactional quota admission, owner-only durable progress, and immutable public report/graph projections whose access can be revoked. Phase 8 adds the public research/report UI, server-only uncached report rendering, safe failure categories, and cross-page evidence relationships. The broad admin control plane remains Phase 9; default-root cutover remains Phase 11.

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
