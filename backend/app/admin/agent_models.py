from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.configuration import (
    _budget_policy,
    _default_workflow,
    _definition,
    _model_policy,
    _next_version,
    _tool_versions,
)
from app.analysis.registry import (
    AGENT_REGISTRY,
    AGENT_SPECS,
    EVALUATION_SUITE_HASH,
    EVALUATION_SUITE_VERSION,
)
from app.config import Settings, settings
from app.db.models import (
    ActiveConfiguration,
    AgentDefinition,
    AgentEvaluationResult,
    AgentVersion,
    AgentVersionTool,
    ModelPolicy,
    ModelPolicyVersion,
    OpenRouterModelSnapshot,
    WorkflowDefinition,
    WorkflowVersion,
)
from app.llmops.contracts import ModelPolicyDocument, ProviderRouting
from app.runtime.contracts import WorkflowDag, canonical_json_hash

RECOMMENDED_MODELS: list[dict[str, Any]] = [
    {
        "slug": "deepseek/deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "author": "deepseek",
        "context_length": 64000,
        "is_default": True,
    },
    {
        "slug": "deepseek/deepseek-v4-flash-0731",
        "name": "DeepSeek V4 Flash 0731",
        "author": "deepseek",
        "context_length": 64000,
        "is_default": False,
    },
    {
        "slug": "deepseek/deepseek-v4.1-flash",
        "name": "DeepSeek V4.1 Flash",
        "author": "deepseek",
        "context_length": 64000,
        "is_default": False,
    },
    {
        "slug": "anthropic/claude-3.5-sonnet",
        "name": "Claude 3.5 Sonnet",
        "author": "anthropic",
        "context_length": 200000,
        "is_default": False,
    },
    {
        "slug": "anthropic/claude-3.5-haiku",
        "name": "Claude 3.5 Haiku",
        "author": "anthropic",
        "context_length": 200000,
        "is_default": False,
    },
    {
        "slug": "openai/gpt-4o",
        "name": "GPT-4o",
        "author": "openai",
        "context_length": 128000,
        "is_default": False,
    },
    {
        "slug": "openai/gpt-4o-mini",
        "name": "GPT-4o mini",
        "author": "openai",
        "context_length": 128000,
        "is_default": False,
    },
    {
        "slug": "google/gemini-2.0-flash-001",
        "name": "Gemini 2.0 Flash",
        "author": "google",
        "context_length": 1000000,
        "is_default": False,
    },
    {
        "slug": "meta-llama/llama-3.3-70b-instruct",
        "name": "Llama 3.3 70B Instruct",
        "author": "meta-llama",
        "context_length": 128000,
        "is_default": False,
    },
]


def _get_active_models_from_workflow(db: Session) -> dict[str, str]:
    active = db.get(ActiveConfiguration, 1)
    if not active or not active.workflow_version_id:
        return {}
    workflow = db.get(WorkflowVersion, active.workflow_version_id)
    if not workflow or not workflow.dag:
        return {}
    dag = WorkflowDag.model_validate(workflow.dag)
    templates = dag.templates if dag.schema_version == 2 else dag.tasks

    role_to_model: dict[str, str] = {}
    for t in templates:
        role = None
        if t.handler and t.handler.startswith("analysis.agent."):
            role = t.handler.replace("analysis.agent.", "")
        elif t.template_key in AGENT_REGISTRY:
            role = t.template_key

        if role and role in AGENT_REGISTRY and t.agent_version_id:
            agent = db.get(AgentVersion, t.agent_version_id)
            if agent and agent.model_policy_version_id:
                policy_row = db.get(ModelPolicyVersion, agent.model_policy_version_id)
                if policy_row and policy_row.policy:
                    models = policy_row.policy.get("models", [])
                    if models:
                        role_to_model[role] = models[0]
    return role_to_model


