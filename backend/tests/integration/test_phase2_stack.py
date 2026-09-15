from __future__ import annotations

import os
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.cache import get_redis
from app.config import settings
from app.db.models import (
    AnalysisRun,
    ConfigurationSnapshot,
    ProgressEvent,
    RunBudgetState,
    RuntimeOutbox,
    TaskAttempt,
    TaskRun,
)
from app.db.session import session_scope
from app.runtime.contracts import AttemptStatus, OutboxKind, OutboxStatus, RunStatus, TaskStatus
from app.runtime.fixtures import create_fixture_run, install_fixture_configuration
from app.runtime.outbox import read_progress, relay_runtime_outbox, stream_key
from app.runtime.service import (
    create_run,
    reconstruct_run,
    recover_stale_attempts,
    request_cancellation,
    retry_task,
    utc_now,
)
from app.worker import celery_app

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1",
    reason="set REVIEWLENS_RUN_INTEGRATION=1 inside the isolated Compose test stack",
)


def _create(scenario: str) -> uuid.UUID:
    with session_scope() as db:
        run = create_fixture_run(db, scenario)  # type: ignore[arg-type]
        return run.id


def _run_status(run_id: uuid.UUID) -> str:
    with session_scope() as db:
        value = db.scalar(select(AnalysisRun.status).where(AnalysisRun.id == run_id))
    assert value is not None
    return value


def _wait_for_status(run_id: uuid.UUID, expected: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        relay_runtime_outbox()
        if _run_status(run_id) == expected:
            relay_runtime_outbox()
            return
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach {expected}; current={_run_status(run_id)}")


def _wait_for_task_status(run_id: uuid.UUID, expected: str, timeout: float = 15) -> uuid.UUID:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        relay_runtime_outbox()
        with session_scope() as db:
            task_id = db.scalar(
                select(TaskRun.id).where(TaskRun.run_id == run_id, TaskRun.status == expected)
            )
        if task_id:
            return task_id
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} has no task in {expected}")


def _drain_outbox(run_id: uuid.UUID, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        relay_runtime_outbox()
        with session_scope() as db:
            remaining = db.scalar(
                select(func.count(RuntimeOutbox.id)).where(
                    RuntimeOutbox.run_id == run_id,
                    RuntimeOutbox.status != OutboxStatus.PUBLISHED,
                )
            )
        if remaining == 0:
            return
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} still has pending outbox records")


def test_run_creation_is_atomic_and_contains_snapshot_dag_budget_event_and_outbox() -> None:
    with session_scope() as db:
        install_fixture_configuration(db, "success")

    created_ids: list[uuid.UUID] = []
    with pytest.raises(RuntimeError, match="force rollback"):
        with session_scope() as db:
            run = create_run(
                db,
                product_name="Atomic rollback fixture",
                initiator_type="system_fixture",
                requested_options={"rollback": True},
            )
            created_ids.append(run.id)
            raise RuntimeError("force rollback")
    with session_scope() as db:
        assert db.get(AnalysisRun, created_ids[0]) is None

    run_id = _create("success")
    with session_scope() as db:
        state = reconstruct_run(db, run_id)
        run = db.get(AnalysisRun, run_id)
        snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id)
        outbox_kinds = set(
            db.scalars(select(RuntimeOutbox.kind).where(RuntimeOutbox.run_id == run_id))
        )
    assert state["run"]["status"] == RunStatus.QUEUED
    assert len(state["tasks"]) == 3
    assert len(state["dependencies"]) == 2
    assert state["events"][0]["event_type"] == "run.queued"
    assert snapshot.snapshot["budget_limits"]["max_tokens"] == 100_000
    assert snapshot.snapshot["feature_flags"]["runtime_fixture"] is True
    assert {OutboxKind.TASK_DISPATCH, OutboxKind.PROGRESS_PUBLISH} <= outbox_kinds


def test_background_fixture_completes_and_reconstructs_from_postgres() -> None:
    run_id = _create("success")
    _wait_for_status(run_id, RunStatus.COMPLETE)
    with session_scope() as db:
        first = reconstruct_run(db, run_id)
    with session_scope() as new_db:
        reconstructed = reconstruct_run(new_db, run_id)
    assert first == reconstructed
    assert [task["status"] for task in reconstructed["tasks"]] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.SUCCEEDED,
        TaskStatus.SUCCEEDED,
    ]
    assert len(reconstructed["attempts"]) == 3
    sequences = [event["sequence"] for event in reconstructed["events"]]
    assert sequences == list(range(1, len(sequences) + 1))
    assert reconstructed["events"][-1]["event_type"] == "run.completed"
    assert reconstructed["budget"]["status"] == "closed"


