from __future__ import annotations

import logging
import socket
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AgentVersion,
    AnalysisRun,
    BudgetPolicyVersion,
    ConfigurationSnapshot,
    EmbeddingPolicyVersion,
    ModelPolicyVersion,
    ProgressEvent,
    RunBudgetState,
    RuntimeOutbox,
    TaskAttempt,
    TaskDependency,
    TaskRun,
    ToolVersion,
    WorkflowVersion,
)
from app.db.session import session_scope
from app.runtime.contracts import (
    ATTEMPT_TERMINAL_STATUSES,
    PUBLIC_EVENT_TYPES,
    RUN_TERMINAL_STATUSES,
    TASK_TERMINAL_STATUSES,
    AttemptStatus,
    OutboxKind,
    OutboxStatus,
    ProgressData,
    RetryPolicy,
    RunStatus,
    TaskStatus,
    WorkflowDag,
    canonical_json_hash,
    require_run_transition,
    require_task_transition,
    stable_idempotency_key,
)

logger = logging.getLogger(__name__)


class RuntimeConfigurationError(RuntimeError):
    pass


class RuntimeTaskError(RuntimeError):
    def __init__(self, code: str, *, category: str = "internal", retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.category = category
        self.retryable = retryable


class RuntimeTaskCancelled(RuntimeTaskError):
    def __init__(self) -> None:
        super().__init__("task_cancelled", category="cancelled", retryable=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_product_name(value: str) -> tuple[str, str]:
    display = " ".join(value.split()).strip()
    if not display:
        raise ValueError("product_name cannot be empty")
    if len(display) > 500:
        raise ValueError("product_name cannot exceed 500 characters")
    return display, display.casefold()


def _version_ref(record: Any) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "version_number": record.version_number,
        "content_hash": record.content_hash,
    }


def _require_published(record: Any, label: str) -> None:
    if record is None or record.lifecycle != "published":
        raise RuntimeConfigurationError(f"{label} must reference a published version")


def _budget_max_tokens(policy: BudgetPolicyVersion) -> int | None:
    raw = policy.token_limits.get("run_total_tokens", policy.token_limits.get("run_total"))
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigurationError("budget run token limit must be an integer") from exc
    if value < 0:
        raise RuntimeConfigurationError("budget run token limit cannot be negative")
    return value


def _usd_to_micros(value: Decimal) -> int:
    return int((value * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def build_configuration_snapshot(db: Session) -> tuple[ConfigurationSnapshot, WorkflowDag, BudgetPolicyVersion]:
    active = db.get(ActiveConfiguration, 1)
    if active is None:
        raise RuntimeConfigurationError("active configuration is missing")
    workflow = db.get(WorkflowVersion, active.workflow_version_id) if active.workflow_version_id else None
    budget = db.get(BudgetPolicyVersion, active.budget_policy_version_id) if active.budget_policy_version_id else None
    _require_published(workflow, "active workflow")
    _require_published(budget, "active budget policy")
    assert workflow is not None and budget is not None

    dag = WorkflowDag.model_validate(workflow.dag)
    agent_ids = sorted(
        {task.agent_version_id for task in dag.tasks if task.agent_version_id}, key=str
    )
    tool_ids = sorted(
        {task.tool_version_id for task in dag.tasks if task.tool_version_id}, key=str
    )
    agents = [db.get(AgentVersion, item) for item in agent_ids]
    tools = [db.get(ToolVersion, item) for item in tool_ids]
    for agent in agents:
        _require_published(agent, "workflow agent")
    if agent_ids:
        from app.db.models import AgentVersionTool

        allowlisted_tool_ids = set(
            db.scalars(
                select(AgentVersionTool.tool_version_id).where(
                    AgentVersionTool.agent_version_id.in_(agent_ids)
                )
            )
        )
        tool_ids = sorted(set(tool_ids) | allowlisted_tool_ids, key=str)
        tools = [db.get(ToolVersion, item) for item in tool_ids]
    for tool in tools:
        _require_published(tool, "workflow tool")

    model_ids = sorted(
        {agent.model_policy_version_id for agent in agents if agent and agent.model_policy_version_id},
        key=str,
    )
    models = [db.get(ModelPolicyVersion, item) for item in model_ids]
    for model in models:
        _require_published(model, "agent model policy")

    embedding_record = (
        db.get(EmbeddingPolicyVersion, active.embedding_policy_version_id)
        if active.embedding_policy_version_id
        else None
    )
    if embedding_record:
        _require_published(embedding_record, "active embedding policy")
    embedding = _version_ref(embedding_record) if embedding_record else None
    body = {
        "schema_version": 1,
        "workflow": _version_ref(workflow),
        "budget_policy": _version_ref(budget),
        "embedding_policy": embedding,
        "agents": [_version_ref(item) for item in agents if item],
        "tools": [_version_ref(item) for item in tools if item],
        "model_policies": [_version_ref(item) for item in models if item],
        "feature_flags": active.feature_flags,
        "dag": dag.model_dump(mode="json"),
        "budget_limits": {
            "max_tokens": _budget_max_tokens(budget),
            "max_cost_microusd": _usd_to_micros(budget.public_run_cost_cap_usd),
        },
    }
    snapshot = ConfigurationSnapshot(
        id=uuid.uuid4(),
        workflow_version_id=workflow.id,
        budget_policy_version_id=budget.id,
        embedding_policy_version_id=active.embedding_policy_version_id,
        content_hash=canonical_json_hash(body),
        snapshot=body,
        created_at=utc_now(),
    )
    return snapshot, dag, budget


def create_run(
    db: Session,
    *,
    product_name: str,
    initiator_type: str,
    initiator_id: uuid.UUID | None = None,
    requested_options: dict[str, Any] | None = None,
) -> AnalysisRun:
    display_product, canonical_product = normalize_product_name(product_name)
    snapshot, dag, budget = build_configuration_snapshot(db)
    now = utc_now()
    run = AnalysisRun(
        id=uuid.uuid4(),
        product_input=display_product,
        canonical_product=canonical_product,
        initiator_type=initiator_type,
        initiator_id=initiator_id,
        requested_options=requested_options or {},
        status=RunStatus.QUEUED,
        configuration_snapshot_id=snapshot.id,
        progress_sequence=0,
        coverage={"completed_tasks": 0, "total_tasks": len(dag.tasks)},
        warning_summary={},
        deadline_at=now + timedelta(seconds=dag.run_timeout_seconds),
    )
    # Flush the immutable parent first. The models intentionally avoid broad
    # ORM relationships, so explicit ordering keeps the FK transaction clear.
    db.add(snapshot)
    db.flush()
    db.add(run)
    db.flush()
    limits = snapshot.snapshot["budget_limits"]
    db.add(
        RunBudgetState(
            run_id=run.id,
            budget_policy_version_id=budget.id,
            max_tokens=limits["max_tokens"],
            max_cost_microusd=limits["max_cost_microusd"],
            status="active",
        )
    )

    tasks_by_key: dict[str, TaskRun] = {}
    for spec in dag.topological_order():
        task = TaskRun(
            id=uuid.uuid4(),
            run_id=run.id,
            workflow_task_key=spec.task_key,
            source_key=None,
            executor_kind=spec.executor_kind,
            handler=spec.handler,
            agent_version_id=spec.agent_version_id,
            tool_version_id=spec.tool_version_id,
            status=TaskStatus.BLOCKED if spec.dependencies else TaskStatus.QUEUED,
            priority=spec.priority,
            weight=spec.weight,
            timeout_seconds=spec.timeout_seconds,
            max_attempts=spec.retry.max_attempts,
            retry_policy=spec.retry.model_dump(mode="json"),
            input_payload=spec.input,
            idempotency_key=stable_idempotency_key(run.id, spec.task_key, "root"),
            current_attempt=0,
            deadline_at=run.deadline_at,
        )
        tasks_by_key[spec.task_key] = task
        db.add(task)
    db.flush()

    for spec in dag.tasks:
        for upstream_key in spec.dependencies:
            db.add(
                TaskDependency(
                    upstream_task_id=tasks_by_key[upstream_key].id,
                    downstream_task_id=tasks_by_key[spec.task_key].id,
                )
            )
    for task in tasks_by_key.values():
        if task.status == TaskStatus.QUEUED:
            queue_task_dispatch(db, run, task)
    append_progress(
        db,
        run,
        event_type="run.queued",
        label="Analysis queued",
        detail="The durable workflow is waiting for a worker.",
    )
    db.flush()
    return run


def _progress_counts(db: Session, run: AnalysisRun) -> tuple[int, int, int]:
    tasks = list(db.scalars(select(TaskRun).where(TaskRun.run_id == run.id)))
    total_weight = sum(item.weight for item in tasks) or 1
    completed_weight = sum(item.weight for item in tasks if TaskStatus(item.status) in TASK_TERMINAL_STATUSES)
    completed_tasks = sum(TaskStatus(item.status) in TASK_TERMINAL_STATUSES for item in tasks)
    percent = min(100, int((completed_weight * 100) / total_weight))
    return completed_tasks, len(tasks) or 1, percent


def append_progress(
    db: Session,
    run: AnalysisRun,
    *,
    event_type: str,
    label: str,
    detail: str = "",
    task: TaskRun | None = None,
    result_summary: dict[str, Any] | None = None,
    admin_metadata: dict[str, Any] | None = None,
) -> ProgressEvent:
    if event_type not in PUBLIC_EVENT_TYPES:
        raise ValueError(f"unsupported public event type: {event_type}")
    db.flush()
    run.progress_sequence += 1
    sequence = run.progress_sequence
    completed, total, percent = _progress_counts(db, run)
    now = utc_now()
    payload = ProgressData(
        sequence=sequence,
        run_id=run.id,
        task_id=task.id if task else None,
        task_key=task.workflow_task_key if task else None,
        label=label,
        detail=detail,
        completed_tasks=completed,
        total_tasks=total,
        percent=percent,
        timestamp=now.isoformat(),
        result_summary=result_summary,
    ).model_dump(mode="json", exclude_none=True)
    event = ProgressEvent(
        id=uuid.uuid4(),
        run_id=run.id,
        task_run_id=task.id if task else None,
        sequence=sequence,
        event_type=event_type,
        public_payload=payload,
        admin_metadata=admin_metadata or {},
        created_at=now,
    )
    db.add(event)
    db.add(
        RuntimeOutbox(
            id=uuid.uuid4(),
            kind=OutboxKind.PROGRESS_PUBLISH,
            aggregate_type="progress_event",
            aggregate_id=event.id,
            run_id=run.id,
            task_run_id=task.id if task else None,
            payload={"event_id": str(event.id)},
            idempotency_key=stable_idempotency_key("progress", event.id),
            status=OutboxStatus.PENDING,
            next_attempt_at=now,
        )
    )
    return event


def queue_task_dispatch(
    db: Session,
    run: AnalysisRun,
    task: TaskRun,
    *,
    available_at: datetime | None = None,
) -> RuntimeOutbox:
    next_attempt = task.current_attempt + 1
    key = stable_idempotency_key("dispatch", task.id, next_attempt)
    existing = db.scalar(select(RuntimeOutbox).where(RuntimeOutbox.idempotency_key == key))
    if existing:
        return existing
    outbox = RuntimeOutbox(
        id=uuid.uuid4(),
        kind=OutboxKind.TASK_DISPATCH,
        aggregate_type="task_run",
        aggregate_id=task.id,
        run_id=run.id,
        task_run_id=task.id,
        payload={"task_run_id": str(task.id), "attempt_number": next_attempt},
        idempotency_key=key,
        status=OutboxStatus.PENDING,
        next_attempt_at=available_at or utc_now(),
    )
    db.add(outbox)
    return outbox


def _queue_task_revoke(db: Session, run: AnalysisRun, task: TaskRun, attempt: TaskAttempt) -> None:
    if not attempt.celery_task_id:
        return
    key = stable_idempotency_key("revoke", attempt.id)
    if db.scalar(select(RuntimeOutbox.id).where(RuntimeOutbox.idempotency_key == key)):
        return
    db.add(
        RuntimeOutbox(
            id=uuid.uuid4(),
            kind=OutboxKind.TASK_REVOKE,
            aggregate_type="task_attempt",
            aggregate_id=attempt.id,
            run_id=run.id,
            task_run_id=task.id,
            payload={"celery_task_id": attempt.celery_task_id},
            idempotency_key=key,
            status=OutboxStatus.PENDING,
            next_attempt_at=utc_now(),
        )
    )


def _set_run_status(run: AnalysisRun, status: RunStatus) -> None:
    require_run_transition(run.status, status)
    run.status = status


def _set_task_status(task: TaskRun, status: TaskStatus) -> None:
    require_task_transition(task.status, status)
    task.status = status


def request_cancellation(db: Session, run_id: uuid.UUID) -> AnalysisRun:
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
    if run is None:
        raise LookupError("analysis run not found")
    if RunStatus(run.status) in RUN_TERMINAL_STATUSES:
        return run
    if run.status != RunStatus.CANCELLING:
        _set_run_status(run, RunStatus.CANCELLING)
        run.cancellation_requested_at = utc_now()
        append_progress(
            db,
            run,
            event_type="task.progress",
            label="Cancellation requested",
            detail="No additional workflow tasks will be dispatched.",
        )

    tasks = list(
        db.scalars(select(TaskRun).where(TaskRun.run_id == run.id).with_for_update())
    )
    for task in tasks:
        status = TaskStatus(task.status)
        if status in {TaskStatus.BLOCKED, TaskStatus.QUEUED}:
            _set_task_status(task, TaskStatus.CANCELLED)
            task.completed_at = utc_now()
        elif status == TaskStatus.RUNNING:
            _set_task_status(task, TaskStatus.CANCELLING)
            attempt = db.scalar(
                select(TaskAttempt)
                .where(
                    TaskAttempt.task_run_id == task.id,
                    TaskAttempt.status == AttemptStatus.RUNNING,
                )
                .order_by(TaskAttempt.attempt_number.desc())
            )
            if attempt:
                _queue_task_revoke(db, run, task, attempt)
    _finalize_run(db, run)
    return run


def retry_task(db: Session, task_id: uuid.UUID) -> TaskRun:
    task = db.scalar(select(TaskRun).where(TaskRun.id == task_id).with_for_update())
    if task is None:
        raise LookupError("task run not found")
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == task.run_id).with_for_update())
    assert run is not None
    if run.status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
        raise RuntimeTaskError("run_is_cancelling", category="cancelled")
    if task.status not in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
        raise RuntimeTaskError("task_not_retryable", category="invalid_state")
    if utc_now() >= run.deadline_at:
        raise RuntimeTaskError("run_deadline_elapsed", category="timeout")
    task.max_attempts = max(task.max_attempts, task.current_attempt + 1)
    _set_task_status(task, TaskStatus.QUEUED)
    task.completed_at = None
    if RunStatus(run.status) in RUN_TERMINAL_STATUSES:
        _set_run_status(run, RunStatus.RUNNING)
        run.completed_at = None
        run.error_code = None
        budget = db.get(RunBudgetState, run.id)
        if budget:
            budget.status = "active"
    _reset_skipped_descendants(db, task.id)
    queue_task_dispatch(db, run, task)
    return task


def task_is_cancelled(run_id: uuid.UUID, task_id: uuid.UUID) -> bool:
    with session_scope() as db:
        run_status = db.scalar(select(AnalysisRun.status).where(AnalysisRun.id == run_id))
        task_status = db.scalar(select(TaskRun.status).where(TaskRun.id == task_id))
    return run_status in {RunStatus.CANCELLING, RunStatus.CANCELLED} or task_status in {
        TaskStatus.CANCELLING,
        TaskStatus.CANCELLED,
    }


def heartbeat_attempt(attempt_id: uuid.UUID, config: Settings = settings) -> None:
    now = utc_now()
    with session_scope() as db:
        attempt = db.scalar(
            select(TaskAttempt).where(
                TaskAttempt.id == attempt_id,
                TaskAttempt.status == AttemptStatus.RUNNING,
            )
        )
        if attempt:
            attempt.heartbeat_at = now
            attempt.lease_expires_at = now + timedelta(seconds=config.runtime_task_lease_seconds)


def execute_task_run(
    task_id: uuid.UUID,
    *,
    expected_attempt_number: int | None = None,
    celery_task_id: str | None = None,
    worker_identity: str | None = None,
    config: Settings = settings,
) -> dict[str, Any]:
    attempt_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    handler = ""
    input_payload: dict[str, Any] = {}
    attempt_number = 0
    with session_scope() as db:
        task = db.scalar(select(TaskRun).where(TaskRun.id == task_id).with_for_update())
        if task is None:
            return {"status": "missing"}
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == task.run_id).with_for_update())
        assert run is not None
        run_id = run.id
        if TaskStatus(task.status) in TASK_TERMINAL_STATUSES:
            return _existing_task_result(db, task)
        if run.status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
            if task.status in {TaskStatus.BLOCKED, TaskStatus.QUEUED}:
                _set_task_status(task, TaskStatus.CANCELLED)
                task.completed_at = utc_now()
                _finalize_run(db, run)
            return {"status": "cancelled"}
        if task.status == TaskStatus.CANCELLING:
            return {"status": "cancelling"}
        if task.status == TaskStatus.RUNNING:
            attempt = db.scalar(
                select(TaskAttempt)
                .where(TaskAttempt.task_run_id == task.id)
                .order_by(TaskAttempt.attempt_number.desc())
            )
            if attempt and attempt.status == AttemptStatus.RUNNING and attempt.lease_expires_at:
                lease = attempt.lease_expires_at
                if lease.tzinfo is None:
                    lease = lease.replace(tzinfo=timezone.utc)
                if lease > utc_now():
                    return {"status": "already_running", "attempt_number": attempt.attempt_number}
            return {"status": "stale_running"}
        if task.status != TaskStatus.QUEUED:
            return {"status": task.status}
        if expected_attempt_number is not None and expected_attempt_number != task.current_attempt + 1:
            return {
                "status": "stale_delivery",
                "expected_attempt_number": expected_attempt_number,
                "current_attempt": task.current_attempt,
            }

        now = utc_now()
        budget = db.get(RunBudgetState, run.id)
        budget_exhausted = bool(
            budget
            and (
                budget.status == "exhausted"
                or (
                    budget.max_tokens is not None
                    and budget.reserved_tokens + budget.consumed_tokens >= budget.max_tokens
                )
                or (
                    budget.max_cost_microusd is not None
                    and budget.reserved_cost_microusd + budget.consumed_cost_microusd
                    >= budget.max_cost_microusd
                )
            )
        )
        if budget_exhausted:
            if budget:
                budget.status = "exhausted"
            _set_task_status(task, TaskStatus.FAILED)
            task.completed_at = now
            append_progress(
                db,
                run,
                event_type="task.failed",
                task=task,
                label="Task blocked by run budget",
                detail="The run execution envelope is exhausted.",
                admin_metadata={"error_category": "budget", "error_code": "budget_exceeded"},
            )
            _skip_blocked_descendants(db, run, task.id)
            _finalize_run(db, run)
            return {"status": "failed", "error_code": "budget_exceeded"}
        if now >= task.deadline_at or now >= run.deadline_at:
            _set_task_status(task, TaskStatus.TIMED_OUT)
            task.completed_at = now
            append_progress(
                db,
                run,
                event_type="task.failed",
                task=task,
                label="Task timed out",
                detail="The task deadline elapsed before execution.",
                admin_metadata={"error_category": "timeout", "error_code": "deadline_elapsed"},
            )
            _skip_blocked_descendants(db, run, task.id)
            _finalize_run(db, run)
            return {"status": "timed_out"}

        attempt_number = task.current_attempt + 1
        attempt_id = uuid.uuid4()
        lease_expires = now + timedelta(seconds=config.runtime_task_lease_seconds)
        task.deadline_at = min(run.deadline_at, now + timedelta(seconds=task.timeout_seconds))
        attempt = TaskAttempt(
            id=attempt_id,
            task_run_id=task.id,
            attempt_number=attempt_number,
            status=AttemptStatus.RUNNING,
            input_payload=task.input_payload,
            input_hash=canonical_json_hash(task.input_payload),
            celery_task_id=celery_task_id,
            worker_identity=worker_identity or socket.gethostname(),
            lease_expires_at=lease_expires,
            heartbeat_at=now,
            started_at=now,
            created_at=now,
        )
        db.add(attempt)
        task.current_attempt = attempt_number
        _set_task_status(task, TaskStatus.RUNNING)
        task.started_at = task.started_at or now
        if run.status == RunStatus.QUEUED:
            _set_run_status(run, RunStatus.RUNNING)
            run.started_at = now
            append_progress(
                db,
                run,
                event_type="run.started",
                label="Analysis started",
                detail="A worker claimed the first task.",
            )
        append_progress(
            db,
            run,
            event_type="task.started",
            task=task,
            label="Task started",
            detail=task.workflow_task_key,
            admin_metadata={"attempt_number": attempt_number},
        )
        handler = task.handler
        input_payload = dict(task.input_payload)

    assert attempt_id is not None and run_id is not None
    try:
        from app.tools.registry import HANDLER_REGISTRY

        if handler in HANDLER_REGISTRY:
            from app.tools.errors import ToolExecutionError
            from app.tools.runner import execute_registered_tool

            try:
                output = execute_registered_tool(attempt_id, handler, input_payload, config=config)
            except ToolExecutionError as exc:
                raise RuntimeTaskError(
                    exc.code,
                    category=exc.category,
                    retryable=exc.retryable,
                ) from exc
        else:
            from app.runtime.fixtures import execute_deterministic_handler

            output = execute_deterministic_handler(
                handler,
                input_payload,
                attempt_number=attempt_number,
                should_cancel=lambda: task_is_cancelled(run_id, task_id),
                heartbeat=lambda: heartbeat_attempt(attempt_id, config),
            )
    except RuntimeTaskCancelled as exc:
        return _complete_attempt_failure(task_id, attempt_id, exc, config=config)
    except RuntimeTaskError as exc:
        return _complete_attempt_failure(task_id, attempt_id, exc, config=config)
    except Exception as exc:
        logger.error(
            "Runtime executor failed run_id=%s task_id=%s exception_type=%s",
            run_id,
            task_id,
            type(exc).__name__,
        )
        safe = RuntimeTaskError("executor_failed", category="internal", retryable=False)
        return _complete_attempt_failure(task_id, attempt_id, safe, config=config)
    return _complete_attempt_success(task_id, attempt_id, output)


