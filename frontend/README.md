# ReviewLens frontend

Next.js 16 and React 19 frontend for the V2 public research flow and protected admin cockpit.

## Routes

- `/` — research intake
- `/research` — permanent `308` redirect to `/`
- `/analysis/{run_id}` — owner session progress
- `/r/{public_token}` — unlisted, uncached report
- `/r/{public_token}/evidence` — bounded evidence graph with a keyboard friendly list alternative
- `/admin` — authenticated operations and LLMOps control plane

The retired V1 UI and provider selector are not bundled.

## Run

```bash
cd frontend
npm ci
npm run dev
```

`NEXT_PUBLIC_API_BASE_URL` is the browser origin for credentialed V2 requests. `V2_API_INTERNAL_URL` is server only and used for report rendering; Compose sets it to `http://api:8000`. Provider credentials never enter the browser bundle.

## Verify

```bash
npm run lint
npm run test:unit
npm run build
npm run test:e2e
```

Browser tests use local mocks. Repository wide verification is `python scripts/check_phase12.py --full` from the repository root.
