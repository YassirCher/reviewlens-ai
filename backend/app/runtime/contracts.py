from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TASK_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    BLOCKED = "blocked"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"


class OutboxKind(StrEnum):
    TASK_DISPATCH = "task.dispatch"
    TASK_REVOKE = "task.revoke"
    PROGRESS_PUBLISH = "progress.publish"


RUN_TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETE, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED}
)
TASK_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMED_OUT,
    }
)
ATTEMPT_TERMINAL_STATUSES = frozenset(
    {AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.CANCELLED, AttemptStatus.TIMED_OUT}
)

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.COMPLETE, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLING}
    ),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLED}),
    RunStatus.COMPLETE: frozenset(),
    RunStatus.PARTIAL: frozenset(),
    RunStatus.FAILED: frozenset({RunStatus.RUNNING}),
    RunStatus.CANCELLED: frozenset(),
}

TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.BLOCKED: frozenset({TaskStatus.QUEUED, TaskStatus.SKIPPED, TaskStatus.CANCELLED}),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
            TaskStatus.CANCELLING,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.QUEUED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLING,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
        }
    ),
    TaskStatus.CANCELLING: frozenset({TaskStatus.CANCELLED, TaskStatus.SUCCEEDED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED}),
    TaskStatus.SKIPPED: frozenset({TaskStatus.BLOCKED}),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.TIMED_OUT: frozenset({TaskStatus.QUEUED}),
}


class InvalidStateTransition(ValueError):
    pass


def require_run_transition(current: str, target: RunStatus) -> None:
    source = RunStatus(current)
    if target != source and target not in RUN_TRANSITIONS[source]:
        raise InvalidStateTransition(f"analysis run cannot transition from {source} to {target}")


def require_task_transition(current: str, target: TaskStatus) -> None:
    source = TaskStatus(current)
    if target != source and target not in TASK_TRANSITIONS[source]:
        raise InvalidStateTransition(f"task cannot transition from {source} to {target}")


class RetryPolicy(StrictModel):
    max_attempts: int = Field(default=1, ge=1, le=10)
    base_delay_seconds: float = Field(default=1.0, ge=0, le=300)
    max_delay_seconds: float = Field(default=30.0, ge=0, le=900)
    jitter_ratio: float = Field(default=0.1, ge=0, le=1)
    retryable_categories: tuple[str, ...] = ("transient", "timeout", "worker_interrupted")

    @model_validator(mode="after")
    def validate_delays(self) -> "RetryPolicy":
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be greater than or equal to base_delay_seconds")
        if len(set(self.retryable_categories)) != len(self.retryable_categories):
            raise ValueError("retryable_categories must be unique")
        return self

    def delay_for_attempt(self, attempt_number: int, *, random_value: float | None = None) -> float:
        if attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        raw = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt_number - 1)))
        if not raw or not self.jitter_ratio:
            return raw
        sample = random.random() if random_value is None else random_value
        centered = (sample * 2) - 1
        return max(0.0, raw * (1 + centered * self.jitter_ratio))


class WorkflowTaskSpec(StrictModel):
    task_key: str = Field(min_length=1, max_length=120)
    executor_kind: Literal["deterministic"] = "deterministic"
    handler: str = Field(min_length=1, max_length=160)
    dependencies: tuple[str, ...] = ()
    input: dict[str, Any] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: int = Field(default=180, ge=1, le=900)
    priority: int = Field(default=0, ge=-100, le=100)
    weight: int = Field(default=1, ge=1, le=100)
    agent_version_id: uuid.UUID | None = None
    tool_version_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_task(self) -> "WorkflowTaskSpec":
        if not TASK_KEY_PATTERN.fullmatch(self.task_key):
            raise ValueError("task_key must be a lowercase stable identifier")
        if not TASK_KEY_PATTERN.fullmatch(self.handler):
            raise ValueError("handler must be a lowercase stable identifier")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("dependencies must be unique")
        if self.task_key in self.dependencies:
            raise ValueError("a task cannot depend on itself")
        return self


class WorkflowDag(StrictModel):
    schema_version: Literal[1] = 1
    run_timeout_seconds: int = Field(default=900, ge=1, le=3600)
    tasks: tuple[WorkflowTaskSpec, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDag":
        keys = [task.task_key for task in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("workflow task keys must be unique")
        known = set(keys)
        for task in self.tasks:
            missing = set(task.dependencies) - known
            if missing:
                raise ValueError(f"task {task.task_key} has unknown dependencies: {sorted(missing)}")
        self.topological_order()
        return self

    def topological_order(self) -> tuple[WorkflowTaskSpec, ...]:
        by_key = {task.task_key: task for task in self.tasks}
        pending = {task.task_key: set(task.dependencies) for task in self.tasks}
        ordered: list[WorkflowTaskSpec] = []
        while pending:
            ready = [task.task_key for task in self.tasks if task.task_key in pending and not pending[task.task_key]]
            if not ready:
                raise ValueError("workflow DAG contains a cycle")
            for key in ready:
                ordered.append(by_key[key])
                pending.pop(key)
                for dependencies in pending.values():
                    dependencies.discard(key)
        return tuple(ordered)

    @property
    def total_weight(self) -> int:
        return sum(task.weight for task in self.tasks)


PUBLIC_EVENT_TYPES = frozenset(
    {
        "run.queued",
        "run.started",
        "task.started",
        "task.progress",
        "source.completed",
        "task.failed",
        "run.partial",
        "run.completed",
        "run.failed",
        "run.cancelled",
    }
)


class ProgressData(StrictModel):
    sequence: int = Field(ge=1)
    run_id: uuid.UUID
    task_id: uuid.UUID | None = None
    task_key: str | None = None
    label: str = Field(min_length=1, max_length=160)
    detail: str = Field(default="", max_length=500)
    completed_tasks: int = Field(ge=0)
    total_tasks: int = Field(ge=1)
    percent: int = Field(ge=0, le=100)
    timestamp: str
    result_summary: dict[str, Any] | None = None


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_idempotency_key(*parts: object) -> str:
    return canonical_json_hash([str(part) for part in parts])
