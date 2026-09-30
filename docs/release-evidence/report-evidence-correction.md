# Report evidence correction — 2026-09-30

Run `c74ab5c2-284a-46c3-81f8-411eab21f537` published a partial report with five analyzed sources, 82,264 recorded tokens, and eleven chat calls. The curator's first call failed lineage validation and retried. The first audit pruned five compound findings; optional product details were absent. Completed records and their immutable snapshots are preserved.

## Implemented

- Successor `curate_knowledge` is a deterministic one-attempt projection. Only exact case/whitespace-normalized claims merge. Source-specific and opposing statements, independent channels, directed lineage, and evidence support/contradiction edges remain traceable. Stable IDs and transactional writes allow idempotent replay. Legacy model-curator snapshots remain executable.
- Successor synthesis receives one short-reference evidence catalog and returns at most twelve atomic assertions. Code resolves UUIDs and source ownership, rejects unknown references, and renders unchanged report fields. Numeric validation treats attached units as quantities and product codes as scope. The auditor checks meaning, polarity, conditions, attribution, and unsupported scope without a permissive instruction override.
- Product validation shares the review quote matcher: exact adjacent-caption word sequences, at most 45 seconds, within the existing timestamp tolerance. Optional items remain independently rejectable. Bounded rejection codes/counts are internal; raw-response retention remains off.
- Full coverage with audit warnings uses an evidence-warning notice and badge. Source shortfalls retain their existing notice; public warning codes render as readable text.
- Five-source comments-off runs use nine normal model calls. The existing one correction and one re-audit limit remains, and failed final audits cannot publish.

## Captured regression evidence

`backend/tests/fixtures/report_correction_5_sources.json` contains sanitized structured outputs, stored captions and metadata, baseline contracts, and the successful graph proposal from the run. No provider credentials or raw model responses are retained.

The captured quotes for **3.2 g**, **10%**, **30 hours**, and Bluetooth **5.3** span caption boundaries. They reproduce rejection by the old single-segment product predicate and pass the shared bounded matcher. Foreign quotes, distant timestamps, unsupported values, and sibling-model scopes fail independently. The exact reasons for all 37 historical fact rejections were not retained and cannot be reconstructed.

## Offline paired gate

Run `backend/.venv/Scripts/python.exe scripts/check_report_correction_budget.py`. Twenty matched captured-input variants rotate source order and optional-item defects; five cases include correction and re-audit. Seven cases include a baseline curator retry. Each timing is the median of three alternating replays.

Common-call token costs use captured usage. Changed-call estimates include prompt, response schema, and fixture completion. Local timing includes application processing and a fixed 2 ms fixture-provider delay; it excludes database, queue, real network, and remote inference. Product retention and buying-topic coverage use captured verified quotations and annotated fixture responses. These are deterministic regression checks, not a production semantic-model evaluation.

| Measure | Baseline | Successor |
|---|---:|---:|
| Analyzed sources per case | 5 | 5 |
| Normal model calls | 10, or 11 with curator retry | 9 |
| Retained captured product facts | 0 | 4 |
| Mean estimated total tokens | 73,212.35 | 56,330.00 |
| p95 estimated total tokens | 94,204 | 69,035 |
| p95 local replay completion | 151.69 ms | 123.94 ms |

All strict checks passed: source and buying-topic coverage, quotation validity, unsupported-claim annotations, product retention, total tokens, bounded normal/repair calls, and p95 local time. Repair work is included in every performance measurement.

**Paid verification spend is zero. Actual production token and latency parity remains unverified.**

## Verification and rollout

Local backend tests: **218 passed**, 58 integration cases skipped outside Compose. Full backend Ruff and the thirteen-file acceptance type-check target passed, along with scoped type checks for synthesis, graph projection, and product validation. Frontend lint, TypeScript, twelve unit tests, and the production build passed. The context atlas audit and whitespace check passed.

Mocked browser acceptance: **26 passed**, including full-coverage audit warnings, source-shortfall wording, readable codes, omitted optional details, accessibility, and admin journeys.

Isolated stack acceptance: **272 passed**, four host-only gate tests skipped; **three stack browser tests passed**. Migration, backup/restore, dependency outages/recovery, allowlisted local provider fixtures, and credential checks passed. Workers recorded **zero curator calls** and **zero separate product-information calls**. Replaying graph projection changed neither nodes, versions, nor edges; a later invalid-lineage item rolled back prior writes without requesting a retry.

The three repair drills recorded actual chat usage: successful correction and re-audit made **11 total calls** and published; failed re-audit made **11** and published no report; malformed correction made **10** with no re-audit and published no report. All five sources survived and review contexts remained assigned-source-only and duplicate-free.

The first isolated attempt failed during Neo4j startup. A retry started successfully. Acceptance also caught the new deterministic handler missing from the admin workflow validator; registration was corrected before the successful full gate. The existing admin versions test was updated to select its current tab.

Rebuilt and restarted the main Compose application with its existing volumes. All eight services are healthy; frontend `/` returned HTTP 200 and API `/health/ready` reported `ready`. Active workflow and agent schemas/prompts match the checked-in successors, including one-attempt deterministic graph projection and one-attempt correction/re-audit. Saved model selections are preserved.

Verified active versions: workflow **20**, Review Analyst **13** (`VideoExtraction`), Consensus Analyst **12** (`AtomicBuyingSynthesis`), and Quality Auditor **12** (`AuditResult`).

Historical run `c74ab5c2-284a-46c3-81f8-411eab21f537` remains partial with 82,264 recorded tokens, five sources, seven retained findings, and no product-information card. Its report hash verifies and its immutable snapshot still refers to the previous workflow. New runs receive the corrected configuration. No paid fresh analysis was submitted.
