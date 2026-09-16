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
            "schedule": float(settings.openrouter_catalog_refresh_minutes * 60),
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
    },
)


@celery_app.task(name="reviewlens.platform.scheduler_heartbeat")
def scheduler_heartbeat() -> None:
    get_redis().set("reviewlens:health:scheduler", str(time.time()), ex=120)


@celery_app.task(name="reviewlens.platform.expire_admin_sessions")
def expire_admin_sessions() -> None:
    with session_scope() as db:
        revoke_expired_sessions(db)


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
        return execute_task_run(
            task_id,
            expected_attempt_number=expected_attempt_number,
            celery_task_id=self.request.id,
            worker_identity=self.request.hostname,
        )
    except SoftTimeLimitExceeded:
        return fail_active_attempt(
            task_id,
            code="task_soft_time_limit",
            category="timeout",
            retryable=True,
        )


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

    from app.llmops.catalog import refresh_catalogs

    return asyncio.run(refresh_catalogs())


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
