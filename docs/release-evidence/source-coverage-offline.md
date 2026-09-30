# Source coverage verification — 2026-09-30

Implemented seven-day verified caption reuse, hard exclusions and unique source slots, source IDs assigned in code, compact combined review/product extraction, deterministic product projection, and legacy snapshot adapters. Public report contracts and publication gates remain intact.

## Evidence

- Local backend tests: **204 passed**, 58 integration tests skipped outside Compose.
- Isolated Phase 12 acceptance: **259 passed**, 3 host-only tests skipped; **3 browser tests passed**. Migration, backup/restore, dependency recovery, credential checks, and provider allowlist checks passed with local upstream mocks.
- Workers analyzed five sources, made **10 normal chat calls**, and made **zero separate product-information calls**. Review manifests contained only assigned source metadata and distinct chronological chunks.
- Recoverable audit fixtures recorded one actual correction and one actual re-audit call, for **12 total calls**. A failed final re-audit published no report.
- Caption tests cover reuse, expiry, corruption, request language/fallback/origin matching, verified bootstrap, fresh graph identities and lineage, and blocked uncached acquisition. Review tests cover assigned identities, foreign quotations, independent optional-detail rejection, compact claims, and old/new output contracts.
- Scoped Ruff checks and type checks for the new review/cache contracts, gateway, and evaluation passed. The context atlas audit passed.

## Offline paired budget gate

Run `backend/.venv/Scripts/python.exe scripts/check_source_coverage_budget.py` and inspect `.audit-cache/source-coverage/result.json`, `baseline.jsonl`, and `candidate.jsonl`. The strict comparison includes all correction and re-audit work and requires at least twenty matched cases.

The final replay used twenty matched local fixture cases, including five audit repair cases, strict response-schema validation, exact quote checks, and product validation. Timing is the median of three alternating in-process replays, with a fixed 2 ms local fixture provider delay. It excludes database, worker queue, real network, and remote inference timing. Token estimates include prompt, response schema, and serialized completion; they are not provider usage records.

| Measure | Baseline | Successor |
|---|---:|---:|
| Valid sources per case | 2 / 5 | 5 / 5 |
| Normal model calls | 11 | 10 |
| Mean estimated total tokens | 55,926.85 | 52,510.25 |
| p95 estimated total tokens | 63,736 | 62,597 |
| p95 local replay time | 171.36 ms | 141.14 ms |
| Valid retained quotations | 100% | 100% |
| Unsupported retained claims | 0% | 0% |
| Fixture buying-topic coverage | 100% | 100% |

All strict gate checks passed. **Actual production token and latency parity remains unverified. Paid verification spend was zero.**

## Relaunch and retained history

Rebuilt and relaunched the existing Compose stack. Frontend `/` returned HTTP 200; API readiness returned `ready`; all services became healthy. The active workflow uses deterministic product projection.

Verified and bootstrapped these eligible Blackshark T11 captions without fetching videos or invoking a model: `9iLTdMCz6Ss`, `E_nBOaQA_qQ`, `Ie2XDAnhJ1o`, `qr_n9Tam1jc`, and `hP4lKJa5P_k`. Original recorded fetch age and language provenance were retained.

Historic run `7b7c780d-eecc-457e-b21f-41f339387a0e` remains partial with two analyzed sources and 83,309 tokens. New runs use the successor configuration; old run snapshots are immutable. Uncached caption requests can still be blocked by YouTube and produce partial coverage.
