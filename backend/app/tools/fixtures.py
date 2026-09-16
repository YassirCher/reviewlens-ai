from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AgentDefinition,
    AgentVersion,
    AgentVersionTool,
    AnalysisRun,
    BudgetPolicy,
    BudgetPolicyVersion,
    TaskAttempt,
    TaskRun,
    ToolDefinition,
    ToolVersion,
    WorkflowDefinition,
    WorkflowVersion,
)
from app.knowledge.service import create_workspace
from app.runtime.contracts import RetryPolicy, WorkflowDag, WorkflowTaskSpec, canonical_json_hash
from app.runtime.service import create_run, utc_now
from app.tools.registry import TOOL_SPECS, seed_tool_registry

ResearchScenario = Literal["complete", "missing_transcript", "comments_off", "partial"]


def _ensure_test(config: Settings) -> None:
    if config.app_env.lower() != "test":
        raise RuntimeError("research fixtures are only available when APP_ENV=test")


def create_research_fixture_attempt(
    db: Session,
    scenario: ResearchScenario,
    *,
    config: Settings = settings,
) -> tuple[AnalysisRun, TaskRun, TaskAttempt]:
    _ensure_test(config)
    seed_tool_registry(db)
    suffix = uuid.uuid4().hex
    budget_definition = BudgetPolicy(
        key=f"phase5-budget-{suffix}", name="Phase 5 fixture budget", description="test"
    )
    db.add(budget_definition)
    db.flush()
    budget_payload = {"scenario": scenario, "source_count": 5}
    budget_version = BudgetPolicyVersion(
        definition_id=budget_definition.id,
        version_number=1,
        lifecycle="published",
        content_hash=canonical_json_hash(budget_payload),
        change_note="Phase 5 deterministic research fixture",
        published_at=datetime.now(timezone.utc),
        public_runs_per_hour=100,
        public_runs_per_day=100,
        public_concurrent_runs=10,
        public_run_cost_cap_usd=Decimal("1"),
        public_daily_cost_cap_usd=Decimal("10"),
        min_video_count=3,
        default_video_count=5,
        max_video_count=8,
        comments_enabled_default=scenario != "comments_off",
        token_limits={"run_total_tokens": 100000},
    )
    db.add(budget_version)

    agent_definition = db.scalar(
        select(AgentDefinition).where(AgentDefinition.key == "deterministic")
    )
    if agent_definition is None:
        agent_definition = AgentDefinition(
            key="deterministic",
            name="Deterministic worker",
            description="Checked-in deterministic tool executor",
        )
        db.add(agent_definition)
        db.flush()
    next_version = (
        db.scalar(
            select(func.coalesce(func.max(AgentVersion.version_number), 0)).where(
                AgentVersion.definition_id == agent_definition.id
            )
        )
        or 0
    ) + 1
    agent_version = AgentVersion(
        definition_id=agent_definition.id,
        version_number=next_version,
        lifecycle="draft",
        content_hash=canonical_json_hash({"scenario": scenario, "suffix": suffix}),
        change_note="Phase 5 deterministic research fixture",
        system_prompt="Deterministic fixture; no model call is permitted.",
        output_schema={"type": "object"},
        retrieval_policy={},
        budget_policy_version_id=budget_version.id,
    )
    db.add(agent_version)
    db.flush()
    versions: list[ToolVersion] = []
    for spec in TOOL_SPECS:
        definition = db.scalar(select(ToolDefinition).where(ToolDefinition.key == spec.key))
        assert definition is not None
        version = db.scalar(
            select(ToolVersion).where(
                ToolVersion.definition_id == definition.id,
                ToolVersion.semantic_version == spec.semantic_version,
            )
        )
        assert version is not None
        versions.append(version)
        db.add(
            AgentVersionTool(
                agent_version_id=agent_version.id,
                tool_version_id=version.id,
            )
        )
    db.flush()
    agent_version.lifecycle = "published"
    agent_version.published_at = datetime.now(timezone.utc)

    dag = WorkflowDag(
        run_timeout_seconds=600,
        tasks=(
            WorkflowTaskSpec(
                task_key="fixture.research",
                handler="fixture.echo",
                agent_version_id=agent_version.id,
                timeout_seconds=590,
                retry=RetryPolicy(max_attempts=1),
            ),
        ),
    )
    workflow_definition = WorkflowDefinition(
        key=f"phase5-workflow-{suffix}", name="Phase 5 research fixture", description="test"
    )
    db.add(workflow_definition)
    db.flush()
    workflow_version = WorkflowVersion(
        definition_id=workflow_definition.id,
        version_number=1,
        lifecycle="published",
        content_hash=canonical_json_hash(dag.model_dump(mode="json")),
        change_note="Phase 5 deterministic research fixture",
        published_at=datetime.now(timezone.utc),
        dag=dag.model_dump(mode="json"),
    )
    db.add(workflow_version)
    db.flush()

    active = db.get(ActiveConfiguration, 1)
    if active is None:
        active = ActiveConfiguration(id=1, environment=config.app_env)
        db.add(active)
    active.workflow_version_id = workflow_version.id
    active.budget_policy_version_id = budget_version.id
    active.kill_switch = False
    db.flush()
    analyze_comments = scenario not in {"comments_off", "missing_transcript"}
    run = create_run(
        db,
        product_name=f"Phase 5 {scenario.replace('_', ' ')} fixture",
        initiator_type="system_fixture",
        requested_options={
            "scenario": scenario,
            "analyze_comments": analyze_comments,
            "contacts_openrouter": False,
        },
    )
    db.flush()
    create_workspace(db, run.id, config=config)
    task = db.scalar(select(TaskRun).where(TaskRun.run_id == run.id))
    assert task is not None
    now = utc_now()
    attempt = TaskAttempt(
        task_run_id=task.id,
        attempt_number=1,
        status="running",
        input_payload={"scenario": scenario},
        input_hash=canonical_json_hash({"scenario": scenario}),
        started_at=now,
        heartbeat_at=now,
        lease_expires_at=task.deadline_at,
        created_at=now,
    )
    db.add(attempt)
    task.status = "running"
    task.current_attempt = 1
    task.started_at = now
    run.status = "running"
    run.started_at = now
    db.flush()
    return run, task, attempt


def finish_research_fixture(
    db: Session,
    run_id: uuid.UUID,
    task_id: uuid.UUID,
    attempt_id: uuid.UUID,
    output: dict,
) -> None:
    now = utc_now()
    run = db.get(AnalysisRun, run_id)
    task = db.get(TaskRun, task_id)
    attempt = db.get(TaskAttempt, attempt_id)
    assert run and task and attempt
    attempt.status = "succeeded"
    attempt.output_payload = output
    attempt.output_hash = canonical_json_hash(output)
    attempt.ended_at = now
    started = attempt.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    attempt.duration_ms = max(0, round((now - started).total_seconds() * 1000))
    attempt.lease_expires_at = None
    task.status = "succeeded"
    task.completed_at = now
    run.status = "complete"
    run.completed_at = now
