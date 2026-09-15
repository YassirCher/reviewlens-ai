# ReviewLens — Product Review Intelligence POC

> **Implementation status:** The code in this repository is the legacy V1 proof of concept described below. The decision-complete Target V2 rebuild specification is in [context/00_INDEX_AND_PROJECT_OVERVIEW.md](./context/00_INDEX_AND_PROJECT_OVERVIEW.md). The current-to-target source atlas is in [context/codebase/00_CODEBASE_MAP.md](./context/codebase/00_CODEBASE_MAP.md). Open the repository root as an Obsidian vault to navigate both. V2 is not yet implemented.

ReviewLens is a full-stack proof of concept that turns the top YouTube reviews for a product into a structured, evidence-backed buying decision.

The user enters a product name, optionally enables YouTube comment analysis, and ReviewLens:

1. Searches YouTube for relevant product review videos.
2. Filters obvious non-review content.
3. Selects up to three of the most-viewed review candidates with usable transcripts.
4. Extracts timestamped transcripts.
5. Optionally retrieves top/relevant YouTube comments.
6. Uses an AI provider to analyze each video into a strict schema.
7. Uses a final consensus pass to produce an overall buy / caveat / mixed / avoid verdict.

## Stack

- **Frontend:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4
- **Backend:** FastAPI, Pydantic v2, httpx
- **YouTube:** YouTube Data API v3
- **Transcripts:** `youtube-transcript-api`
- **AI providers:** OpenRouter, xAI/Grok, optional OpenAI-compatible adapter
- **Storage:** none required for V1

## Free-tier strategy

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

After creating `.env`:

```bash
docker compose up --build
```

Frontend: `http://localhost:3000`  
Backend docs: `http://localhost:8000/docs`

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