def _existing_task_result(db: Session, task: TaskRun) -> dict[str, Any]:
    attempt = db.scalar(
        select(TaskAttempt)
        .where(TaskAttempt.task_run_id == task.id)
        .order_by(TaskAttempt.attempt_number.desc())
    )
    payload: dict[str, Any] = {"status": task.status, "attempt_number": task.current_attempt}
    if attempt and attempt.status == AttemptStatus.SUCCEEDED:
        payload["output"] = attempt.output_payload
    return payload


def _finish_attempt_common(attempt: TaskAttempt, now: datetime) -> None:
    attempt.ended_at = now
    attempt.lease_expires_at = None
    attempt.duration_ms = max(0, int((now - attempt.started_at).total_seconds() * 1000))


def _complete_attempt_success(
    task_id: uuid.UUID, attempt_id: uuid.UUID, output: dict[str, Any]
) -> dict[str, Any]:
    now = utc_now()
    with session_scope() as db:
        attempt = db.scalar(select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update())
        task = db.scalar(select(TaskRun).where(TaskRun.id == task_id).with_for_update())
        if attempt is None or task is None:
            return {"status": "missing"}
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == task.run_id).with_for_update())
        assert run is not None
        if attempt.status in {item.value for item in ATTEMPT_TERMINAL_STATUSES}:
            return _existing_task_result(db, task)
        _finish_attempt_common(attempt, now)
        if run.status == RunStatus.CANCELLING or task.status == TaskStatus.CANCELLING:
            attempt.status = AttemptStatus.CANCELLED
            attempt.error_category = "cancelled"
            attempt.error_code = "cancelled_after_execution"
            _set_task_status(task, TaskStatus.CANCELLED)
            task.completed_at = now
            _finalize_run(db, run)
            return {"status": "cancelled"}

        attempt.status = AttemptStatus.SUCCEEDED
        attempt.output_payload = output
        attempt.output_hash = canonical_json_hash(output)
        _set_task_status(task, TaskStatus.SUCCEEDED)
        task.completed_at = now
        append_progress(
            db,
            run,
            event_type="task.progress",
            task=task,
            label="Task completed",
            detail=task.workflow_task_key,
            admin_metadata={"attempt_number": attempt.attempt_number},
        )
        _unblock_dependents(db, run, task.id)
        _finalize_run(db, run)
    return {"status": "succeeded", "attempt_number": attempt.attempt_number, "output": output}


