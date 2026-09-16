from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.registry import (
    AGENT_SPECS,
    EVALUATION_SUITE_HASH,
    EVALUATION_SUITE_VERSION,
    evaluate_agent_spec,
)
from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AgentDefinition,
    AgentEvaluationResult,
    AgentVersion,
    AgentVersionTool,
    BudgetPolicy,
    BudgetPolicyVersion,
    ModelPolicy,
    ModelPolicyVersion,
    ToolDefinition,
    ToolVersion,
    WorkflowDefinition,
    WorkflowVersion,
)
from app.llmops.contracts import ModelPolicyDocument, ProviderRouting
from app.llmops.policies import publish_model_policy, validate_model_policy
from app.runtime.contracts import (
    RetryPolicy,
    WorkflowDag,
    WorkflowTaskTemplate,
    canonical_json_hash,
)
from app.tools.registry import TOOL_REGISTRY, seed_tool_registry


class AnalysisConfigurationConflict(RuntimeError):
    pass


def _next_version(db: Session, model: type, definition_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.max(model.version_number), 0)).where(
                model.definition_id == definition_id
            )
        )
        or 0
    ) + 1


def _definition(
    db: Session,
    model: type,
    *,
    key: str,
    name: str,
    description: str,
):
    row = db.scalar(select(model).where(model.key == key))
    if row is None:
        row = model(key=key, name=name, description=description)
        db.add(row)
        db.flush()
    elif row.name != name or row.description != description:
        raise AnalysisConfigurationConflict(f"definition conflicts with checked-in configuration: {key}")
    return row


def _tool_versions(db: Session) -> dict[str, ToolVersion]:
    versions: dict[str, ToolVersion] = {}
    for key, spec in TOOL_REGISTRY.items():
        definition = db.scalar(select(ToolDefinition).where(ToolDefinition.key == key))
        version = db.scalar(
            select(ToolVersion).where(
                ToolVersion.definition_id == definition.id if definition else False,
                ToolVersion.semantic_version == spec.semantic_version,
            )
        )
        if not version or version.lifecycle != "published" or version.content_hash != spec.content_hash:
            raise AnalysisConfigurationConflict(f"published tool is unavailable: {key}@{spec.semantic_version}")
        versions[key] = version
    return versions


def _model_policy(db: Session, config: Settings) -> ModelPolicyVersion:
    document = ModelPolicyDocument(
        name="ReviewLens V2 bounded analysis",
        purpose="Strict structured analysis for the seven bounded ReviewLens roles.",
        models=config.v2_agent_model_slugs,
        provider=ProviderRouting(
            mode="all_compatible",
            allow_fallbacks=False,
            require_parameters=True,
            data_collection="deny",
        ),
        temperature=0.1,
        minimum_context_tokens=18_000,
        max_completion_tokens=4_500,
        compatibility_mode="strict",
    )
    payload = document.model_dump(mode="json")
    content_hash = canonical_json_hash(payload)
    definition = _definition(
        db,
        ModelPolicy,
        key="v2-default-agent-chat",
        name="V2 default agent chat policy",
        description="Strict privacy-denying policy for bounded V2 agents.",
    )
    existing = db.scalar(
        select(ModelPolicyVersion).where(
            ModelPolicyVersion.definition_id == definition.id,
            ModelPolicyVersion.content_hash == content_hash,
        )
    )
    if existing:
        if existing.lifecycle != "published" or existing.policy != payload:
            raise AnalysisConfigurationConflict("existing V2 model policy conflicts with checked-in policy")
        validate_model_policy(db, document, acknowledge_stale=False, config=config)
        return existing
    version = ModelPolicyVersion(
        definition_id=definition.id,
        version_number=_next_version(db, ModelPolicyVersion, definition.id),
        lifecycle="draft",
        content_hash=content_hash,
        change_note="Phase 6 initial bounded-agent policy",
        policy=payload,
    )
    db.add(version)
    db.flush()
    publish_model_policy(db, version, acknowledge_stale=False, config=config)
    return version


