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

    stale_attempts = recover_stale_attempts()
    repaired_runs = repair_unfinished_runs()
    return {"stale_attempts": stale_attempts, "repaired_runs": repaired_runs}
