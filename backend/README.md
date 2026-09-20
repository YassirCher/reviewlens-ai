# ReviewLens Backend

FastAPI backend for the durable V2 research runtime, public API, admin control plane, and temporary V1 compatibility adapter. The adapter creates ordinary V2 runs with active published configuration and records content-free cutover telemetry.

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

Temporary V1-compatible contracts delegate to the V2 runtime:

- `GET /health`
- `GET /api/config`
- `POST /api/analyze`
- `POST /api/analyze/stream`

Both analysis routes accept the legacy `provider` field for wire compatibility and ignore it. They return deprecation and successor-version headers, use policy-managed OpenRouter routing, and return `410` when `LEGACY_ANALYSIS_ADAPTER_ENABLED=false`.

Implemented V2 platform endpoints:

- `GET /health/live`
- `GET /health/ready`
- `POST /api/v2/admin/session`
- `GET /api/v2/admin/session`
- `DELETE /api/v2/admin/session` with `X-CSRF-Token`
- `GET /api/v2/admin/csrf`
- `GET /api/v2/admin/system/health`
- `POST /api/v2/analyses/preflight`
- `POST /api/v2/analyses` with `Idempotency-Key`
- `GET /api/v2/analyses/{run_id}` and `/events` with resumable `Last-Event-ID`
- `POST /api/v2/analyses/{run_id}/cancel`
- `GET /api/v2/reports/{public_token}` and `/graph`
- `POST /api/v2/admin/analyses` and `/admin/reports/{report_id}/revoke` with CSRF
- `GET /api/v2/admin/cutover`
- `POST /api/v2/admin/cutover/observations` with CSRF
- `POST /api/v2/admin/cutover/observations/{id}/evaluate` with CSRF
- `POST /api/v2/admin/cutover/observations/{id}/record-rollback` with CSRF

Session cookies are HttpOnly, SameSite=Lax, and Secure outside local/test environments. The login endpoint is throttled through Redis; if throttling is unavailable, login fails closed. Health responses expose status rather than credentials or connection strings.

Phase 7 public submissions use signed anonymous cookies, a persistent idempotency record, Redis/SQL admission limits, a queue cap seeded into the published budget policy, and the existing OpenRouter daily ledger. The V2 API returns only allowlisted report/graph data and native token totals, never public dollar costs. The unlisted report token is 256-bit secret-derived, stored only as a keyed hash, recoverable to the owner through status, and revocable by the admin. Public URLs are excluded from access logs. Run `python scripts/check_phase7.py` from the repository root for isolated migration, API, security, mocked workflow, and Compose checks.

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

With Docker Desktop running, execute the Phase 11 clean-environment gate from the repository root:

```bash
python scripts/check_phase11.py
```

The checker generates isolated credentials, migrates empty-to-head and downgrade/re-upgrade, runs the full backend and browser suites, exercises the root rollback and compatibility adapter, evaluates a shortened test observation, and proves every mocked inference dispatch uses exactly `deepseek/deepseek-v4-flash`. It scans logs and mock history for live endpoints, paid calls, secret exposure, and other inference models, then removes its generated environment, results, containers, and volumes.

Production cutover evidence remains a separate operational requirement. Phase 12 requires one passed non-test observation lasting at least 24 hours with at least 20 eligible terminal runs and five compatibility requests. See [the Phase 11 cutover runbook](../docs/operations/phase11-cutover.md).