def _complete_attempt_failure(
    task_id: uuid.UUID,
    attempt_id: uuid.UUID,
    error: RuntimeTaskError,
    *,
    config: Settings,
) -> dict[str, Any]:
    now = utc_now()
    with session_scope() as db:
        attempt = db.scalar(select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update())
        task = db.scalar(select(TaskRun).where(TaskRun.id == task_id).with_for_update())
        if attempt is None or task is None:
            return {"status": "missing"}
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == task.run_id).with_for_update())
        assert run is not None
        if attempt.status in {item.value for item in ATTEMPT_TERMINAL_STATUSES}:
            return _existing_task_result(db, task)
        _finish_attempt_common(attempt, now)
        attempt.error_category = error.category
        attempt.error_code = error.code
        attempt.retryable = error.retryable

        if error.category == "cancelled" or run.status == RunStatus.CANCELLING:
            attempt.status = AttemptStatus.CANCELLED
            target = TaskStatus.CANCELLED
            if task.status == TaskStatus.RUNNING:
                _set_task_status(task, TaskStatus.CANCELLED)
            elif task.status == TaskStatus.CANCELLING:
                _set_task_status(task, TaskStatus.CANCELLED)
            task.completed_at = now
            _finalize_run(db, run)
            return {"status": target.value, "attempt_number": attempt.attempt_number}

        policy = RetryPolicy.model_validate(task.retry_policy)
        retryable = (
            error.retryable
            and error.category in policy.retryable_categories
            and attempt.attempt_number < task.max_attempts
            and now < task.deadline_at
            and now < run.deadline_at
        )
        attempt.status = AttemptStatus.TIMED_OUT if error.category == "timeout" else AttemptStatus.FAILED
        append_progress(
            db,
            run,
            event_type="task.failed",
            task=task,
            label="Task attempt failed",
            detail="The task will retry." if retryable else "The task cannot continue.",
            admin_metadata={
                "attempt_number": attempt.attempt_number,
                "error_category": error.category,
                "error_code": error.code,
                "retryable": retryable,
            },
        )
        if retryable:
            _set_task_status(task, TaskStatus.QUEUED)
            delay = policy.delay_for_attempt(attempt.attempt_number)
            queue_task_dispatch(db, run, task, available_at=now + timedelta(seconds=delay))
            return {"status": "retrying", "attempt_number": attempt.attempt_number}

        terminal = TaskStatus.TIMED_OUT if error.category == "timeout" else TaskStatus.FAILED
        _set_task_status(task, terminal)
        task.completed_at = now
        _skip_blocked_descendants(db, run, task.id)
        _finalize_run(db, run)
        return {"status": terminal.value, "attempt_number": attempt.attempt_number}