def _budget_policy(db: Session, config: Settings) -> BudgetPolicyVersion:
    definition = _definition(
        db,
        BudgetPolicy,
        key="v2-default-analysis-budget",
        name="V2 default analysis budget",
        description="Bounded public analysis limits for the seven-agent workflow.",
    )
    payload = {
        "public_runs_per_hour": config.public_runs_per_hour,
        "public_runs_per_day": config.public_runs_per_day,
        "public_concurrent_runs": config.public_concurrent_runs,
        "public_run_cost_cap_usd": str(config.public_run_cost_cap_usd),
        "public_daily_cost_cap_usd": str(config.public_daily_cost_cap_usd),
        "min_video_count": config.min_video_count,
        "default_video_count": config.default_video_count,
        "max_video_count": config.max_video_count,
        "comments_enabled_default": False,
        "token_limits": {
            "run_total_tokens": 250_000,
            "task_total_tokens": {"default": 30_000},
            "task_cost_microusd": {"default": 1_000_000},
        },
    }
    content_hash = canonical_json_hash(payload)
    existing = db.scalar(
        select(BudgetPolicyVersion).where(
            BudgetPolicyVersion.definition_id == definition.id,
            BudgetPolicyVersion.content_hash == content_hash,
        )
    )
    if existing:
        if existing.lifecycle != "published":
            raise AnalysisConfigurationConflict("existing V2 budget policy is not published")
        return existing
    version = BudgetPolicyVersion(
        definition_id=definition.id,
        version_number=_next_version(db, BudgetPolicyVersion, definition.id),
        lifecycle="published",
        content_hash=content_hash,
        change_note="Phase 6 bounded workflow defaults",
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
        token_limits=payload["token_limits"],
    )
    db.add(version)
    db.flush()
    return version


def _agents(
    db: Session,
    model_policy: ModelPolicyVersion,
    budget_policy: BudgetPolicyVersion,
    tools: dict[str, ToolVersion],
) -> dict[str, AgentVersion]:
    result: dict[str, AgentVersion] = {}
    for spec in AGENT_SPECS:
        definition = _definition(
            db,
            AgentDefinition,
            key=spec.key,
            name=spec.name,
            description=spec.purpose,
        )
        evaluation = evaluate_agent_spec(spec)
        if evaluation["status"] != "passed":
            raise AnalysisConfigurationConflict(
                f"critical agent evaluation failed: {spec.key}: {evaluation['issue_codes']}"
            )
        tool_refs = [
            {"id": str(tools[key].id), "content_hash": tools[key].content_hash}
            for key in spec.tool_keys
        ]
        payload = {
            **spec.persisted_payload(),
            "model_policy_version_id": str(model_policy.id),
            "budget_policy_version_id": str(budget_policy.id),
            "tools": tool_refs,
            "evaluation_suite_hash": EVALUATION_SUITE_HASH,
        }
        content_hash = canonical_json_hash(payload)
        version = db.scalar(
            select(AgentVersion).where(
                AgentVersion.definition_id == definition.id,
                AgentVersion.content_hash == content_hash,
            )
        )
        if version is None:
            persisted = spec.persisted_payload()
            version = AgentVersion(
                definition_id=definition.id,
                version_number=_next_version(db, AgentVersion, definition.id),
                lifecycle="draft",
                content_hash=content_hash,
                change_note="Phase 6 initial operational agent",
                system_prompt=persisted["system_prompt"],
                purpose=spec.purpose,
                prohibited_behaviors=list(spec.prohibited_behaviors),
                input_schema=persisted["input_schema"],
                output_schema=persisted["output_schema"],
                retrieval_policy=persisted["retrieval_policy"],
                generation_config=persisted["generation_config"],
                execution_limits=persisted["execution_limits"],
                evaluation_metadata={
                    "suite_version": EVALUATION_SUITE_VERSION,
                    "suite_hash": EVALUATION_SUITE_HASH,
                    "status": evaluation["status"],
                },
                model_policy_version_id=model_policy.id,
                budget_policy_version_id=budget_policy.id,
            )
            db.add(version)
            db.flush()
            for key in spec.tool_keys:
                db.add(
                    AgentVersionTool(
                        agent_version_id=version.id,
                        tool_version_id=tools[key].id,
                    )
                )
            db.flush()
            db.add(
                AgentEvaluationResult(
                    agent_version_id=version.id,
                    model_policy_version_id=model_policy.id,
                    suite_version=EVALUATION_SUITE_VERSION,
                    suite_hash=EVALUATION_SUITE_HASH,
                    status=evaluation["status"],
                    metrics=evaluation["metrics"],
                    issue_codes=evaluation["issue_codes"],
                )
            )
            version.lifecycle = "published"
            version.published_at = datetime.now(timezone.utc)
        elif version.lifecycle != "published":
            raise AnalysisConfigurationConflict(f"existing agent version is not published: {spec.key}")
        else:
            persisted = spec.persisted_payload()
            expected_tools = {tools[key].id for key in spec.tool_keys}
            actual_tools = set(
                db.scalars(
                    select(AgentVersionTool.tool_version_id).where(
                        AgentVersionTool.agent_version_id == version.id
                    )
                )
            )
            evaluation_row = db.scalar(
                select(AgentEvaluationResult).where(
                    AgentEvaluationResult.agent_version_id == version.id,
                    AgentEvaluationResult.suite_hash == EVALUATION_SUITE_HASH,
                    AgentEvaluationResult.status == "passed",
                )
            )
            if (
                version.system_prompt != persisted["system_prompt"]
                or version.input_schema != persisted["input_schema"]
                or version.output_schema != persisted["output_schema"]
                or version.retrieval_policy != persisted["retrieval_policy"]
                or version.generation_config != persisted["generation_config"]
                or version.execution_limits != persisted["execution_limits"]
                or version.model_policy_version_id != model_policy.id
                or version.budget_policy_version_id != budget_policy.id
                or actual_tools != expected_tools
                or evaluation_row is None
            ):
                raise AnalysisConfigurationConflict(
                    f"existing agent version conflicts with checked-in configuration: {spec.key}"
                )
        result[spec.key] = version
    return result


