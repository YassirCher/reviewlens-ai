# Phase 12 retirement and rollback runbook

This runbook applies when deploying Phase 12 over an operating Phase 11 environment. The production observation and attestation are not prerequisites for running, testing, or contributing to the project locally.

## Preconditions

Retirement requires all of the following in the authoritative production database:

1. A passed Phase 11 observation with `test_evidence=false` and the production thresholds.
2. A stable window of at least 24 hours, at least 20 eligible terminal runs, and at least five compatibility requests.
3. Failure rate at or below 10%, p95 latency at or below 15 minutes, complete central claim evidence linkage, and compatibility mapping success at or above 95%.
4. No pending or unreconcilable usage and no run or public daily budget breach.
5. No rollback recorded for the observation.
6. At least 24 hours since both the observation ended and the last compatibility request.
7. An operator attestation reference confirming known consumers migrated. The reference may identify a change record or ticket and must contain no secret.

## Export evidence

Run against the production database from the release checkout:

```bash
cd backend
python -m app.cli phase12-export-cutover-evidence \
  --observation-id OBSERVATION_UUID \
  --attestation-reference change/REFERENCE \
  --output ../docs/release-evidence/phase12-cutover.json
```

The command is read only with respect to the database. It writes a sanitized document containing aggregate observation data, compatibility timing, safe audit identifiers, the attestation reference, and a canonical SHA-256 digest. It never exports prompts, source bodies, credentials, cookies, provider parameter values, or raw compatibility input.

Run the evidence check against the exported final observation result:

```bash
python scripts/check_phase12.py --evidence-only
```

## Deploy and verify

1. Take a PostgreSQL backup and record the Phase 11 and Phase 12 application image digests.
2. Run `python scripts/check_phase12.py --evidence-only`, then `python scripts/check_phase12.py --full`, from a clean checkout with Docker and Chromium available.
3. Deploy the Phase 12 image without changing or downgrading the database.
4. Confirm `/health/live` and `/health/ready`.
5. Confirm `/`, owner progress, an unlisted report, and the protected admin overview.
6. Confirm `/research` returns `308` to `/`.
7. Confirm `/health`, `/api/config`, `/api/analyze`, and `/api/analyze/stream` return `404`.
8. Confirm `GET /api/v2/admin/cutover` is authenticated and read only.
9. Inspect worker and scheduler heartbeats, queue age, usage reconciliation, projection backlog, Markdown reconciliation, catalog freshness, and public budget alerts.

## Rollback

Rollback is an application deployment operation:

1. Enable the admin kill switch to stop new paid dispatch.
2. Deploy the recorded Phase 11 application image.
3. Do not downgrade the database. Migration `20260920_0009` and all compatibility/cutover history remain valid for the Phase 11 image.
4. Verify process health, V2 public and admin paths, queued work, usage reconciliation, and compatibility paths.
5. Restore admission only after the failure is understood and the worker queue is healthy.

If Phase 12 is redeployed after rollback, create a fresh qualifying observation and evidence export. An earlier artifact cannot authorize another retirement.

## Recovery references

- Worker interruption: restart workers, run stale lease recovery, relay the runtime outbox, and verify one terminal attempt per call key.
- Usage: repair OpenRouter credentials or credits, clear the account block, run reconciliation, and require zero pending or unreconcilable events.
- Markdown: quarantine unsafe files, run workspace reconciliation, and verify manifest hashes before rebuilding projections.
- Projections: rebuild Neo4j and Qdrant from PostgreSQL/Markdown authority; do not restore them as primary data.
- Credentials: rotate the affected key, restart dependent processes, audit redacted events, and rerun health and mocked acceptance.
