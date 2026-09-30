# Report audit repair — 2026-09-30

## Captured failure

Run `e2d5b40e-a9d4-4e9d-b854-3f92fc2d9972` retained all five reviews and recorded 100,781 tokens. Its first audit rejected the report. Correction received issues and source analyses but omitted the rejected draft, and returned a summary with no structured findings. Re-audit failed and publication correctly stopped with `report_audit_failed`.

The stored analyses also showed 0–3 point scores despite the intended 0–100 scale. The auditor prompt did not explain already validated quotation lineage, combined cited excerpts, translated wording, or narrative fields without inline evidence-ID fields. Some initial findings legitimately combined quantities that their citations did not support.

## Changes

- A successor consensus contract receives `report_under_repair` and requires 1–12 cited strengths/caveats. Backend code adapts it to the unchanged report shape and binds product identity. Known adapters retain legacy snapshot input/output contracts.
- Auditor instructions resolve citations within validated analyses, combine excerpts per source, distinguish stated specifications from observed tests, and handle narrative fields, translations, and null optional values. Review instructions explicitly use integer 0–100 points and require support for each claim clause.
- Deterministic quantity/scope checks combine supporting excerpts within each source; another source cannot fill missing support. Publication still blocks failed final audits.
- Correction and re-audit each have one task attempt, including schema failures. No model call or token ceiling was added. Successor schemas appear once through response_format.

## Verification

- Local backend suite: **210 passed**, 58 stack tests skipped.
- Deterministic Phase 12 stack acceptance: **265 passed**, 3 host-only tests skipped; **3 browser tests passed**. Local provider fixtures only, zero paid calls.
- Worker drills verified five valid sources, isolated chronological review context, zero separate product extraction calls, and one actual correction plus one actual re-audit (**12 total calls**) for successful repair and failed final audit cases. A schema-invalid empty correction recorded one correction, zero re-audits (**11 total calls**), and no publication.
- Captured regression fixtures cover empty correction rejection, narrowed findings, same-source multi-quote quantities, rejection of borrowed quantities, old snapshot adapters, and bounded repair attempts. Scoped Ruff/type checks and the context atlas audit passed.
- The strict twenty-case offline coverage gate, including five repair cases, passed every evidence, coverage, call, token, and timing check. Mean estimated tokens: **55,926.85 → 51,183.25**; p95 estimated tokens: **63,736 → 60,553**; p95 local replay time: **145.27 ms → 134.14 ms**. These are fixture estimates and in-process timings, not production measurements.

## Relaunch and limits

Docker Desktop startup was additionally blocked by inaccessible zero-byte inference and Secrets Engine sockets. Their transient directories were preserved under sibling `*.stale.20260930*` names; no factory reset or container-volume deletion was performed on the main project.

The main Compose stack was rebuilt and relaunched. Frontend `/` returned HTTP 200 and API readiness returned `ready`. Active review, consensus, and auditor prompts, schemas, retrieval settings, and execution limits match the tested successor specifications. The old failed run remains failed, unpublished, with 100,781 tokens and its original FinalReportDraft contract.

Start a new analysis to use the successor configuration. Paid verification spend was zero; actual production token/latency parity and live model behavior remain unverified. Uncached YouTube captions can still be blocked upstream.
