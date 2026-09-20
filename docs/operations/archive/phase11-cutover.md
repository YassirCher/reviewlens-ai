# Archived Phase 11 cutover runbook

This document records the retired compatibility period. Its commands and rollback switch do not apply to the Phase 12 runtime. Use [the Phase 12 retirement runbook](../phase12-retirement.md).


Use this runbook for the V2 root cutover, temporary V1 compatibility, stable-window observation, and presentation rollback. Store credentials in the deployment secret manager. Do not paste secret values, source content, prompts, cookies, or tokens into commands, tickets, or logs.

## Configuration

Phase 11 introduces these controls:

- `PUBLIC_ROOT_EXPERIENCE=v2|v1` selects the root presentation at request time. It defaults to `v2` and requires a frontend restart after a change.
- `LEGACY_ANALYSIS_ADAPTER_ENABLED` enables the temporary `/api/analyze` and `/api/analyze/stream` adapter. Disabling it returns `410`.
- `LEGACY_ADAPTER_WAIT_SECONDS` bounds the synchronous compatibility wait.
- `LEGACY_API_SUNSET_AT` optionally emits an HTTP `Sunset` header. Leave it unset until an actual retirement date is approved.
- `CUTOVER_OBSERVATION_*` settings define production duration, sample, failure, latency, and mapping thresholds. The service snapshots these values when an observation starts.

The active model remains `deepseek/deepseek-v4-flash`. Browsing the model catalog does not change routing. Phase 11 automated inference must use the local OpenRouter mock and rejects any chat or embedding request for another model.

## Migration and deployment

1. Enable the kill switch from `/admin/settings` to stop new reservations while active attempts reach a safe boundary.
2. Back up PostgreSQL and record the current Alembic revision and active workflow, model, embedding, and budget version IDs.
3. Deploy the Phase 11 API, worker, scheduler, and frontend images with `PUBLIC_ROOT_EXPERIENCE=v2`.
4. Run `alembic upgrade head`. Revision `20260920_0009` adds compatibility request telemetry and cutover observations without changing existing run or configuration rows.
5. Start the API, worker, scheduler, and frontend. Verify `/health/ready`, authenticated `/api/v2/admin/system/health`, `/`, `/research`, and `/api/config`.
6. Confirm `/` shows V2, `/research` is available and no-indexed, and the legacy adapter returns `Deprecation: true` with the V2 successor link.
7. Disable the kill switch after dependencies, routing policy, budgets, and queue health are ready.

Active and queued runs retain their snapshotted configuration across the deployment and any root presentation change.

## Start and evaluate an observation

1. Sign in to `/admin/settings` or `/admin` and review the cutover panel.
2. Start one observation for the deployment environment. The action requires the displayed confirmation phrase and CSRF token, is audited, and fails if another observation is active.
3. Allow the production window to run for at least 24 hours. Do not tag production evidence as test evidence.
4. Evaluate manually from the cutover panel when investigating, or allow the scheduler task `reviewlens.admin.evaluate_cutover` to update it.
5. Inspect all blockers, sample counts, p50/p95 latency, cost and token totals, compatibility outcomes, evidence linkage, usage reconciliation, and budget state.

A production observation passes only when it has at least 20 eligible terminal runs and five compatibility requests, failure rate at or below 10%, p95 latency at or below 15 minutes, 100% central-claim evidence linkage, compatibility mapping success at least 95%, no pending or unreconcilable usage, and no run or public daily budget breach.

Automated acceptance uses an isolated observation tagged as test evidence and shorter test-only thresholds. Test evidence cannot authorize Phase 12.

## Presentation rollback

1. Enable the kill switch in `/admin/settings` and verify the audited action.
2. Set `PUBLIC_ROOT_EXPERIENCE=v1` in the frontend runtime environment.
3. Restart or recreate only the frontend service. No frontend rebuild or database downgrade is required.
4. Verify `/health/ready`, authenticated system health, `/`, `/research`, `/api/config`, `/api/analyze`, and `/api/analyze/stream`.
5. Confirm `/` shows the V1 presentation while requests still create V2 runs with `provider_used="openrouter"` and `model_used="policy-managed"`.
6. Record the rollback against the active cutover observation with the displayed confirmation phrase and current optimistic version.
7. Restore admission only after the triggering condition is resolved and dependencies, queues, account state, budgets, and usage reconciliation are healthy.

The root switch never changes active or queued runs, immutable snapshots, reports, or usage. Do not downgrade revision `20260920_0009` during a presentation rollback.

## Adapter disablement

Disable `LEGACY_ANALYSIS_ADAPTER_ENABLED` only after consumers have migrated or during a controlled incident response. Verify that legacy analysis endpoints return the safe `410` envelope and that native V2 creation, progress, and report flows remain healthy. Configure `LEGACY_API_SUNSET_AT` only after an approved date exists.

## Queue and worker recovery

Keep the kill switch enabled while recovering stale queued work. Restore Redis and worker connectivity, restart workers with the deployed queue settings, and allow `reviewlens.runtime.recover` to reclaim stale leases and outbox work. Inspect `/admin/runs`, operational alerts, and audit history. Do not rewrite task or attempt rows manually.

## Phase 12 authorization

Before removing the adapter, V1 presentation, direct-provider services, or legacy configuration:

1. Select a passed observation whose `test_evidence` value is false.
2. Verify it used the production thresholds and includes the required duration and samples.
3. Confirm no rollback was recorded, no usage remains pending or unreconcilable, and no run or public daily budget breach occurred.
4. Export or record the observation identifier and audit events in the release evidence.
5. Run `python scripts/check_phase11.py` and the Phase 12 conformance gate from a clean environment.

Without this evidence, keep the compatibility boundary and rollback controls deployed.

## Acceptance commands

Run from the repository root:

```bash
python scripts/check_phase11.py --static-only
python scripts/check_phase11.py --stack-only
python scripts/check_phase11.py --browser-only
```

The full gate is `python scripts/check_phase11.py`. It generates isolated credentials, uses only local upstream mocks, checks migration downgrade/re-upgrade and root rollback, asserts DeepSeek-only inference, scans for live or paid traffic and secret exposure, and removes generated environments, results, containers, and volumes after success or failure.
