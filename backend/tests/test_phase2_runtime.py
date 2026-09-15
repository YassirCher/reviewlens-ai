from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db.base import Base
from app.runtime.contracts import (
    InvalidStateTransition,
    ProgressData,
    RetryPolicy,
    RunStatus,
    TaskStatus,
    WorkflowDag,
    require_run_transition,
    require_task_transition,
)
from app.runtime.fixtures import fixture_dag, install_fixture_configuration
from app.runtime.service import normalize_product_name


def test_workflow_dag_is_deterministic_and_rejects_unknown_or_cyclic_dependencies() -> None:
    dag = fixture_dag("success")
    assert [item.task_key for item in dag.topological_order()] == [
        "fixture.prepare",
        "fixture.work",
        "fixture.finish",
    ]
    assert dag.total_weight == 3

    with pytest.raises(ValidationError, match="unknown dependencies"):
        WorkflowDag.model_validate(
            {
                "schema_version": 1,
                "tasks": [
                    {
                        "task_key": "fixture.one",
                        "handler": "fixture.echo",
                        "dependencies": ["fixture.missing"],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="contains a cycle"):
        WorkflowDag.model_validate(
            {
                "schema_version": 1,
                "tasks": [
                    {
                        "task_key": "fixture.one",
                        "handler": "fixture.echo",
                        "dependencies": ["fixture.two"],
                    },
                    {
                        "task_key": "fixture.two",
                        "handler": "fixture.echo",
                        "dependencies": ["fixture.one"],
                    },
                ],
            }
        )


def test_runtime_state_machines_allow_only_declared_transitions() -> None:
    require_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
    require_task_transition(TaskStatus.RUNNING, TaskStatus.QUEUED)
    with pytest.raises(InvalidStateTransition):
        require_run_transition(RunStatus.COMPLETE, RunStatus.RUNNING)
    with pytest.raises(InvalidStateTransition):
        require_task_transition(TaskStatus.SUCCEEDED, TaskStatus.QUEUED)


def test_retry_policy_uses_bounded_exponential_backoff_and_jitter() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=2,
        max_delay_seconds=5,
        jitter_ratio=0.25,
    )
    assert policy.delay_for_attempt(1, random_value=0.5) == 2
    assert policy.delay_for_attempt(2, random_value=0.5) == 4
    assert policy.delay_for_attempt(3, random_value=0.5) == 5
    assert policy.delay_for_attempt(1, random_value=0) == 1.5


def test_progress_payload_is_strict_and_bounded() -> None:
    payload = ProgressData(
        sequence=1,
        run_id=uuid.uuid4(),
        label="Queued",
        completed_tasks=0,
        total_tasks=3,
        percent=0,
        timestamp="2026-09-15T00:00:00+00:00",
    )
    assert payload.percent == 0
    with pytest.raises(ValidationError):
        ProgressData.model_validate({**payload.model_dump(), "secret": "must-not-pass"})
    with pytest.raises(ValidationError):
        ProgressData.model_validate({**payload.model_dump(), "percent": 101})


def test_product_normalization_preserves_model_punctuation() -> None:
    display, canonical = normalize_product_name("  Sony   WH-1000XM6 / Mk.2  ")
    assert display == "Sony WH-1000XM6 / Mk.2"
    assert canonical == "sony wh-1000xm6 / mk.2"


def test_runtime_schema_has_constraints_and_defers_later_phase_tables() -> None:
    tables = Base.metadata.tables
    expected = {
        "configuration_snapshots",
        "analysis_runs",
        "run_budget_states",
        "task_runs",
        "task_dependencies",
        "task_attempts",
        "progress_events",
        "runtime_outbox",
    }
    assert expected <= set(tables)
    assert {"max_tokens", "max_cost_microusd", "reserved_tokens", "consumed_tokens"} <= set(
        tables["run_budget_states"].c.keys()
    )
    assert {"lease_expires_at", "heartbeat_at", "error_category", "error_code"} <= set(
        tables["task_attempts"].c.keys()
    )
    # Phase 3 now owns the accounting tables that Phase 2 intentionally deferred.
    assert "usage_events" in tables
    assert "budget_reservations" in tables
    assert "reports" not in tables


def test_fixture_installation_rejects_non_test_environments() -> None:
    config = Settings(_env_file=None, app_env="development")
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        install_fixture_configuration(None, "success", config)  # type: ignore[arg-type]
