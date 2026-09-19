from __future__ import annotations

import time
import uuid

from celery import Celery
from billiard.exceptions import SoftTimeLimitExceeded

from app.cache import get_redis
from app.config import settings
from app.db.session import session_scope
from app.services.admin_auth import revoke_expired_sessions

celery_app = Celery(
    "reviewlens",
    broker=settings.celery_broker_url or settings.redis_url or None,
    backend=settings.celery_result_backend or None,
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_ignore_result=True,
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": settings.celery_visibility_timeout_seconds,
    },
    worker_prefetch_multiplier=1,
    beat_schedule={
        "platform-scheduler-heartbeat": {
            "task": "reviewlens.platform.scheduler_heartbeat",
            "schedule": 30.0,
        },
        "expire-admin-sessions": {
            "task": "reviewlens.platform.expire_admin_sessions",
            "schedule": 300.0,
        },
        "runtime-outbox-relay": {
            "task": "reviewlens.runtime.relay_outbox",
            "schedule": 2.0,
        },
        "runtime-recovery": {
            "task": "reviewlens.runtime.recover",
            "schedule": float(settings.runtime_recovery_interval_seconds),
        },
        "openrouter-catalog-refresh": {
            "task": "reviewlens.llmops.refresh_catalogs",
            "schedule": 60.0,
        },
        "openrouter-credit-refresh": {
            "task": "reviewlens.llmops.refresh_credit_state",
            "schedule": float(settings.openrouter_credit_refresh_minutes * 60),
        },
        "openrouter-usage-reconciliation": {
            "task": "reviewlens.llmops.reconcile_usage",
            "schedule": float(settings.openrouter_reconciliation_interval_seconds),
        },
        "context-markdown-reconciliation": {
            "task": "reviewlens.context.reconcile_markdown",
            "schedule": float(settings.context_reconciliation_interval_seconds),
        },
        "admin-job-recovery": {
            "task": "reviewlens.admin.recover_jobs",
            "schedule": 60.0,
        },
        "admin-aggregate-reconciliation": {
            "task": "reviewlens.admin.reconcile_analytics",
            "schedule": 300.0,
        },
        "admin-content-expiry": {
            "task": "reviewlens.admin.expire_retained_content",
            "schedule": 300.0,
        },
    },
)


@celery_app.task(name="reviewlens.platform.scheduler_heartbeat")
def scheduler_heartbeat() -> None:
    get_redis().set("reviewlens:health:scheduler", str(time.time()), ex=120)


@celery_app.task(name="reviewlens.platform.expire_admin_sessions")
def expire_admin_sessions() -> None:
    with session_scope() as db:
        revoke_expired_sessions(db)


def _safe_runtime_task_result(result: dict) -> dict[str, str | int]:
    """Discard persisted task output before Celery formats its completion log."""
    status = result.get("status")
    allowed = {
        "succeeded", "failed", "retrying", "cancelled", "timed_out",
        "queued", "running", "missing", "skipped", "duplicate",
    }
    attempt_number = result.get("attempt_number")
    return {
        "status": status if status in allowed else "unknown",
        "attempt_number": attempt_number if type(attempt_number) is int else 0,
    }


