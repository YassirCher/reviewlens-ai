# ReviewLens Backend

FastAPI backend containing the legacy V1 analysis flow plus the Phase 1 platform foundation, Phase 2 durable V2 execution backbone, Phase 3 OpenRouter/LLMOps core, Phase 4 context graph/retrieval core, and Phase 5 typed research tools.

## Local setup

From the repository root:

```bash
python -m venv backend/.venv
# Windows: backend/.venv/Scripts/activate
# macOS/Linux: source backend/.venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
```

Generate an Argon2id admin password hash interactively. Only the resulting hash belongs in `ADMIN_PASSWORD_HASH`; never put the plaintext password in `.env`. Wrap the hash in single quotes in `.env` so Compose treats its dollar signs literally.

```bash
cd backend
python -m app.cli hash-password
```

Generate independent random values of at least 32 bytes for `SESSION_SECRET`, `PUBLIC_TOKEN_HASH_SECRET`, and `RATE_LIMIT_HASH_SECRET`. Configure the remaining required fields listed in `.env.example` without committing `.env`.

## Database and processes

From `backend`:

```bash
alembic upgrade head
python -m app.cli seed
python -m app.cli validate api
uvicorn app.main:app --reload --port 8000
```

The Compose stack runs the migration/seed as a one-shot dependency and launches the same image as separate API, Celery worker, and Celery scheduler processes. Published configuration versions and audit events are immutable at the PostgreSQL layer. Durable V2 runs snapshot the active published workflow and budget, then persist DAG tasks, attempts, cancellation, retries, progress events, and outbox state before Redis/Celery delivery. V2 chat and embedding calls use the separate `app.llmops` OpenRouter gateway with catalog snapshots, endpoint-aware policy validation, transactional reservations, and immutable final usage attribution. `app.knowledge` validates and atomically versions Markdown bodies, owns typed PostgreSQL relations/manifests, rebuilds Neo4j/Qdrant projections, and produces deterministic token-bounded context packets with PostgreSQL fallback. `app.tools` exposes only fixed typed handlers, authorizes them against immutable snapshots and allowlists, records sanitized invocations, reserves YouTube quota transactionally, and persists untrusted source evidence with explicit lineage.

## Endpoints

Legacy V1 remains operational:

- `GET /health`
- `GET /api/config`
- `POST /api/analyze`
- `POST /api/analyze/stream`

Implemented V2 platform endpoints:

- `GET /health/live`
- `GET /health/ready`
- `POST /api/v2/admin/session`
- `GET /api/v2/admin/session`
- `DELETE /api/v2/admin/session` with `X-CSRF-Token`
- `GET /api/v2/admin/csrf`
- `GET /api/v2/admin/system/health`

Session cookies are HttpOnly, SameSite=Lax, and Secure outside local/test environments. The login endpoint is throttled through Redis; if throttling is unavailable, login fails closed. Health responses expose status rather than credentials or connection strings.

Phase 2 adds no public analysis endpoint. Its deterministic smoke fixture is restricted to `APP_ENV=test` and never calls YouTube or OpenRouter:

```bash
python -m app.cli runtime-fixture --scenario success --wait
```

Phase 3 also adds internal operator/test commands. Catalog refreshes use only the configured V2 OpenRouter base URL; the fixture command is rejected outside `APP_ENV=test`:

```bash
python -m app.cli openrouter-catalog-refresh
python -m app.cli llmops-fixture --operation chat
python -m app.cli llmops-fixture --operation embedding
```

When a 401 or 402 has durably stopped paid dispatch, repair the key or credits first and then clear the block explicitly with `python -m app.cli openrouter-reset-account`. The optional live smoke path is disabled unless its dedicated opt-in flag and exact chat/embedding model slugs are configured. It also requires a command-line confirmation, disables fallbacks, restricts provider prices, and refuses models whose conservative preflight estimate exceeds `OPENROUTER_SMOKE_MAX_COST_MICROUSD`:

```bash
python -m app.cli openrouter-live-smoke --confirm-paid-smoke
```

Normal verification never invokes this command.

Phase 4 adds a deterministic test-only graph/retrieval fixture. The roundtrip scenario rebuilds the local Neo4j and Qdrant projections using deterministic vectors; neither scenario makes a paid call:

```bash
python -m app.cli context-fixture --scenario roundtrip
python -m app.cli context-fixture --scenario degraded
```

Phase 5 adds a deterministic test-only research fixture backed by the local YouTube mock. Its four scenarios exercise default-five coverage, transcript fallback, comments opt-out, and useful partial coverage:

```bash
python -m app.cli research-fixture --scenario complete
python -m app.cli research-fixture --scenario missing_transcript
python -m app.cli research-fixture --scenario comments_off
python -m app.cli research-fixture --scenario partial
```

The live YouTube smoke is disabled by default. It requires `YOUTUBE_LIVE_SMOKE_ENABLED=true`, an explicit confirmation, and a video ID. It performs one metadata request and one transcript probe without search or comments, and prints safe metadata only:

```bash
python -m app.cli youtube-live-smoke --confirm-live-smoke --video-id VIDEO_ID
```

## Verification

```bash
python -m pytest tests -q
```

With Docker Desktop running, execute the isolated empty-database, auth, durable worker/runtime, mocked OpenRouter, and full-stack suite from the repository root:

```bash
python scripts/check_phase5.py
```

The Phase 5 checker generates an isolated environment with fake keys, retains the local OpenRouter mock for earlier tests, and uses a local YouTube mock for research fixtures. It validates the empty-to-head and Phase 4 downgrade/re-upgrade paths, all integration tests, full-stack health, V1 smoke routes, tool auditing, transcript fallback, comments gating, and partial coverage. It never reads the repository `.env`, makes live YouTube calls, or spends OpenRouter credits.
