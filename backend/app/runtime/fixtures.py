from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AnalysisRun,
    BudgetPolicy,
    BudgetPolicyVersion,
    WorkflowDefinition,
    WorkflowVersion,
)
from app.runtime.contracts import RetryPolicy, WorkflowDag, WorkflowTaskSpec, canonical_json_hash
from app.runtime.service import RuntimeTaskCancelled, RuntimeTaskError, create_run

FixtureScenario = Literal["success", "retry_once", "cancel"]


def fixture_dag(scenario: FixtureScenario) -> WorkflowDag:
    work_handler = "fixture.fail_once" if scenario == "retry_once" else "fixture.echo"
    resilient_retry = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=0,
        max_delay_seconds=0,
        jitter_ratio=0,
    )
    work_retry = resilient_retry
    if scenario == "cancel":
        return WorkflowDag(
            run_timeout_seconds=120,
            tasks=(
                WorkflowTaskSpec(
                    task_key="fixture.wait",
                    handler="fixture.wait_for_cancel",
                    input={"max_wait_seconds": 60},
                    retry=RetryPolicy(max_attempts=1),
                    timeout_seconds=90,
                ),
                WorkflowTaskSpec(
                    task_key="fixture.downstream",
                    handler="fixture.echo",
                    dependencies=("fixture.wait",),
                    input={"value": "must-not-run"},
                ),
            ),
        )
    return WorkflowDag(
        run_timeout_seconds=120,
        tasks=(
            WorkflowTaskSpec(
                task_key="fixture.prepare",
                handler="fixture.echo",
                input={"value": "prepared"},
                retry=resilient_retry,
            ),
            WorkflowTaskSpec(
                task_key="fixture.work",
                handler=work_handler,
                dependencies=("fixture.prepare",),
                input={"value": "worked"},
                retry=work_retry,
            ),
            WorkflowTaskSpec(
                task_key="fixture.finish",
                handler="fixture.echo",
                dependencies=("fixture.work",),
                input={"value": "finished"},
                retry=resilient_retry,
            ),
        ),
    )


def _ensure_test_environment(config: Settings) -> None:
    if config.app_env.lower() != "test":
        raise RuntimeError("runtime fixtures are only available when APP_ENV=test")