def test_duplicate_celery_delivery_produces_one_successful_effect_per_task() -> None:
    run_id = _create("success")
    with session_scope() as db:
        first_task_id = db.scalar(
            select(TaskRun.id).where(TaskRun.run_id == run_id, TaskRun.status == TaskStatus.QUEUED)
        )
    assert first_task_id
    celery_app.send_task("reviewlens.runtime.execute_task", args=[str(first_task_id), 1])
    celery_app.send_task("reviewlens.runtime.execute_task", args=[str(first_task_id), 1])
    _wait_for_status(run_id, RunStatus.COMPLETE)
    with session_scope() as db:
        tasks = list(db.scalars(select(TaskRun).where(TaskRun.run_id == run_id)))
        attempt_counts = {
            task.id: db.scalar(
                select(func.count(TaskAttempt.id)).where(TaskAttempt.task_run_id == task.id)
            )
            for task in tasks
        }
    assert set(attempt_counts.values()) == {1}


def test_retry_preserves_failed_attempt_and_creates_a_new_successful_attempt() -> None:
    run_id = _create("retry_once")
    _wait_for_status(run_id, RunStatus.COMPLETE)
    with session_scope() as db:
        task = db.scalar(
            select(TaskRun).where(
                TaskRun.run_id == run_id,
                TaskRun.workflow_task_key == "fixture.work",
            )
        )
        attempts = list(
            db.scalars(
                select(TaskAttempt)
                .where(TaskAttempt.task_run_id == task.id)
                .order_by(TaskAttempt.attempt_number)
            )
        )
    assert [(item.attempt_number, item.status) for item in attempts] == [
        (1, AttemptStatus.FAILED),
        (2, AttemptStatus.SUCCEEDED),
    ]
    assert attempts[0].error_code == "fixture_transient"
    assert attempts[0].output_payload is None
    assert attempts[1].output_payload["attempt_number"] == 2


def test_exhausted_run_budget_blocks_execution_before_an_attempt_starts() -> None:
    run_id = _create("success")
    with session_scope() as db:
        budget = db.get(RunBudgetState, run_id)
        budget.max_tokens = 0
    _wait_for_status(run_id, RunStatus.FAILED)
    with session_scope() as db:
        attempts = db.scalar(
            select(func.count(TaskAttempt.id))
            .join(TaskRun, TaskRun.id == TaskAttempt.task_run_id)
            .where(TaskRun.run_id == run_id)
        )
        budget = db.get(RunBudgetState, run_id)
    assert attempts == 0
    assert budget.status == "exhausted"


def test_manual_retry_reopens_failed_run_and_rebuilds_skipped_dependencies() -> None:
    run_id = _create("retry_once")
    with session_scope() as db:
        task = db.scalar(
            select(TaskRun).where(
                TaskRun.run_id == run_id,
                TaskRun.workflow_task_key == "fixture.work",
            )
        )
        task.max_attempts = 1
        task_id = task.id
    _wait_for_status(run_id, RunStatus.FAILED)
    with session_scope() as db:
        skipped = db.scalar(
            select(TaskRun).where(
                TaskRun.run_id == run_id,
                TaskRun.workflow_task_key == "fixture.finish",
            )
        )
        assert skipped.status == TaskStatus.SKIPPED
        retry_task(db, task_id)
    _wait_for_status(run_id, RunStatus.COMPLETE)
    with session_scope() as db:
        retried = db.get(TaskRun, task_id)
        downstream = db.scalar(
            select(TaskRun).where(
                TaskRun.run_id == run_id,
                TaskRun.workflow_task_key == "fixture.finish",
            )
        )
    assert retried.current_attempt == 2
    assert downstream.status == TaskStatus.SUCCEEDED


def test_cancellation_is_cooperative_and_never_dispatches_downstream() -> None:
    run_id = _create("cancel")
    _wait_for_task_status(run_id, TaskStatus.RUNNING)
    with session_scope() as db:
        request_cancellation(db, run_id)
    _wait_for_status(run_id, RunStatus.CANCELLED)
    with session_scope() as db:
        downstream = db.scalar(
            select(TaskRun).where(
                TaskRun.run_id == run_id,
                TaskRun.workflow_task_key == "fixture.downstream",
            )
        )
        attempt_count = db.scalar(
            select(func.count(TaskAttempt.id)).where(TaskAttempt.task_run_id == downstream.id)
        )
    assert downstream.status == TaskStatus.CANCELLED
    assert downstream.current_attempt == 0
    assert attempt_count == 0