def _default_workflow(
    agents: dict[str, AgentVersion],
    tools: dict[str, ToolVersion],
    *,
    run_timeout_seconds: int,
) -> WorkflowDag:
    agent_retry = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=1,
        max_delay_seconds=8,
        jitter_ratio=0,
        retryable_categories=("transient", "timeout", "worker_interrupted", "validation"),
    )
    deterministic_retry = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=1,
        max_delay_seconds=8,
        jitter_ratio=0,
        retryable_categories=("transient", "timeout", "worker_interrupted"),
    )

    def agent_task(
        template_key: str,
        task_key: str,
        role: str,
        dependencies: tuple[str, ...],
        *,
        fanout: str = "none",
        conditional: str = "always",
        dependency_mode: str = "all_succeeded",
        minimum_successes: int = 0,
        input_payload: dict[str, Any] | None = None,
    ) -> WorkflowTaskTemplate:
        return WorkflowTaskTemplate(
            template_key=template_key,
            task_key=task_key,
            executor_kind="agent",
            handler=f"analysis.agent.{role}",
            dependencies=dependencies,
            input=input_payload or {},
            retry=agent_retry,
            timeout_seconds=180,
            agent_version_id=agents[role].id,
            fanout=fanout,
            conditional=conditional,
            dependency_mode=dependency_mode,
            minimum_successes=minimum_successes,
        )

    templates = (
        WorkflowTaskTemplate(
            template_key="validate_request",
            task_key="validate_request",
            handler="analysis.validate_request",
            retry=RetryPolicy(max_attempts=1),
            timeout_seconds=30,
        ),
        agent_task("plan_research", "plan_research", "research_coordinator", ("validate_request",)),
        WorkflowTaskTemplate(
            template_key="discover_candidates",
            task_key="discover_candidates",
            handler="analysis.discover_candidates",
            dependencies=("plan_research",),
            retry=deterministic_retry,
            timeout_seconds=120,
            allowed_tool_version_ids=(
                tools["youtube.search"].id,
                tools["youtube.video_details"].id,
                tools["graph.create_nodes"].id,
                tools["graph.create_edges"].id,
            ),
        ),
        agent_task("curate_sources", "curate_sources", "source_curator", ("discover_candidates",)),
        WorkflowTaskTemplate(
            template_key="fetch_transcript",
            task_key="fetch_transcript.source_{index}",
            handler="analysis.fetch_transcript",
            dependencies=("curate_sources",),
            retry=deterministic_retry,
            timeout_seconds=90,
            fanout="source_slots",
            allowed_tool_version_ids=(
                tools["youtube.transcript"].id,
                tools["graph.create_nodes"].id,
                tools["graph.create_edges"].id,
            ),
        ),
        WorkflowTaskTemplate(
            template_key="fetch_comments",
            task_key="fetch_comments.source_{index}",
            handler="analysis.fetch_comments",
            dependencies=("fetch_transcript",),
            retry=deterministic_retry,
            timeout_seconds=60,
            fanout="source_slots",
            conditional="comments_enabled",
            optional=True,
            allowed_tool_version_ids=(
                tools["youtube.comments"].id,
                tools["graph.create_nodes"].id,
                tools["graph.create_edges"].id,
            ),
        ),
        agent_task(
            "analyze_review",
            "analyze_review.source_{index}",
            "review_analyst",
            ("fetch_transcript",),
            fanout="source_slots",
            dependency_mode="all_terminal_min_success",
            minimum_successes=1,
        ),
        agent_task(
            "analyze_audience",
            "analyze_audience.source_{index}",
            "audience_analyst",
            ("fetch_comments",),
            fanout="source_slots",
            conditional="comments_enabled",
            dependency_mode="all_terminal_min_success",
            minimum_successes=1,
        ),
        agent_task(
            "curate_knowledge",
            "curate_knowledge",
            "knowledge_curator",
            ("analyze_review", "analyze_audience"),
            dependency_mode="all_terminal_min_success",
            minimum_successes=1,
        ),
        agent_task("build_consensus", "build_consensus", "consensus_analyst", ("curate_knowledge",)),
        agent_task("audit_report", "audit_report", "quality_auditor", ("build_consensus",)),
        agent_task(
            "correct_consensus",
            "correct_consensus",
            "consensus_analyst",
            ("audit_report",),
            input_payload={"correction_stage": True},
        ),
        agent_task(
            "reaudit_report",
            "reaudit_report",
            "quality_auditor",
            ("correct_consensus",),
            input_payload={"reaudit_stage": True},
        ),
        WorkflowTaskTemplate(
            template_key="publish_report",
            task_key="publish_report",
            handler="analysis.publish_report",
            dependencies=("reaudit_report",),
            retry=RetryPolicy(max_attempts=1),
            timeout_seconds=60,
        ),
    )
    return WorkflowDag(
        schema_version=2,
        run_timeout_seconds=run_timeout_seconds,
        templates=templates,
    )