@celery_app.task(
    bind=True,
    name="reviewlens.runtime.execute_task",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_runtime_task(self, task_run_id: str, expected_attempt_number: int) -> dict:
    from app.runtime.service import execute_task_run, fail_active_attempt

    task_id = uuid.UUID(task_run_id)
    try:
        result = execute_task_run(
            task_id,
            expected_attempt_number=expected_attempt_number,
            celery_task_id=self.request.id,
            worker_identity=self.request.hostname,
        )
    except SoftTimeLimitExceeded:
        result = fail_active_attempt(
            task_id,
            code="task_soft_time_limit",
            category="timeout",
            retryable=True,
        )
    # Celery logs and may retain task return values. Runtime outputs can contain
    # untrusted YouTube bodies, so only return a fixed, content-free receipt.
    return _safe_runtime_task_result(result)


@celery_app.task(name="reviewlens.runtime.relay_outbox")
def relay_runtime_outbox_task() -> dict[str, int]:
    from app.runtime.outbox import relay_runtime_outbox

    return relay_runtime_outbox()


@celery_app.task(name="reviewlens.runtime.recover")
def recover_runtime_task() -> dict[str, int]:
    from app.runtime.service import recover_stale_attempts, repair_unfinished_runs
    from app.tools.runner import recover_stale_invocations

    stale_attempts = recover_stale_attempts()
    repaired_runs = repair_unfinished_runs()
    stale_invocations = recover_stale_invocations()
    return {
        "stale_attempts": stale_attempts,
        "repaired_runs": repaired_runs,
        "stale_invocations": stale_invocations,
    }


@celery_app.task(name="reviewlens.llmops.refresh_catalogs")
def refresh_openrouter_catalogs_task() -> dict:
    import asyncio
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.db.models import ActiveConfiguration, OpenRouterCatalogRefresh, SystemSettingsVersion
    from app.llmops.catalog import refresh_catalogs

    with session_scope() as db:
        active = db.get(ActiveConfiguration, 1)
        version = db.get(SystemSettingsVersion, active.system_settings_version_id) if active and active.system_settings_version_id else None
        minutes = version.catalog_refresh_minutes if version else settings.openrouter_catalog_refresh_minutes
        latest = db.scalar(select(OpenRouterCatalogRefresh).where(
            OpenRouterCatalogRefresh.catalog_kind == "chat_models",
        ).order_by(OpenRouterCatalogRefresh.started_at.desc()).limit(1))
        if latest and (latest.completed_at or latest.started_at) > datetime.now(timezone.utc) - timedelta(minutes=minutes):
            return {"status": "not_due"}
    redis = get_redis()
    lock_key = "reviewlens:scheduled-catalog-refresh"
    token = uuid.uuid4().hex
    if not redis.set(lock_key, token, nx=True, ex=900):
        return {"status": "already_running"}
    try:
        return asyncio.run(refresh_catalogs())
    finally:
        redis.eval("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                   1, lock_key, token)


@celery_app.task(name="reviewlens.llmops.refresh_credit_state")
def refresh_openrouter_credit_state_task() -> dict:
    import asyncio

    from app.llmops.operations import refresh_credit_state

    return asyncio.run(refresh_credit_state())


@celery_app.task(name="reviewlens.llmops.reconcile_usage")
def reconcile_openrouter_usage_task() -> dict[str, int]:
    import asyncio

    from app.llmops.operations import reconcile_pending_usage

    return asyncio.run(reconcile_pending_usage())


@celery_app.task(name="reviewlens.context.reconcile_markdown")
def reconcile_markdown_task() -> dict[str, int]:
    from sqlalchemy import select

    from app.db.models import Workspace
    from app.knowledge.service import reconcile_workspace

    checked = 0
    failed = 0
    with session_scope() as db:
        workspace_ids = list(db.scalars(select(Workspace.id).where(Workspace.status != "deleted")))
    for workspace_id in workspace_ids:
        try:
            with session_scope() as db:
                result = reconcile_workspace(db, workspace_id)
                checked += result["valid"]
                failed += result["missing"] + result["mismatched"]
        except Exception:
            failed += 1
    return {"workspaces": len(workspace_ids), "checked": checked, "failed": failed}


@celery_app.task(name="reviewlens.admin.execute_job", acks_late=True)
def execute_admin_job_task(job_id: str) -> dict:
    from app.admin.jobs import run_job

    return run_job(uuid.UUID(job_id))


@celery_app.task(name="reviewlens.admin.recover_jobs")
def recover_admin_jobs_task() -> dict[str, int]:
    from app.admin.jobs import recover_jobs

    return {"dispatched": recover_jobs()}


@celery_app.task(name="reviewlens.admin.reconcile_analytics")
def reconcile_admin_analytics_task() -> dict[str, int]:
    from app.admin.analytics import reconcile_usage_aggregates

    with session_scope() as db:
        return reconcile_usage_aggregates(db)


@celery_app.task(name="reviewlens.admin.expire_retained_content")
def expire_retained_content_task() -> dict[str, int]:
    from datetime import datetime, timezone

    from sqlalchemy import delete

    from app.db.models import RetainedLLMContent

    with session_scope() as db:
        result = db.execute(delete(RetainedLLMContent).where(
            RetainedLLMContent.expires_at <= datetime.now(timezone.utc)
        ))
        return {"deleted": result.rowcount or 0}
