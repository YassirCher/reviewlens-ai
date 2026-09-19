# ReviewLens Phase 10 operations runbook

Use these procedures with the deployed environment's secret manager and Compose project name. Never paste credential values into commands, tickets, or logs. The Phase 10 acceptance checker performs the restore and recovery drills with generated test credentials only.

## Backup and restore

1. Quiesce public admission with the admin kill switch while allowing active work to reach a terminal state.
2. Create encrypted PostgreSQL, Redis, Neo4j, Qdrant, Markdown workspace, and quarantine backups using the platform backup service. Record the active configuration IDs and migration head beside the backup manifest.
3. Restore into an isolated environment first. Run `alembic current`, verify the active configuration IDs, reconcile Markdown, rebuild Neo4j and Qdrant, and reconcile usage before opening traffic.
4. The acceptance gate validates a PostgreSQL custom format dump by restoring it into a second database and checking its migration head.

## Kill switch

Open `/admin/settings`, select the emergency kill switch, type the displayed confirmation phrase, and submit. The action is authenticated, CSRF protected, and audited. Existing attempts can finish; new model reservations are refused. Restore service from the same control only after dependency health and account state are healthy.

## Worker interruption and queue recovery

1. Confirm the `worker_unavailable`, `scheduler_heartbeat_stale`, or `queued_work_stale` alert in `/admin`.
2. Restore Redis and worker connectivity, then restart the worker with the same queue and visibility timeout configuration.
3. The scheduler dispatches `reviewlens.runtime.recover`. It expires stale attempt and tool leases, retries within the immutable snapshot's limits, repairs unfinished runs, and safely reclaims outbox work.
4. Inspect `/admin/runs` and the audit history. Do not manually rewrite attempt state.

## Redis stream gap and outages

Clients reconnect with their last committed event ID. If the Redis stream no longer contains that sequence, the API reconstructs the current state from PostgreSQL and resumes with a fresh stream position. During a Redis outage, public admission fails closed and durable PostgreSQL state remains authoritative. Restore Redis, then run the worker recovery procedure.

## Usage reconciliation

The scheduler runs `reviewlens.llmops.reconcile_usage`. For events that lack final usage, it queries the mocked or configured generation endpoint by generation ID and reconciles reservations transactionally. Investigate `usage_reconciliation_stale` after 15 minutes. Never edit final usage rows; database triggers enforce immutability.

## Markdown reconciliation

The scheduler runs `reviewlens.context.reconcile_markdown`. Invalid, missing, linked, or hash mismatched Markdown is quarantined and the workspace is marked degraded. Review `/admin/knowledge`, repair the authoritative source, and rerun reconciliation. Do not copy a quarantined file back without validating its frontmatter, extension, ownership, and hash.

## Neo4j and Qdrant rebuild

Use `/admin/knowledge` to enqueue the target rebuild. The job reads versioned PostgreSQL nodes, edges, and manifests and replaces the projection idempotently. PostgreSQL and Markdown remain authoritative. Verify that the workspace target state returns to `ready` before closing the alert.

## Credential and payment recovery

For `openrouter_authentication_blocked`, rotate the OpenRouter credential in the secret manager, restart the affected processes, and use the account reset command only after the new credential is deployed. For `openrouter_payment_blocked`, restore account credit before resetting the block. Rotate YouTube, database, Redis, Neo4j, Qdrant, session, and hashing secrets with their service specific procedures; revoke exposed sessions after rotating the session secret.

## Application rollback

1. Enable the kill switch and preserve the current database, configuration IDs, and workspace backup.
2. Deploy the previous tested application image. Phase 10 adds no database migration, so the Phase 9 schema remains compatible.
3. If a later migration is present, run only its checked in Alembic downgrade after restoring a verified backup.
4. Rebuild projections, reconcile usage and Markdown, verify `/health/ready`, `/api/v2/admin/system/health`, V1 `/api/config`, and the public/admin smoke journeys, then disable the kill switch.
