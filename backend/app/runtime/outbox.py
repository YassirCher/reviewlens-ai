from __future__ import annotations

import json
import logging
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from redis import Redis
from redis.exceptions import ResponseError
from sqlalchemy import or_, select

from app.cache import get_redis
from app.config import Settings, settings
from app.db.models import AnalysisRun, ProgressEvent, RuntimeOutbox, TaskRun
from app.db.session import session_scope
from app.runtime.contracts import OutboxKind, OutboxStatus, RunStatus, TaskStatus

logger = logging.getLogger(__name__)


class DispatchLockBusy(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stream_key(run_id: uuid.UUID) -> str:
    return f"reviewlens:runs:{run_id}:events"


@contextmanager
def run_dispatch_lock(
    redis_client: Redis,
    run_id: uuid.UUID,
    *,
    ttl_seconds: int,
) -> Iterator[bool]:
    key = f"reviewlens:runtime:dispatch-lock:{run_id}"
    token = secrets.token_hex(16)
    acquired = bool(redis_client.set(key, token, nx=True, ex=ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            redis_client.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end",
                1,
                key,
                token,
            )


def _claim_next_outbox(config: Settings, run_id: uuid.UUID | None = None) -> RuntimeOutbox | None:
    now = utc_now()
    with session_scope() as db:
        statement = (
            select(RuntimeOutbox)
            .where(
                or_(
                    (
                        (RuntimeOutbox.status == OutboxStatus.PENDING)
                        & (RuntimeOutbox.next_attempt_at <= now)
                    ),
                    (
                        (RuntimeOutbox.status == OutboxStatus.PROCESSING)
                        & (RuntimeOutbox.lease_expires_at.is_not(None))
                        & (RuntimeOutbox.lease_expires_at < now)
                    ),
                )
            )
            .order_by(RuntimeOutbox.next_attempt_at, RuntimeOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run_id is not None:
            statement = statement.where(RuntimeOutbox.run_id == run_id)
        item = db.scalar(statement)
        if item is None:
            return None
        item.status = OutboxStatus.PROCESSING
        item.attempts += 1
        item.lease_expires_at = now + timedelta(seconds=config.runtime_outbox_lease_seconds)
        item.last_error_code = None
        db.flush()
        return item


def _load_event(event_id: str) -> ProgressEvent:
    with session_scope() as db:
        event = db.get(ProgressEvent, uuid.UUID(event_id))
        if event is None:
            raise LookupError("progress_event_missing")
        return event


def _publish_progress(item: RuntimeOutbox, redis_client: Redis, config: Settings) -> None:
    event = _load_event(str(item.payload["event_id"]))
    with session_scope() as db:
        predecessor_pending = db.scalar(
            select(RuntimeOutbox.id)
            .join(ProgressEvent, ProgressEvent.id == RuntimeOutbox.aggregate_id)
            .where(
                RuntimeOutbox.run_id == event.run_id,
                RuntimeOutbox.kind == OutboxKind.PROGRESS_PUBLISH,
                RuntimeOutbox.status != OutboxStatus.PUBLISHED,
                ProgressEvent.sequence < event.sequence,
            )
            .limit(1)
        )
    if predecessor_pending:
        raise DispatchLockBusy("an earlier progress event is still pending")
    key = stream_key(event.run_id)
    redis_id = f"{event.sequence}-0"
    fields = {
        "sequence": str(event.sequence),
        "event_type": event.event_type,
        "data": json.dumps(event.public_payload, separators=(",", ":"), ensure_ascii=True),
    }
    try:
        redis_client.xadd(
            key,
            fields,
            id=redis_id,
            maxlen=config.run_event_stream_max_length,
            approximate=True,
        )
    except ResponseError as exc:
        existing = redis_client.xrange(key, min=redis_id, max=redis_id, count=1)
        if not existing or existing[0][1] != fields:
            raise exc
    redis_client.expire(key, config.run_event_stream_ttl_hours * 3600)


def _publish_task_dispatch(item: RuntimeOutbox, redis_client: Redis, config: Settings) -> None:
    with run_dispatch_lock(
        redis_client,
        item.run_id,
        ttl_seconds=config.runtime_dispatch_lock_seconds,
    ) as acquired:
        if not acquired:
            raise DispatchLockBusy("run dispatch lock is busy")
        with session_scope() as db:
            task = db.get(TaskRun, item.task_run_id)
            run = db.get(AnalysisRun, item.run_id)
            if task is None or run is None:
                return
            expected_attempt = int(item.payload["attempt_number"])
            if (
                task.status != TaskStatus.QUEUED
                or run.status in {RunStatus.CANCELLING, RunStatus.CANCELLED}
                or task.current_attempt + 1 != expected_attempt
            ):
                return
        from app.worker import celery_app

        celery_app.send_task(
            "reviewlens.runtime.execute_task",
            args=[str(item.task_run_id), expected_attempt],
            task_id=str(item.id),
            time_limit=min(task.timeout_seconds + 15, 915),
            soft_time_limit=task.timeout_seconds,
        )


def _publish_task_revoke(item: RuntimeOutbox) -> None:
    celery_task_id = item.payload.get("celery_task_id")
    if not celery_task_id:
        return
    from app.worker import celery_app

    celery_app.control.revoke(str(celery_task_id), terminate=False)


def publish_outbox_item(
    item: RuntimeOutbox,
    *,
    redis_client: Redis | None = None,
    config: Settings = settings,
) -> None:
    client = redis_client or get_redis()
    if item.kind == OutboxKind.PROGRESS_PUBLISH:
        _publish_progress(item, client, config)
        return
    if item.kind == OutboxKind.TASK_DISPATCH:
        _publish_task_dispatch(item, client, config)
        return
    if item.kind == OutboxKind.TASK_REVOKE:
        _publish_task_revoke(item)
        return
    raise ValueError("unknown_runtime_outbox_kind")


def relay_runtime_outbox(
    *,
    limit: int | None = None,
    run_id: uuid.UUID | None = None,
    publisher: Callable[[RuntimeOutbox], None] | None = None,
    config: Settings = settings,
) -> dict[str, int]:
    batch_limit = limit or config.runtime_outbox_batch_size
    published = 0
    failed = 0
    for _ in range(batch_limit):
        item = _claim_next_outbox(config, run_id)
        if item is None:
            break
        try:
            if publisher:
                publisher(item)
            else:
                publish_outbox_item(item, config=config)
        except Exception as exc:
            failed += 1
            logger.warning(
                "Runtime outbox delivery failed outbox_id=%s kind=%s exception_type=%s",
                item.id,
                item.kind,
                type(exc).__name__,
            )
            with session_scope() as db:
                current = db.get(RuntimeOutbox, item.id)
                if current and current.status == OutboxStatus.PROCESSING:
                    current.status = OutboxStatus.PENDING
                    current.lease_expires_at = None
                    current.last_error_code = type(exc).__name__[:120]
                    delay = min(60, 2 ** min(current.attempts - 1, 6))
                    current.next_attempt_at = utc_now() + timedelta(seconds=delay)
            continue
        with session_scope() as db:
            current = db.get(RuntimeOutbox, item.id)
            if current and current.status == OutboxStatus.PROCESSING:
                current.status = OutboxStatus.PUBLISHED
                current.lease_expires_at = None
                current.published_at = utc_now()
                current.last_error_code = None
                published += 1
    return {"published": published, "failed": failed}


def _postgres_progress(run_id: uuid.UUID, after_sequence: int, limit: int | None = None) -> list[dict[str, Any]]:
    with session_scope() as db:
        statement = (
            select(ProgressEvent)
            .where(ProgressEvent.run_id == run_id, ProgressEvent.sequence > after_sequence)
            .order_by(ProgressEvent.sequence)
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = list(db.scalars(statement))
    return [
        {"sequence": row.sequence, "event_type": row.event_type, "data": row.public_payload}
        for row in rows
    ]


def read_progress(
    run_id: uuid.UUID,
    *,
    after_sequence: int = 0,
    redis_client: Redis | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if after_sequence < 0:
        raise ValueError("after_sequence cannot be negative")
    if limit is not None and not 1 <= limit <= 1000:
        raise ValueError("progress limit must be from 1 to 1000")
    with session_scope() as db:
        current_sequence = db.scalar(
            select(AnalysisRun.progress_sequence).where(AnalysisRun.id == run_id)
        )
    if current_sequence is None:
        raise LookupError("analysis run not found")
    if after_sequence >= current_sequence:
        return {"source": "redis", "current_sequence": current_sequence, "events": []}

    client = redis_client or get_redis()
    try:
        rows = client.xrange(
            stream_key(run_id),
            min=f"({after_sequence}-0",
            max="+",
            count=limit,
        )
        events = [
            {
                "sequence": int(fields["sequence"]),
                "event_type": fields["event_type"],
                "data": json.loads(fields["data"]),
            }
            for _, fields in rows
        ]
        expected = list(range(after_sequence + 1, after_sequence + len(events) + 1))
        if events and [item["sequence"] for item in events] == expected:
            return {"source": "redis", "current_sequence": current_sequence, "events": events}
    except Exception as exc:
        logger.info(
            "Redis progress read fell back to PostgreSQL run_id=%s exception_type=%s",
            run_id,
            type(exc).__name__,
        )
    return {
        "source": "postgres",
        "current_sequence": current_sequence,
        "events": _postgres_progress(run_id, after_sequence, limit),
    }