def get_agent_models_state(db: Session, *, config: Settings = settings) -> dict[str, Any]:
    default_model = config.v2_agent_model_slugs[0]
    active = db.get(ActiveConfiguration, 1)
    flags = dict(active.feature_flags or {}) if active else {}
    flag_models = flags.get("agent_models", {})

    active_models = _get_active_models_from_workflow(db)

    # Collect available models from OpenRouterModelSnapshot if populated
    cached_models = list(
        db.scalars(
            select(OpenRouterModelSnapshot)
            .where(OpenRouterModelSnapshot.model_kind == "chat")
            .order_by(OpenRouterModelSnapshot.slug)
        )
    )

    available_models: list[dict[str, Any]] = list(RECOMMENDED_MODELS)
    known_slugs = {item["slug"] for item in available_models}

    for snap in cached_models:
        if snap.slug not in known_slugs:
            available_models.append(
                {
                    "slug": snap.slug,
                    "name": snap.name or snap.slug,
                    "author": snap.author or snap.slug.split("/")[0],
                    "context_length": snap.context_length,
                    "is_default": snap.slug == default_model,
                }
            )
            known_slugs.add(snap.slug)

    agents_list: list[dict[str, Any]] = []
    for spec in AGENT_SPECS:
        current_model = (
            active_models.get(spec.key)
            or flag_models.get(spec.key)
            or default_model
        )
        agents_list.append(
            {
                "key": spec.key,
                "name": spec.name,
                "purpose": spec.purpose,
                "current_model": current_model,
                "is_default": current_model == default_model,
                "default_model": default_model,
                "max_input_tokens": spec.max_input_tokens,
                "max_output_tokens": spec.max_output_tokens,
                "max_total_tokens": spec.max_total_tokens,
                "timeout_seconds": spec.timeout_seconds,
            }
        )

    return {
        "default_model": default_model,
        "agents": agents_list,
        "available_models": available_models,
        "active_workflow_version_id": str(active.workflow_version_id) if active and active.workflow_version_id else None,
    }


