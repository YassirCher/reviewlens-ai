# Architecture

## Frontend
- Next.js 16 App Router
- React 19
- TypeScript
- Tailwind CSS 4
- SSE client for live analysis progress

## Backend
- FastAPI
- Pydantic v2
- httpx
- youtube-transcript-api

## Runtime pipeline

```text
Search form
  -> FastAPI SSE endpoint
  -> YouTube search + metadata
  -> relevance filtering / view ranking
  -> transcript fallback selection until 3 usable videos
  -> optional comment retrieval
  -> 3 structured per-video AI calls
  -> 1 structured consensus call
  -> deterministic consensus fallback if final AI call fails
```

AI providers are behind an OpenAI-compatible adapter:
- OpenRouter (`https://openrouter.ai/api/v1`)
- xAI (`https://api.x.ai/v1`)
- optional OpenAI

The LLM never controls arbitrary browsing/tools. It only interprets supplied text.
