# ReviewLens Backend

FastAPI backend containing the legacy V1 analysis flow plus the Phase 1 platform foundation and Phase 2 durable V2 execution backbone.

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

The Compose stack runs the migration/seed as a one-shot dependency and launches the same image as separate API, Celery worker, and Celery scheduler processes. Published configuration versions and audit events are immutable at the PostgreSQL layer. Durable V2 runs snapshot the active published workflow and budget, then persist DAG tasks, attempts, cancellation, retries, progress events, and outbox state before Redis/Celery delivery.

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

## Verification

```bash
python -m pytest tests -q
```

With Docker Desktop running, execute the isolated empty-database, auth, durable worker/runtime, and full-stack suite from the repository root:

```bash
python scripts/check_phase2.py
```