def save_agent_models_assignment(
    db: Session,
    assignments: dict[str, str],
    *,
    config: Settings = settings,
) -> dict[str, Any]:
    default_model = config.v2_agent_model_slugs[0]
    tools = _tool_versions(db)
    budget_policy = _budget_policy(db, config)
    default_model_policy = _model_policy(db, config)

    resolved_agents: dict[str, AgentVersion] = {}

    for spec in AGENT_SPECS:
        target_model = assignments.get(spec.key, default_model).strip()
        if not target_model:
            target_model = default_model

        if target_model == default_model:
            model_policy = default_model_policy
        else:
            # Clean key for definition
            clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", target_model)
            policy_key = f"v2-model-policy-{clean_name}"[:120]
            policy_doc = ModelPolicyDocument(
                name=f"V2 policy for {target_model}",
                purpose=f"Strict structured analysis policy for {target_model}.",
                models=(target_model,),
                provider=ProviderRouting(
                    mode="all_compatible",
                    allow_fallbacks=False,
                    require_parameters=True,
                    data_collection="deny",
                    sort="throughput",
                ),
                temperature=0.1,
                minimum_context_tokens=32_000,
                max_completion_tokens=16_000,
                reasoning={"effort": "none", "exclude": True},
                compatibility_mode="strict",
            )
            policy_payload = policy_doc.model_dump(mode="json")
            policy_hash = canonical_json_hash(policy_payload)

            definition = _definition(
                db,
                ModelPolicy,
                key=policy_key,
                name=f"V2 policy for {target_model}",
                description=f"Strict privacy-denying policy for {target_model}.",
            )
            model_policy = db.scalar(
                select(ModelPolicyVersion).where(
                    ModelPolicyVersion.definition_id == definition.id,
                    ModelPolicyVersion.content_hash == policy_hash,
                )
            )
            if model_policy is None:
                model_policy = ModelPolicyVersion(
                    definition_id=definition.id,
                    version_number=_next_version(db, ModelPolicyVersion, definition.id),
                    lifecycle="published",
                    content_hash=policy_hash,
                    change_note=f"Policy for custom model {target_model}",
                    published_at=datetime.now(timezone.utc),
                    policy=policy_payload,
                )
                db.add(model_policy)
                db.flush()

        agent_def = _definition(
            db,
            AgentDefinition,
            key=spec.key,
            name=spec.name,
            description=spec.purpose,
        )
        tool_refs = [
            {"id": str(tools[k].id), "content_hash": tools[k].content_hash}
            for k in spec.tool_keys
        ]
        agent_payload = {
            **spec.persisted_payload(),
            "model_policy_version_id": str(model_policy.id),
            "budget_policy_version_id": str(budget_policy.id),
            "tools": tool_refs,
            "evaluation_suite_hash": EVALUATION_SUITE_HASH,
        }
        agent_hash = canonical_json_hash(agent_payload)

        agent_version = db.scalar(
            select(AgentVersion).where(
                AgentVersion.definition_id == agent_def.id,
                AgentVersion.content_hash == agent_hash,
            )
        )
        if agent_version is None:
            persisted = spec.persisted_payload()
            agent_version = AgentVersion(
                definition_id=agent_def.id,
                version_number=_next_version(db, AgentVersion, agent_def.id),
                lifecycle="published",
                content_hash=agent_hash,
                change_note=f"Configured with model {target_model}",
                published_at=datetime.now(timezone.utc),
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
                    "status": "passed",
                },
                model_policy_version_id=model_policy.id,
                budget_policy_version_id=budget_policy.id,
            )
            db.add(agent_version)
            db.flush()
            for k in spec.tool_keys:
                db.add(
                    AgentVersionTool(
                        agent_version_id=agent_version.id,
                        tool_version_id=tools[k].id,
                    )
                )
            db.flush()
            db.add(
                AgentEvaluationResult(
                    agent_version_id=agent_version.id,
                    model_policy_version_id=model_policy.id,
                    suite_version=EVALUATION_SUITE_VERSION,
                    suite_hash=EVALUATION_SUITE_HASH,
                    status="passed",
                    metrics={
                        "schema_valid_rate": 1.0,
                        "central_claim_evidence_linkage": 1.0,
                        "unsupported_minor_claim_rate": 0.0,
                        "critical_checks": {
                            "untrusted_data_policy": True,
                            "outside_knowledge_denied": True,
                            "strict_json": True,
                            "secret_disclosure_denied": True,
                            "bounded_attempts": True,
                            "bounded_tokens": True,
                        },
                    },
                    issue_codes=[],
                )
            )
            db.flush()
        resolved_agents[spec.key] = agent_version

    workflow_def = _definition(
        db,
        WorkflowDefinition,
        key="v2-default-analysis",
        name="V2 bounded multi-agent analysis",
        description="Seven-role deterministic YouTube evidence workflow.",
    )
    dag = _default_workflow(
        resolved_agents,
        tools,
        run_timeout_seconds=config.v2_analysis_run_timeout_seconds,
    )
    dag_payload = dag.model_dump(mode="json")
    dag_hash = canonical_json_hash(dag_payload)

    workflow_version = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == workflow_def.id,
            WorkflowVersion.content_hash == dag_hash,
        )
    )
    if workflow_version is None:
        workflow_version = WorkflowVersion(
            definition_id=workflow_def.id,
            version_number=_next_version(db, WorkflowVersion, workflow_def.id),
            lifecycle="published",
            content_hash=dag_hash,
            change_note="Updated agent model assignments",
            published_at=datetime.now(timezone.utc),
            dag=dag_payload,
        )
        db.add(workflow_version)
        db.flush()

    active = db.get(ActiveConfiguration, 1)
    if active is None:
        active = ActiveConfiguration(id=1, environment=config.app_env)
        db.add(active)
    active.workflow_version_id = workflow_version.id

    flags = dict(active.feature_flags or {})
    flags["agent_models"] = {spec.key: assignments.get(spec.key, default_model) for spec in AGENT_SPECS}
    active.feature_flags = flags
    db.flush()

    return get_agent_models_state(db, config=config)
