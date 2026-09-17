# ReviewLens Frontend

Next.js 16 / React 19 frontend. `/` remains the legacy V1 product page; `/research` previews the separate V2 evidence-led flow.

## Run

```bash
cd frontend
npm install
npm run dev
```

The default backend base URL is `http://localhost:8000`. Override with:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
V2_API_INTERNAL_URL=http://localhost:8000
```

`NEXT_PUBLIC_API_BASE_URL` is the browser origin for credentialed V2 preflight, submission, owner status, SSE, and cancellation. `V2_API_INTERNAL_URL` is server-only and used for uncached, unlisted report pages; Compose sets it to `http://api:8000`. The browser never receives YouTube or AI provider API keys.

V2 routes: `/research` (3–8 sources, comments off by default), `/analysis/{run_id}` (owner session), `/r/{public_token}` (revocable report), and `/r/{public_token}/evidence` (bounded graph plus keyboard-friendly list). Reports are marked noindex/no-store; V1 analysis and provider selection remain confined to `/`.

Run local checks with `npm run lint`, `npx tsc --noEmit`, `npm run test:unit`, and `npm run test:e2e`. Browser tests start a local mock API and do not use `.env` or paid providers. The repository-wide isolated Compose/browser verification command is `python scripts/check_phase8.py`.
