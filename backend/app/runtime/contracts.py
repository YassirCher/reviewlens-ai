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
    executor_kind: Literal["deterministic", "agent"] = "deterministic"
    handler: str = Field(min_length=1, max_length=160)
    dependencies: tuple[str, ...] = ()
    input: dict[str, Any] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: int = Field(default=180, ge=1, le=900)
    priority: int = Field(default=0, ge=-100, le=100)
    weight: int = Field(default=1, ge=1, le=100)
    agent_version_id: uuid.UUID | None = None
    tool_version_id: uuid.UUID | None = None
    allowed_tool_version_ids: tuple[uuid.UUID, ...] = ()
    source_key: str | None = Field(default=None, max_length=320)
    dependency_mode: Literal["all_succeeded", "all_terminal_min_success"] = "all_succeeded"
    minimum_successes: int = Field(default=0, ge=0, le=200)
    optional: bool = False

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
        if self.executor_kind == "agent" and self.agent_version_id is None:
            raise ValueError("agent tasks require an agent version")
        tool_ids = (*self.allowed_tool_version_ids, *((self.tool_version_id,) if self.tool_version_id else ()))
        if len(set(tool_ids)) != len(tool_ids):
            raise ValueError("task tool versions must be unique")
        if self.dependency_mode == "all_terminal_min_success" and self.minimum_successes < 1:
            raise ValueError("terminal fan-in tasks require at least one successful dependency")
        return self


class WorkflowTaskTemplate(StrictModel):
    template_key: str = Field(min_length=1, max_length=120)
    task_key: str = Field(min_length=1, max_length=140)
    executor_kind: Literal["deterministic", "agent"] = "deterministic"
    handler: str = Field(min_length=1, max_length=160)
    dependencies: tuple[str, ...] = ()
    input: dict[str, Any] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: int = Field(default=180, ge=1, le=900)
    priority: int = Field(default=0, ge=-100, le=100)
    weight: int = Field(default=1, ge=1, le=100)
    agent_version_id: uuid.UUID | None = None
    tool_version_id: uuid.UUID | None = None
    allowed_tool_version_ids: tuple[uuid.UUID, ...] = ()
    fanout: Literal["none", "source_slots"] = "none"
    conditional: Literal["always", "comments_enabled"] = "always"
    dependency_mode: Literal["all_succeeded", "all_terminal_min_success"] = "all_succeeded"
    minimum_successes: int = Field(default=0, ge=0, le=200)
    optional: bool = False

    @model_validator(mode="after")
    def validate_template(self) -> "WorkflowTaskTemplate":
        if not TASK_KEY_PATTERN.fullmatch(self.template_key):
            raise ValueError("template_key must be a lowercase stable identifier")
        candidate = self.task_key.replace("{index}", "1")
        if not TASK_KEY_PATTERN.fullmatch(candidate):
            raise ValueError("task template key must materialize to a stable identifier")
        if self.fanout == "source_slots" and "{index}" not in self.task_key:
            raise ValueError("source fan-out task keys require an {index} placeholder")
        if self.fanout == "none" and "{index}" in self.task_key:
            raise ValueError("non-fan-out task keys cannot contain {index}")
        if not TASK_KEY_PATTERN.fullmatch(self.handler):
            raise ValueError("handler must be a lowercase stable identifier")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("template dependencies must be unique")
        if self.template_key in self.dependencies:
            raise ValueError("a task template cannot depend on itself")
        if self.executor_kind == "agent" and self.agent_version_id is None:
            raise ValueError("agent task templates require an agent version")
        if self.dependency_mode == "all_terminal_min_success" and self.minimum_successes < 1:
            raise ValueError("terminal fan-in templates require at least one successful dependency")
        return self


