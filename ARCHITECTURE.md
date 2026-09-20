# ReviewLens V2 runtime architecture

ReviewLens now has one V2 runtime. The legacy application and wire contracts are retired. Migration `20260920_0009`, compatibility telemetry, and Phase 11 cutover observations remain as historical records so retirement can be audited without keeping the old execution path.

```text
Next.js / and /admin
        |
        v
FastAPI /api/v2 ----------------------> PostgreSQL
        |                                  | run/configuration snapshots
        |                                  | tasks, attempts, usage, budgets
        |                                  | audits, reports, graph metadata
        |                                  ` compatibility/cutover history
        |
        +--> Redis broker + progress streams <--> Celery worker/scheduler
        +--> OpenRouter policy gateway --------> chat/embedding attribution
        +--> typed YouTube tools --------------> source evidence and lineage
        `--> Markdown/PostgreSQL graph --------> Neo4j + Qdrant projections
```

## Durable execution

Run creation commits an immutable configuration snapshot, DAG tasks, budget state, progress, and dispatch outbox in PostgreSQL before delivery. Workers lease attempts, commit terminal state before emitting progress, and recover interrupted or duplicate work deterministically. Redis stream gaps fall back to PostgreSQL reconstruction.

## Inference and evidence

OpenRouter is the only inference gateway. Published model policies constrain eligible catalog endpoints and provider privacy. Every call reserves worst case budget before dispatch and records run, task, attempt, policy, requested model, actual model/provider, tokens, and cost. Scheduled reconciliation resolves delayed usage.

YouTube transcripts and comments are untrusted inputs. Typed handlers enforce allowlists, quota, output bounds, and lineage. The analysis DAG links central claims to timestamped evidence and withholds publication when schema, injection, evidence, or audit gates fail.

## Knowledge and projections

Validated Markdown plus PostgreSQL are authoritative. Writes reject unsafe paths, extensions, links, symlinks, and hardlinks. Neo4j and Qdrant are disposable projections rebuilt from the authoritative node versions and manifests. Retrieval remains available through PostgreSQL when projections degrade.

## Public and admin boundaries

`/` is the public V2 intake. `/research` returns a permanent redirect. Signed anonymous sessions are scoped to `/api/v2`; public reports expose allowlisted fields through unlisted revocable tokens. Admin routes require the HttpOnly session and CSRF for mutations. Central redaction prevents credentials, cookies, prompts, and source bodies from entering logs or audit metadata.

`GET /api/v2/admin/cutover` is read only and reports the latest passed non-test observation. The application contains no cutover mutation, legacy adapter, root rollback switch, or scheduled cutover evaluator. Application rollback deploys the recorded Phase 11 image against the unchanged database.