def fail_active_attempt(
    task_id: uuid.UUID,
    *,
    code: str,
    category: str,
    retryable: bool,
    config: Settings = settings,
) -> dict[str, Any]:
    with session_scope() as db:
        attempt_id = db.scalar(
            select(TaskAttempt.id)
            .where(
                TaskAttempt.task_run_id == task_id,
                TaskAttempt.status == AttemptStatus.RUNNING,
            )
            .order_by(TaskAttempt.attempt_number.desc())
        )
    if attempt_id is None:
        return {"status": "no_active_attempt"}
    return _complete_attempt_failure(
        task_id,
        attempt_id,
        RuntimeTaskError(code, category=category, retryable=retryable),
        config=config,
    )


def _unblock_dependents(db: Session, run: AnalysisRun, upstream_id: uuid.UUID) -> None:
    downstream_ids = list(
        db.scalars(
            select(TaskDependency.downstream_task_id).where(
                TaskDependency.upstream_task_id == upstream_id
            )
        )
    )
    for downstream_id in downstream_ids:
        task = db.scalar(select(TaskRun).where(TaskRun.id == downstream_id).with_for_update())
        if task is None or task.status != TaskStatus.BLOCKED:
            continue
        upstream_statuses = list(
            db.scalars(
                select(TaskRun.status)
                .join(TaskDependency, TaskDependency.upstream_task_id == TaskRun.id)
                .where(TaskDependency.downstream_task_id == task.id)
            )
        )
        if upstream_statuses and all(status == TaskStatus.SUCCEEDED for status in upstream_statuses):
            _set_task_status(task, TaskStatus.QUEUED)
            queue_task_dispatch(db, run, task)