def test_stale_worker_lease_is_recovered_and_retried() -> None:
    with session_scope() as db:
        run = create_fixture_run(db, "success")
        run_id = run.id
        task = db.scalar(
            select(TaskRun).where(TaskRun.run_id == run.id, TaskRun.status == TaskStatus.QUEUED)
        )
        task.status = TaskStatus.RUNNING
        task.current_attempt = 1
        task.started_at = utc_now() - timedelta(seconds=5)
        db.add(
            TaskAttempt(
                id=uuid.uuid4(),
                task_run_id=task.id,
                attempt_number=1,
                status=AttemptStatus.RUNNING,
                input_payload=task.input_payload,
                input_hash="a" * 64,
                worker_identity="interrupted-worker",
                lease_expires_at=utc_now() - timedelta(seconds=1),
                heartbeat_at=utc_now() - timedelta(seconds=2),
                started_at=utc_now() - timedelta(seconds=5),
                created_at=utc_now() - timedelta(seconds=5),
            )
        )
        dispatches = list(
            db.scalars(
                select(RuntimeOutbox).where(
                    RuntimeOutbox.run_id == run.id,
                    RuntimeOutbox.kind == OutboxKind.TASK_DISPATCH,
                )
            )
        )
        for dispatch in dispatches:
            dispatch.status = OutboxStatus.PUBLISHED
            dispatch.published_at = utc_now()
    assert recover_stale_attempts() == 1
    _wait_for_status(run_id, RunStatus.COMPLETE)
    with session_scope() as db:
        recovered = db.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_run_id == task.id,
                TaskAttempt.attempt_number == 1,
            )
        )
    assert recovered.status == AttemptStatus.FAILED
    assert recovered.error_category == "worker_interrupted"
    assert recovered.error_code == "attempt_lease_expired"


def test_progress_resumes_from_redis_and_falls_back_to_postgres_after_a_gap() -> None:
    run_id = _create("success")
    _wait_for_status(run_id, RunStatus.COMPLETE)
    _drain_outbox(run_id)
    redis_client = get_redis()
    mirrored = read_progress(run_id, after_sequence=0, redis_client=redis_client)
    assert mirrored["source"] == "redis"
    assert [item["sequence"] for item in mirrored["events"]] == list(
        range(1, mirrored["current_sequence"] + 1)
    )
    with session_scope() as db:
        persisted_ids = {
            event.sequence
            for event in db.scalars(select(ProgressEvent).where(ProgressEvent.run_id == run_id))
        }
    assert {item["sequence"] for item in mirrored["events"]} <= persisted_ids

    redis_client.delete(stream_key(run_id))
    repaired = read_progress(run_id, after_sequence=1, redis_client=redis_client)
    assert repaired["source"] == "postgres"
    assert repaired["events"][0]["sequence"] == 2


def test_outbox_failure_is_persisted_and_retryable_without_raw_error_text() -> None:
    with session_scope() as db:
        run = create_fixture_run(db, "success")
        run_id = run.id
        db.flush()
        dispatches = list(
            db.scalars(
                select(RuntimeOutbox).where(
                    RuntimeOutbox.run_id == run_id,
                    RuntimeOutbox.kind == OutboxKind.TASK_DISPATCH,
                )
            )
        )
        for dispatch in dispatches:
            dispatch.status = OutboxStatus.PUBLISHED
            dispatch.published_at = utc_now()

    def fail_delivery(_: RuntimeOutbox) -> None:
        raise RuntimeError("secret upstream body must not be stored")

    result = relay_runtime_outbox(limit=1, run_id=run_id, publisher=fail_delivery)
    assert result == {"published": 0, "failed": 1}
    with session_scope() as db:
        failed = db.scalar(
            select(RuntimeOutbox)
            .where(RuntimeOutbox.run_id == run_id, RuntimeOutbox.status == OutboxStatus.PENDING)
            .order_by(RuntimeOutbox.created_at)
        )
        assert failed.last_error_code == "RuntimeError"
        assert "secret" not in (failed.last_error_code or "")
        failed.next_attempt_at = utc_now()
    result = relay_runtime_outbox(limit=1, run_id=run_id, publisher=lambda _: None)
    assert result == {"published": 1, "failed": 0}
    with session_scope() as db:
        request_cancellation(db, run_id)


def test_run_snapshot_and_terminal_attempts_are_database_immutable() -> None:
    run_id = _create("success")
    _wait_for_status(run_id, RunStatus.COMPLETE)
    with session_scope() as db:
        run = db.get(AnalysisRun, run_id)
        snapshot_id = run.configuration_snapshot_id
        attempt_id = db.scalar(
            select(TaskAttempt.id)
            .join(TaskRun, TaskRun.id == TaskAttempt.task_run_id)
            .where(TaskRun.run_id == run_id)
            .limit(1)
        )

    with pytest.raises(DBAPIError), session_scope() as db:
        snapshot = db.get(ConfigurationSnapshot, snapshot_id)
        snapshot.content_hash = "f" * 64

    with pytest.raises(DBAPIError), session_scope() as db:
        attempt = db.get(TaskAttempt, attempt_id)
        attempt.error_code = "forbidden-overwrite"


def test_new_active_configuration_does_not_change_an_existing_run_snapshot() -> None:
    run_id = _create("success")
    with session_scope() as db:
        run = db.get(AnalysisRun, run_id)
        snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id)
        original_hash = snapshot.content_hash
        original_workflow_id = snapshot.workflow_version_id
        install_fixture_configuration(db, "retry_once")
    _wait_for_status(run_id, RunStatus.COMPLETE)
    with session_scope() as db:
        run = db.get(AnalysisRun, run_id)
        snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id)
    assert snapshot.content_hash == original_hash
    assert snapshot.workflow_version_id == original_workflow_id