def install_fixture_configuration(
    db: Session,
    scenario: FixtureScenario,
    config: Settings = settings,
) -> tuple[WorkflowVersion, BudgetPolicyVersion]:
    _ensure_test_environment(config)
    dag = fixture_dag(scenario)
    workflow_key = f"phase2-fixture-{scenario}"
    workflow_definition = db.scalar(
        select(WorkflowDefinition).where(WorkflowDefinition.key == workflow_key)
    )
    if workflow_definition is None:
        workflow_definition = WorkflowDefinition(
            id=uuid.uuid4(),
            key=workflow_key,
            name=f"Phase 2 {scenario} fixture",
            description="Test-only deterministic durable-runtime fixture",
        )
        db.add(workflow_definition)
        db.flush()
    workflow_payload = dag.model_dump(mode="json")
    workflow_hash = canonical_json_hash(workflow_payload)
    workflow_version = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == workflow_definition.id,
            WorkflowVersion.version_number == 1,
        )
    )
    if workflow_version is None:
        workflow_version = WorkflowVersion(
            id=uuid.uuid4(),
            definition_id=workflow_definition.id,
            version_number=1,
            lifecycle="published",
            content_hash=workflow_hash,
            change_note="Phase 2 deterministic verification fixture",
            published_at=datetime.now(timezone.utc),
            dag=workflow_payload,
        )
        db.add(workflow_version)
    elif workflow_version.content_hash != workflow_hash:
        raise RuntimeError("the existing Phase 2 fixture workflow conflicts with checked-in code")

    budget_definition = db.scalar(
        select(BudgetPolicy).where(BudgetPolicy.key == "phase2-fixture-budget")
    )
    if budget_definition is None:
        budget_definition = BudgetPolicy(
            id=uuid.uuid4(),
            key="phase2-fixture-budget",
            name="Phase 2 fixture budget",
            description="Test-only execution envelope with no paid calls",
        )
        db.add(budget_definition)
        db.flush()
    budget_payload = {
        "public_runs_per_hour": config.public_runs_per_hour,
        "public_runs_per_day": config.public_runs_per_day,
        "public_concurrent_runs": config.public_concurrent_runs,
        "public_run_cost_cap_usd": str(config.public_run_cost_cap_usd),
        "public_daily_cost_cap_usd": str(config.public_daily_cost_cap_usd),
        "min_video_count": config.min_video_count,
        "default_video_count": config.default_video_count,
        "max_video_count": config.max_video_count,
        "comments_enabled_default": False,
        "token_limits": {"run_total_tokens": 100_000},
    }
    budget_hash = canonical_json_hash(budget_payload)
    budget_version = db.scalar(
        select(BudgetPolicyVersion).where(
            BudgetPolicyVersion.definition_id == budget_definition.id,
            BudgetPolicyVersion.version_number == 1,
        )
    )
    if budget_version is None:
        budget_version = BudgetPolicyVersion(
            id=uuid.uuid4(),
            definition_id=budget_definition.id,
            version_number=1,
            lifecycle="published",
            content_hash=budget_hash,
            change_note="Phase 2 deterministic verification fixture",
            published_at=datetime.now(timezone.utc),
            public_runs_per_hour=config.public_runs_per_hour,
            public_runs_per_day=config.public_runs_per_day,
            public_concurrent_runs=config.public_concurrent_runs,
            public_run_cost_cap_usd=Decimal(str(config.public_run_cost_cap_usd)),
            public_daily_cost_cap_usd=Decimal(str(config.public_daily_cost_cap_usd)),
            min_video_count=config.min_video_count,
            default_video_count=config.default_video_count,
            max_video_count=config.max_video_count,
            comments_enabled_default=False,
            token_limits={"run_total_tokens": 100_000},
        )
        db.add(budget_version)
    elif budget_version.content_hash != budget_hash:
        raise RuntimeError("the existing Phase 2 fixture budget conflicts with checked-in code")

    db.flush()
    active = db.get(ActiveConfiguration, 1)
    if active is None:
        active = ActiveConfiguration(
            id=1,
            environment=config.app_env,
            public_analysis_enabled=config.public_analysis_enabled,
            feature_flags={"runtime_fixture": True},
        )
        db.add(active)
    elif active.environment != config.app_env:
        raise RuntimeError("active configuration belongs to a different APP_ENV")
    active.workflow_version_id = workflow_version.id
    active.budget_policy_version_id = budget_version.id
    active.feature_flags = {**active.feature_flags, "runtime_fixture": True}
    db.flush()
    return workflow_version, budget_version


def create_fixture_run(
    db: Session,
    scenario: FixtureScenario,
    config: Settings = settings,
) -> AnalysisRun:
    install_fixture_configuration(db, scenario, config)
    return create_run(
        db,
        product_name=f"Phase 2 {scenario} fixture",
        initiator_type="system_fixture",
        requested_options={"scenario": scenario, "contacts_upstreams": False},
    )


def execute_deterministic_handler(
    handler: str,
    payload: dict[str, Any],
    *,
    attempt_number: int,
    should_cancel: Callable[[], bool],
    heartbeat: Callable[[], None],
) -> dict[str, Any]:
    if should_cancel():
        raise RuntimeTaskCancelled()
    if handler == "fixture.echo":
        heartbeat()
        return {"value": payload.get("value"), "attempt_number": attempt_number}
    if handler == "fixture.fail_once":
        heartbeat()
        if attempt_number == 1:
            raise RuntimeTaskError("fixture_transient", category="transient", retryable=True)
        return {"value": payload.get("value"), "attempt_number": attempt_number}
    if handler == "fixture.wait_for_cancel":
        deadline = time.monotonic() + float(payload.get("max_wait_seconds", 60))
        while time.monotonic() < deadline:
            if should_cancel():
                raise RuntimeTaskCancelled()
            heartbeat()
            time.sleep(0.2)
        raise RuntimeTaskError("fixture_cancel_not_requested", category="fixture", retryable=False)
    raise RuntimeTaskError("unknown_deterministic_handler", category="configuration", retryable=False)