def _skip_blocked_descendants(db: Session, run: AnalysisRun, upstream_id: uuid.UUID) -> None:
    pending = [upstream_id]
    visited: set[uuid.UUID] = set()
    while pending:
        current = pending.pop(0)
        downstream_ids = list(
            db.scalars(
                select(TaskDependency.downstream_task_id).where(
                    TaskDependency.upstream_task_id == current
                )
            )
        )
        for downstream_id in downstream_ids:
            if downstream_id in visited:
                continue
            visited.add(downstream_id)
            task = db.scalar(select(TaskRun).where(TaskRun.id == downstream_id).with_for_update())
            if task and task.status in {TaskStatus.BLOCKED, TaskStatus.QUEUED}:
                _set_task_status(task, TaskStatus.SKIPPED)
                task.completed_at = utc_now()
            pending.append(downstream_id)


def _reset_skipped_descendants(db: Session, upstream_id: uuid.UUID) -> None:
    pending = [upstream_id]
    visited: set[uuid.UUID] = set()
    while pending:
        current = pending.pop(0)
        downstream_ids = list(
            db.scalars(
                select(TaskDependency.downstream_task_id).where(
                    TaskDependency.upstream_task_id == current
                )
            )
        )
        for downstream_id in downstream_ids:
            if downstream_id in visited:
                continue
            visited.add(downstream_id)
            task = db.scalar(select(TaskRun).where(TaskRun.id == downstream_id).with_for_update())
            if task and task.status == TaskStatus.SKIPPED:
                _set_task_status(task, TaskStatus.BLOCKED)
                task.completed_at = None
            pending.append(downstream_id)


