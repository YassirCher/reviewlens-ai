from __future__ import annotations

import time

from celery import Celery

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
    },
)


@celery_app.task(name="reviewlens.platform.scheduler_heartbeat")
def scheduler_heartbeat() -> None:
    get_redis().set("reviewlens:health:scheduler", str(time.time()), ex=120)


@celery_app.task(name="reviewlens.platform.expire_admin_sessions")
def expire_admin_sessions() -> None:
    with session_scope() as db:
        revoke_expired_sessions(db)
