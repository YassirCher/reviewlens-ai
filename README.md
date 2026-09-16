# ReviewLens — Product Review Intelligence POC

> **Implementation status:** The public product remains the legacy V1 proof of concept described below. The decision-complete Target V2 rebuild specification is in [context/00_INDEX_AND_PROJECT_OVERVIEW.md](./context/00_INDEX_AND_PROJECT_OVERVIEW.md). The current-to-target source atlas is in [context/codebase/00_CODEBASE_MAP.md](./context/codebase/00_CODEBASE_MAP.md). Open the repository root as an Obsidian vault to navigate both. V2 Phases 1 through 6 now provide the platform, durable execution, OpenRouter/LLMOps, reconstructable context, typed YouTube research tools, and an internal bounded multi-agent workflow; the V1 public flow remains the default.

The V2 foundation now exists beside V1: PostgreSQL/Alembic persistence, Redis/Celery processes, secure admin sessions, durable run/task/attempt state, exact OpenRouter accounting, an authoritative Markdown/PostgreSQL context graph, fixed typed research tools, and a seven-role internal analysis DAG. Phase 6 snapshots every prompt/schema/policy/tool version, bounds source fan-out and correction attempts, validates central evidence and deterministic scores, and publishes immutable internal reports only after audit gates pass. Public V2 analysis/report/SSE routes and the V2 frontend remain Phase 7 work.

ReviewLens is a full-stack proof of concept that turns the top YouTube reviews for a product into a structured, evidence-backed buying decision.

The user enters a product name, optionally enables YouTube comment analysis, and ReviewLens:

1. Searches YouTube for relevant product review videos.
2. Filters obvious non-review content.
3. Selects up to three of the most-viewed review candidates with usable transcripts.
4. Extracts timestamped transcripts.
5. Optionally retrieves top/relevant YouTube comments.
6. Uses an AI provider to analyze each video into a strict schema.
7. Uses a final consensus pass to produce an overall buy / caveat / mixed / avoid verdict.

## Legacy V1 stack

- **Frontend:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4
- **Backend:** FastAPI, Pydantic v2, httpx
- **YouTube:** YouTube Data API v3
- **Transcripts:** `youtube-transcript-api`
- **AI providers:** OpenRouter, xAI/Grok, optional OpenAI-compatible adapter
- **Storage:** none required for V1

## Legacy V1 free-tier strategy

The default OpenRouter model is `openrouter/free`. Comments are **off by default**. When comments are enabled, they are included in the same per-video LLM request, so the normal budget remains roughly:

- 3 per-video AI calls
- 1 final consensus AI call
- **~4 AI calls per analysis**

The backend also includes a deterministic final-consensus fallback if the aggregation call fails.

## Quick start

### 1. Configure environment

```bash
cp .env.example .env
```

Set at minimum:

```env
YOUTUBE_API_KEY=...
OPENROUTER_API_KEY=...
```

xAI/Grok is optional:

```env
XAI_API_KEY=...
XAI_MODEL=grok-4.5
```

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

## Docker

After creating `.env`, configure the required V2 database, Redis, Neo4j, admin, hashing secrets, and runtime operations settings documented in `.env.example`. Generate the admin password hash without placing a plaintext password in `.env`:

```bash
cd backend
python -m app.cli hash-password
cd ..
```

Then start the complete local stack:

```bash
docker compose up --build --wait
```

Frontend: `http://localhost:3000`  
Backend docs: `http://localhost:8000/docs`

Platform endpoints:

- `GET /health` — stable V1 liveness response
- `GET /health/live` — process liveness
- `GET /health/ready` — PostgreSQL/Redis/storage readiness plus projection degradation
- `/api/v2/admin/session` and `/api/v2/admin/csrf` — secure admin-session foundation
- `GET /api/v2/admin/system/health` — authenticated dependency detail

Publish the checked-in Phase 6 model/agent/workflow configuration only after the current OpenRouter catalog and endpoint snapshots are available:

```bash
cd backend
python -m app.cli openrouter-catalog-refresh
python -m app.cli analysis-config-seed
```

`V2_AGENT_CHAT_MODELS` defaults to `deepseek/deepseek-v4-flash` and is separate from the V1-only `OPENROUTER_MODEL`. The workflow allows 3–8 sources, uses the configured worker concurrency, permits one schema-correction attempt per agent task, enforces a bounded run deadline, and publishes a partial report only when at least one validated source and central evidence survive the audit gates.

Run the isolated Phase 6 migration cycle, full backend suite, local OpenRouter/YouTube mocks, seven-agent fixtures, and full-stack verification with Docker Desktop running:

```bash
python scripts/check_phase6.py
```

The checker uses a generated `APP_ENV=test` file and fake upstream keys. It exercises complete, comments, partial, retry, correction, audit-failure, and cancellation paths against isolated storage. It never reads the repository `.env`, contacts live YouTube/OpenRouter, or spends credits.

## Important behavior

- API keys are server-side only.
- Transcripts and comments are treated as untrusted content for prompt-injection resistance.
- Missing transcripts cause the system to try the next ranked candidate.
- Partial results are preferred to complete failure.
- The UI never claims a product fact that is not present in the analyzed sources.

## Project documentation

The authoritative V2 conception is under [context/](./context). Start with [context/00_INDEX_AND_PROJECT_OVERVIEW.md](./context/00_INDEX_AND_PROJECT_OVERVIEW.md), follow its reading order, and read [context/25_AGENT_BUILD_INSTRUCTIONS.md](./context/25_AGENT_BUILD_INSTRUCTIONS.md) before implementation.

Validate the context metadata, links, Canvas, code maps, terminology, and V1/V2 boundary from the repository root:

```bash
python scripts/check_context.py
```
