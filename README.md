# ReviewLens — Evidence backed product research

ReviewLens is a full stack research system that turns several YouTube reviews into a durable, evidence linked buying report. The V2 research experience is the only runtime: `/` accepts research requests, `/analysis/{run_id}` shows owner progress, `/r/{public_token}` serves unlisted reports, and `/admin` hosts the protected operations cockpit. `/research` permanently redirects to `/`.

The legacy V1 UI, provider selection, direct provider clients, `/api/config`, `/api/analyze`, `/api/analyze/stream`, and `GET /health` have been retired. Historical compatibility requests and Phase 11 cutover observations remain in PostgreSQL as immutable operational history.

## System

- Next.js 16 and React 19 public and admin interfaces
- FastAPI V2 API with strict JSON, body limits, CORS, security headers, signed owner sessions, and CSRF protected admin mutations
- PostgreSQL and Alembic for runs, snapshots, configuration, budgets, usage, audits, graph metadata, compatibility history, and cutover observations
- Redis and Celery for queues, progress streams, scheduling, leases, and recovery
- OpenRouter policy routing with exact usage attribution and reconciliation
- YouTube typed tools with bounded quota, transcript fallback, optional comments, and untrusted content handling
- Markdown and PostgreSQL authoritative context with Neo4j and Qdrant rebuildable projections
- A seven role analysis DAG with schema correction, evidence gates, and partial result rules

Production defaults to `deepseek/deepseek-v4-flash`. Phase 12 acceptance permits only that pinned slug and `deepseek/deepseek-v4-flash-0731`, both through the local OpenRouter mock.

## Local setup

Copy `.env.example` to `.env`, set the required infrastructure and server side credentials, then generate the admin password hash:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m app.cli hash-password
cd ..
```

Start the stack:

```bash
docker compose up --build --wait
```

The frontend is at `http://localhost:3000`; API documentation is at `http://localhost:8000/docs`. Process health is available at `/health/live` and `/health/ready`. Authenticated dependency detail is at `/api/v2/admin/system/health`.

Refresh the OpenRouter catalog and seed the initial published workflow before accepting submissions:

```bash
cd backend
python -m app.cli openrouter-catalog-refresh
python -m app.cli analysis-config-seed
```

## Optional production retirement authorization

Export evidence only from the authoritative production database after a passed non-test Phase 11 observation, the 24 hour compatibility quiet period, reconciled usage, no budget breach, and operator migration attestation:

```bash
cd backend
python -m app.cli phase12-export-cutover-evidence \
  --observation-id OBSERVATION_UUID \
  --attestation-reference change/REFERENCE \
  --output ../docs/release-evidence/phase12-cutover.json
```

The artifact stores sanitized aggregates and a canonical SHA-256 digest. See [the Phase 12 runbook](./docs/operations/phase12-retirement.md).

## Acceptance

The current gate replaces the superseded per-phase checkers:

```bash
python scripts/check_phase12.py --evidence-only
python scripts/check_phase12.py --static-only
python scripts/check_phase12.py --stack-only
python scripts/check_phase12.py --browser-only
python scripts/check_phase12.py --full
```

`--full` is the complete working-project gate: static checks, the isolated stack, and browser acceptance. The separate `--evidence-only` mode validates production retirement authorization when ReviewLens is deployed over a real Phase 11 installation; it is not required for local development or repository CI.

The stack gate creates isolated credentials and storage, migrates from empty state, cycles migration `20260920_0009`, runs backup and recovery drills, executes the complete backend and browser suites, and inspects mock history. It fails on a live provider endpoint, secret exposure, or inference outside the two approved Flash slugs, and always removes its generated environment, containers, volumes, and reports.

## Documentation

Start with [the V2 context home](./context/00_INDEX_AND_PROJECT_OVERVIEW.md) and [the codebase map](./context/codebase/00_CODEBASE_MAP.md). The final specification matrix is [Phase 12 conformance](./docs/release-evidence/phase12-conformance.md).

Validate the local context vault with:

```bash
python scripts/check_context.py
```