def _workflow(
    db: Session,
    agents: dict[str, AgentVersion],
    tools: dict[str, ToolVersion],
    config: Settings,
) -> WorkflowVersion:
    definition = _definition(
        db,
        WorkflowDefinition,
        key="v2-default-analysis",
        name="V2 bounded multi-agent analysis",
        description="Seven-role deterministic YouTube evidence workflow.",
    )
    dag = _default_workflow(
        agents,
        tools,
        run_timeout_seconds=config.v2_analysis_run_timeout_seconds,
    )
    payload = dag.model_dump(mode="json")
    content_hash = canonical_json_hash(payload)
    existing = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == definition.id,
            WorkflowVersion.content_hash == content_hash,
        )
    )
    if existing:
        if existing.lifecycle != "published" or existing.dag != payload:
            raise AnalysisConfigurationConflict("existing Phase 6 workflow conflicts with checked-in DAG")
        return existing
    version = WorkflowVersion(
        definition_id=definition.id,
        version_number=_next_version(db, WorkflowVersion, definition.id),
        lifecycle="published",
        content_hash=content_hash,
        change_note="Phase 6 initial bounded multi-agent workflow",
        published_at=datetime.now(timezone.utc),
        dag=payload,
    )
    db.add(version)
    db.flush()
    return version


def seed_analysis_configuration(
    db: Session,
    *,
    config: Settings = settings,
) -> dict[str, Any]:
    seed_tool_registry(db)
    tools = _tool_versions(db)
    model_policy = _model_policy(db, config)
    budget_policy = _budget_policy(db, config)
    agents = _agents(db, model_policy, budget_policy, tools)
    workflow = _workflow(db, agents, tools, config)
    active = db.get(ActiveConfiguration, 1)
    if active is None:
        active = ActiveConfiguration(id=1, environment=config.app_env)
        db.add(active)
    if active.environment != config.app_env:
        raise AnalysisConfigurationConflict("active configuration belongs to another environment")
    active.workflow_version_id = workflow.id
    active.budget_policy_version_id = budget_policy.id
    active.feature_flags = {
        **(active.feature_flags or {}),
        "phase6_bounded_agents": True,
        "comments_default": False,
        "correction_attempts": 1,
    }
    db.flush()
    return {
        "status": "ready",
        "workflow_version_id": str(workflow.id),
        "model_policy_version_id": str(model_policy.id),
        "agent_versions": {key: str(value.id) for key, value in sorted(agents.items())},
        "model_slugs": list(config.v2_agent_model_slugs),
    }