def _finalize_run(db: Session, run: AnalysisRun) -> None:
    db.flush()
    tasks = list(db.scalars(select(TaskRun).where(TaskRun.run_id == run.id)))
    statuses = {TaskStatus(task.status) for task in tasks}
    now = utc_now()
    if run.status == RunStatus.CANCELLING:
        if statuses & {TaskStatus.RUNNING, TaskStatus.CANCELLING}:
            return
        for task in tasks:
            if task.status in {TaskStatus.BLOCKED, TaskStatus.QUEUED}:
                _set_task_status(task, TaskStatus.CANCELLED)
                task.completed_at = now
        _set_run_status(run, RunStatus.CANCELLED)
        run.cancelled_at = now
        run.completed_at = now
        append_progress(
            db,
            run,
            event_type="run.cancelled",
            label="Analysis cancelled",
            detail="No additional tasks will be dispatched.",
        )
        _close_budget(db, run.id)
        return
    if tasks and all(status == TaskStatus.SUCCEEDED for status in statuses) and len(statuses) == 1:
        _set_run_status(run, RunStatus.COMPLETE)
        run.completed_at = now
        append_progress(
            db,
            run,
            event_type="run.completed",
            label="Analysis completed",
            detail="Every workflow task completed successfully.",
        )
        _close_budget(db, run.id)
        return
    active = statuses & {
        TaskStatus.BLOCKED,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.CANCELLING,
    }
    if not active and statuses & {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
        if run.status == RunStatus.QUEUED:
            _set_run_status(run, RunStatus.FAILED)
        elif run.status == RunStatus.RUNNING:
            _set_run_status(run, RunStatus.FAILED)
        run.error_code = "workflow_task_failed"
        run.completed_at = now
        append_progress(
            db,
            run,
            event_type="run.failed",
            label="Analysis failed",
            detail="A required workflow task could not complete.",
        )
        _close_budget(db, run.id)


def _close_budget(db: Session, run_id: uuid.UUID) -> None:
    budget = db.get(RunBudgetState, run_id)
    if budget and budget.status != "exhausted":
        budget.status = "closed"


def recover_stale_attempts(config: Settings = settings) -> int:
    now = utc_now()
    recovered = 0
    with session_scope() as db:
        attempts = list(
            db.scalars(
                select(TaskAttempt)
                .where(
                    TaskAttempt.status == AttemptStatus.RUNNING,
                    TaskAttempt.lease_expires_at.is_not(None),
                    TaskAttempt.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for attempt in attempts:
            task = db.scalar(select(TaskRun).where(TaskRun.id == attempt.task_run_id).with_for_update())
            if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.CANCELLING}:
                continue
            run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == task.run_id).with_for_update())
            if run is None:
                continue
            _finish_attempt_common(attempt, now)
            if run.status == RunStatus.CANCELLING or task.status == TaskStatus.CANCELLING:
                attempt.status = AttemptStatus.CANCELLED
                attempt.error_category = "cancelled"
                attempt.error_code = "worker_interrupted_during_cancellation"
                _set_task_status(task, TaskStatus.CANCELLED)
                task.completed_at = now
            else:
                attempt.status = AttemptStatus.FAILED
                attempt.error_category = "worker_interrupted"
                attempt.error_code = "attempt_lease_expired"
                attempt.retryable = True
                policy = RetryPolicy.model_validate(task.retry_policy)
                if attempt.attempt_number < task.max_attempts and now < task.deadline_at:
                    _set_task_status(task, TaskStatus.QUEUED)
                    queue_task_dispatch(db, run, task, available_at=now)
                else:
                    _set_task_status(task, TaskStatus.FAILED)
                    task.completed_at = now
                    _skip_blocked_descendants(db, run, task.id)
            append_progress(
                db,
                run,
                event_type="task.failed",
                task=task,
                label="Worker interruption recovered",
                detail="The durable task state was recovered from an expired worker lease.",
                admin_metadata={"attempt_number": attempt.attempt_number, "error_code": attempt.error_code},
            )
            _finalize_run(db, run)
            recovered += 1
    return recovered


def repair_unfinished_runs() -> int:
    repaired = 0
    with session_scope() as db:
        runs = list(
            db.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING)))
                .with_for_update(skip_locked=True)
            )
        )
        for run in runs:
            tasks = list(db.scalars(select(TaskRun).where(TaskRun.run_id == run.id)))
            if run.status == RunStatus.CANCELLING:
                _finalize_run(db, run)
                repaired += 1
                continue
            for task in tasks:
                if task.status == TaskStatus.QUEUED:
                    before = db.scalar(
                        select(func.count(RuntimeOutbox.id)).where(
                            RuntimeOutbox.task_run_id == task.id,
                            RuntimeOutbox.kind == OutboxKind.TASK_DISPATCH,
                            RuntimeOutbox.payload["attempt_number"].as_integer() == task.current_attempt + 1,
                        )
                    )
                    queue_task_dispatch(db, run, task)
                    if not before:
                        repaired += 1
            _finalize_run(db, run)
    return repaired


