from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AgentDefinition,
    AgentVersion,
    BudgetPolicy,
    BudgetPolicyVersion,
    EmbeddingPolicy,
    EmbeddingPolicyVersion,
    ModelPolicy,
    ModelPolicyVersion,
    TaskAttempt,
    TaskRun,
    WorkflowDefinition,
    WorkflowVersion,
)
from app.llmops.contracts import (
    EmbeddingPolicyDocument,
    InvocationContext,
    ModelPolicyDocument,
    ProviderRouting,
)
from app.runtime.contracts import RetryPolicy, WorkflowDag, WorkflowTaskSpec, canonical_json_hash
from app.runtime.service import create_run, utc_now


def _ensure_test(config: Settings) -> None:
    if config.app_env.lower() != "test":
        raise RuntimeError("LLMOps fixtures are only available when APP_ENV=test")


def create_llmops_fixture_attempt(
    db: Session,
    *,
    initiator_type: str = "system_fixture",
    run_token_cap: int = 10_000,
    run_cost_cap_microusd: int = 100_000,
    config: Settings = settings,
) -> tuple[InvocationContext, ModelPolicyDocument, EmbeddingPolicyDocument]:
    _ensure_test(config)
    suffix = uuid.uuid4().hex
    model_document = ModelPolicyDocument(
        name="Phase 3 mocked chat",
        purpose="Exercise the paid gateway without external calls",
        models=("fixture/chat-model", "fixture/chat-fallback"),
        provider=ProviderRouting(),
        temperature=0,
        max_completion_tokens=128,
    )
    model_definition = ModelPolicy(key=f"phase3-model-{suffix}", name="Phase 3 model", description="test")
    db.add(model_definition)
    db.flush()
    model_version = ModelPolicyVersion(
        definition_id=model_definition.id,
        version_number=1,
        lifecycle="published",
        content_hash=canonical_json_hash(model_document.model_dump(mode="json")),
        change_note="Phase 3 mocked fixture",
        published_at=datetime.now(timezone.utc),
        policy=model_document.model_dump(mode="json"),
    )
    db.add(model_version)

    embedding_document = EmbeddingPolicyDocument(
        model="fixture/embedding-model",
        provider=ProviderRouting(),
        dimensions=3,
    )
    embedding_definition = EmbeddingPolicy(
        key=f"phase3-embedding-{suffix}", name="Phase 3 embedding", description="test"
    )
    db.add(embedding_definition)
    db.flush()
    embedding_version = EmbeddingPolicyVersion(
        definition_id=embedding_definition.id,
        version_number=1,
        lifecycle="published",
        content_hash=canonical_json_hash(embedding_document.model_dump(mode="json")),
        change_note="Phase 3 mocked fixture",
        published_at=datetime.now(timezone.utc),
        model_slug=embedding_document.model,
        provider_policy=embedding_document.provider.model_dump(mode="json"),
        dimensions=embedding_document.dimensions,
        chunking_version="fixture-v1",
        eligible_node_types=["fixture"],
    )
    db.add(embedding_version)

    budget_definition = BudgetPolicy(key=f"phase3-budget-{suffix}", name="Phase 3 budget", description="test")
    db.add(budget_definition)
    db.flush()
    budget_version = BudgetPolicyVersion(
        definition_id=budget_definition.id,
        version_number=1,
        lifecycle="published",
        content_hash=canonical_json_hash({"suffix": suffix, "run_token_cap": run_token_cap}),
        change_note="Phase 3 mocked fixture",
        published_at=datetime.now(timezone.utc),
        public_runs_per_hour=3,
        public_runs_per_day=10,
        public_concurrent_runs=2,
        public_run_cost_cap_usd=Decimal(run_cost_cap_microusd) / Decimal(1_000_000),
        public_daily_cost_cap_usd=Decimal("25"),
        min_video_count=3,
        default_video_count=5,
        max_video_count=8,
        comments_enabled_default=False,
        token_limits={"run_total_tokens": run_token_cap},
    )
    db.add(budget_version)

    agent_definition = AgentDefinition(key=f"phase3-agent-{suffix}", name="Phase 3 agent", description="test")
    db.add(agent_definition)
    db.flush()
    agent_version = AgentVersion(
        definition_id=agent_definition.id,
        version_number=1,
        lifecycle="published",
        content_hash=canonical_json_hash({"suffix": suffix, "model": str(model_version.id)}),
        change_note="Phase 3 mocked fixture",
        published_at=datetime.now(timezone.utc),
        system_prompt="Return only fixture JSON.",
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        retrieval_policy={},
        model_policy_version_id=model_version.id,
        budget_policy_version_id=budget_version.id,
    )
    db.add(agent_version)
    db.flush()

    dag = WorkflowDag(
        run_timeout_seconds=120,
        tasks=(
            WorkflowTaskSpec(
                task_key="fixture.llm",
                handler="fixture.echo",
                retry=RetryPolicy(max_attempts=1),
                timeout_seconds=90,
                agent_version_id=agent_version.id,
            ),
        ),
    )
    workflow_definition = WorkflowDefinition(
        key=f"phase3-workflow-{suffix}", name="Phase 3 workflow", description="test"
    )
    db.add(workflow_definition)
    db.flush()
    workflow_version = WorkflowVersion(
        definition_id=workflow_definition.id,
        version_number=1,
        lifecycle="published",
        content_hash=canonical_json_hash(dag.model_dump(mode="json")),
        change_note="Phase 3 mocked fixture",
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
    active.embedding_policy_version_id = embedding_version.id
    active.kill_switch = False
    db.flush()

    run = create_run(
        db,
        product_name="Phase 3 mocked fixture",
        initiator_type=initiator_type,
        requested_options={"contacts_upstreams": False},
    )
    db.flush()
    task = db.scalar(select(TaskRun).where(TaskRun.run_id == run.id))
    assert task is not None
    now = utc_now()
    attempt = TaskAttempt(
        task_run_id=task.id,
        attempt_number=1,
        status="running",
        input_payload={},
        input_hash=canonical_json_hash({}),
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
    return (
        InvocationContext(
            run_id=run.id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            agent_version_id=agent_version.id,
            workflow_version_id=workflow_version.id,
            model_policy_version_id=model_version.id,
            embedding_policy_version_id=embedding_version.id,
            call_key="fixture.call",
            deadline_at=task.deadline_at,
            initiator_type=initiator_type,
        ),
        model_document,
        embedding_document,
    )
