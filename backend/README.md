# ReviewLens backend

FastAPI backend for the durable V2 research runtime and protected admin control plane. All product APIs live under `/api/v2`. The retired V1 routes return `404`; compatibility telemetry and cutover observations remain queryable as historical data.

## Setup

From the repository root:

```bash
python -m venv backend/.venv
# Windows: backend/.venv/Scripts/activate
# macOS/Linux: source backend/.venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cp .env.example .env
cd backend
python -m app.cli hash-password
alembic upgrade head
python -m app.cli seed
python -m app.cli validate api
uvicorn app.main:app --reload --port 8000
```

Store only the Argon2id hash in `ADMIN_PASSWORD_HASH`. Generate independent values of at least 32 random bytes for `SESSION_SECRET`, `PUBLIC_TOKEN_HASH_SECRET`, and `RATE_LIMIT_HASH_SECRET`.

## Runtime contracts

- `GET /health/live` and `GET /health/ready`
- `/api/v2/analyses` preflight, idempotent creation, owner status, resumable events, and cancellation
- `/api/v2/reports/{public_token}` and its bounded evidence graph
- `/api/v2/admin/session`, CSRF, system health, runs, configuration, catalog, tools, knowledge, analytics, settings, jobs, recovery, and audits
- authenticated read-only `GET /api/v2/admin/cutover`

Anonymous ownership cookies are HttpOnly, SameSite=Lax, Secure outside local/test, and scoped to `/api/v2`. Admin mutations require the authenticated cookie and CSRF token. Public serializers do not expose cost, prompts, policy details, credentials, or internal graph content.

Published configuration is immutable. Each run snapshots its workflow, agents, policies, tools, and limits before dispatch. PostgreSQL owns run and budget truth; Redis and Celery provide delivery; Markdown/PostgreSQL own graph truth; Neo4j and Qdrant are rebuildable projections.

## Operator commands

```bash
python -m app.cli openrouter-catalog-refresh
python -m app.cli analysis-config-seed
python -m app.cli openrouter-reset-account
```

Live OpenRouter and YouTube smoke commands remain disabled unless their explicit opt-in flags and confirmations are supplied. Automated verification never invokes them.

Export Phase 12 retirement evidence with:

```bash
python -m app.cli phase12-export-cutover-evidence \
  --observation-id OBSERVATION_UUID \
  --attestation-reference change/REFERENCE \
  --output ../docs/release-evidence/phase12-cutover.json
```

This command reads the authoritative database and refuses test evidence, weakened thresholds, failed metrics, rollback state, unresolved usage, budget breaches, insufficient samples, or a compatibility quiet period below 24 hours.

## Verification

```bash
python -m pytest tests -q
python ../scripts/check_phase12.py --static-only
python ../scripts/check_phase12.py --stack-only
```

The isolated stack routes all inference through local mocks and accepts only `deepseek/deepseek-v4-flash` and `deepseek/deepseek-v4-flash-0731`.