def reconstruct_run(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise LookupError("analysis run not found")
    snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id)
    budget = db.get(RunBudgetState, run.id)
    tasks = list(
        db.scalars(
            select(TaskRun)
            .where(TaskRun.run_id == run.id)
            .order_by(TaskRun.created_at, TaskRun.workflow_task_key)
        )
    )
    task_ids = [item.id for item in tasks]
    attempts = (
        list(
            db.scalars(
                select(TaskAttempt)
                .where(TaskAttempt.task_run_id.in_(task_ids))
                .order_by(TaskAttempt.created_at, TaskAttempt.attempt_number)
            )
        )
        if task_ids
        else []
    )
    dependencies = (
        list(
            db.execute(
                select(TaskDependency.upstream_task_id, TaskDependency.downstream_task_id)
                .where(TaskDependency.downstream_task_id.in_(task_ids))
                .order_by(TaskDependency.upstream_task_id, TaskDependency.downstream_task_id)
            )
        )
        if task_ids
        else []
    )
    events = list(
        db.scalars(
            select(ProgressEvent)
            .where(ProgressEvent.run_id == run.id)
            .order_by(ProgressEvent.sequence)
        )
    )
    return {
        "run": {
            "id": str(run.id),
            "status": run.status,
            "product_input": run.product_input,
            "canonical_product": run.canonical_product,
            "progress_sequence": run.progress_sequence,
            "deadline_at": run.deadline_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error_code": run.error_code,
        },
        "configuration_snapshot": {
            "id": str(snapshot.id),
            "content_hash": snapshot.content_hash,
            "workflow_version_id": str(snapshot.workflow_version_id),
            "budget_policy_version_id": str(snapshot.budget_policy_version_id),
        },
        "budget": {
            "status": budget.status,
            "max_tokens": budget.max_tokens,
            "max_cost_microusd": budget.max_cost_microusd,
            "reserved_tokens": budget.reserved_tokens,
            "consumed_tokens": budget.consumed_tokens,
        },
        "tasks": [
            {
                "id": str(task.id),
                "task_key": task.workflow_task_key,
                "status": task.status,
                "current_attempt": task.current_attempt,
                "input_hash": canonical_json_hash(task.input_payload),
            }
            for task in tasks
        ],
        "attempts": [
            {
                "id": str(attempt.id),
                "task_run_id": str(attempt.task_run_id),
                "attempt_number": attempt.attempt_number,
                "status": attempt.status,
                "input_hash": attempt.input_hash,
                "output_hash": attempt.output_hash,
                "error_category": attempt.error_category,
                "error_code": attempt.error_code,
            }
            for attempt in attempts
        ],
        "dependencies": [
            {"upstream_task_id": str(row[0]), "downstream_task_id": str(row[1])}
            for row in dependencies
        ],
        "events": [
            {"sequence": event.sequence, "event_type": event.event_type, "payload": event.public_payload}
            for event in events
        ],
    }