class WorkflowDag(StrictModel):
    schema_version: Literal[1, 2] = 1
    run_timeout_seconds: int = Field(default=900, ge=1, le=3600)
    tasks: tuple[WorkflowTaskSpec, ...] = Field(default=(), max_length=200)
    templates: tuple[WorkflowTaskTemplate, ...] = Field(default=(), max_length=40)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDag":
        if self.schema_version == 1:
            if not self.tasks or self.templates:
                raise ValueError("workflow schema version 1 requires tasks and no templates")
        else:
            if not self.templates or self.tasks:
                raise ValueError("workflow schema version 2 requires templates and no materialized tasks")
            template_keys = [item.template_key for item in self.templates]
            if len(template_keys) != len(set(template_keys)):
                raise ValueError("workflow template keys must be unique")
            known_templates = set(template_keys)
            for item in self.templates:
                missing = set(item.dependencies) - known_templates
                if missing:
                    raise ValueError(
                        f"template {item.template_key} has unknown dependencies: {sorted(missing)}"
                    )
            self._template_order()
            return self
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

    def _template_order(self) -> tuple[WorkflowTaskTemplate, ...]:
        by_key = {item.template_key: item for item in self.templates}
        pending = {item.template_key: set(item.dependencies) for item in self.templates}
        ordered: list[WorkflowTaskTemplate] = []
        while pending:
            ready = [item.template_key for item in self.templates if item.template_key in pending and not pending[item.template_key]]
            if not ready:
                raise ValueError("workflow template DAG contains a cycle")
            for key in ready:
                ordered.append(by_key[key])
                pending.pop(key)
                for dependencies in pending.values():
                    dependencies.discard(key)
        return tuple(ordered)

    def materialize(self, *, source_count: int, comments_enabled: bool) -> "WorkflowDag":
        if self.schema_version == 1:
            return self
        if source_count < 3 or source_count > 8:
            raise ValueError("analysis workflows require 3-8 source slots")
        included = tuple(
            item
            for item in self._template_order()
            if item.conditional == "always" or comments_enabled
        )
        included_keys = {item.template_key for item in included}
        concrete_keys: dict[str, tuple[str, ...]] = {}
        for item in included:
            concrete_keys[item.template_key] = (
                tuple(item.task_key.replace("{index}", str(index)) for index in range(1, source_count + 1))
                if item.fanout == "source_slots"
                else (item.task_key,)
            )
        materialized: list[WorkflowTaskSpec] = []
        for item in included:
            indexes: tuple[int | None, ...] = (
                tuple(range(1, source_count + 1)) if item.fanout == "source_slots" else (None,)
            )
            for index in indexes:
                task_key = item.task_key.replace("{index}", str(index)) if index else item.task_key
                dependencies: list[str] = []
                for dependency in item.dependencies:
                    if dependency not in included_keys:
                        continue
                    upstream = next(candidate for candidate in included if candidate.template_key == dependency)
                    if item.fanout == "source_slots" and upstream.fanout == "source_slots":
                        assert index is not None
                        dependencies.append(upstream.task_key.replace("{index}", str(index)))
                    else:
                        dependencies.extend(concrete_keys[dependency])
                input_payload = dict(item.input)
                if index is not None:
                    input_payload["source_index"] = index
                materialized.append(
                    WorkflowTaskSpec(
                        task_key=task_key,
                        executor_kind=item.executor_kind,
                        handler=item.handler,
                        dependencies=tuple(dependencies),
                        input=input_payload,
                        retry=item.retry,
                        timeout_seconds=item.timeout_seconds,
                        priority=item.priority,
                        weight=item.weight,
                        agent_version_id=item.agent_version_id,
                        tool_version_id=item.tool_version_id,
                        allowed_tool_version_ids=item.allowed_tool_version_ids,
                        source_key=f"slot-{index}" if index is not None else None,
                        dependency_mode=item.dependency_mode,
                        minimum_successes=item.minimum_successes,
                        optional=item.optional,
                    )
                )
        return WorkflowDag(
            schema_version=1,
            run_timeout_seconds=self.run_timeout_seconds,
            tasks=tuple(materialized),
        )

    def topological_order(self) -> tuple[WorkflowTaskSpec, ...]:
        if self.schema_version != 1:
            raise ValueError("workflow templates must be materialized before execution")
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
        if self.schema_version != 1:
            raise ValueError("workflow templates must be materialized before weighting")
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
