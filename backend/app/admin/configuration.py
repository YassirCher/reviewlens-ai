from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.registry import AGENT_REGISTRY, UNIVERSAL_POLICY
from app.db.models import (
    ActiveConfiguration, AgentDefinition, AgentEvaluationResult, AgentVersion, AgentVersionTool,
    BudgetPolicy, BudgetPolicyVersion, EmbeddingPolicy, EmbeddingPolicyVersion, ModelPolicy,
    ModelPolicyVersion, SystemSettingsVersion, ToolDefinition, ToolVersion, WorkflowDefinition,
    WorkflowVersion,
)
from app.errors import V2Error
from app.knowledge.contracts import RetrievalPolicy
from app.llmops.contracts import EmbeddingPolicyDocument, ModelPolicyDocument, ProviderRouting
from app.llmops.policies import PolicyCompatibilityError, validate_embedding_policy, validate_model_policy
from app.runtime.contracts import WorkflowDag, canonical_json_hash

Kind = Literal["agents", "workflows", "model-policies", "embedding-policies", "budget-policies"]

MODELS: dict[str, tuple[type, type]] = {
    "agents": (AgentDefinition, AgentVersion),
    "workflows": (WorkflowDefinition, WorkflowVersion),
    "model-policies": (ModelPolicy, ModelPolicyVersion),
    "embedding-policies": (EmbeddingPolicy, EmbeddingPolicyVersion),
    "budget-policies": (BudgetPolicy, BudgetPolicyVersion),
}


class BudgetDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_runs_per_hour: int = Field(ge=0)
    public_runs_per_day: int = Field(ge=0)
    public_concurrent_runs: int = Field(ge=0)
    public_queue_capacity: int = Field(ge=0)
    public_run_cost_cap_usd: Decimal = Field(ge=0)
    public_daily_cost_cap_usd: Decimal = Field(ge=0)
    min_video_count: int = Field(ge=3, le=8)
    default_video_count: int = Field(ge=3, le=8)
    max_video_count: int = Field(ge=3, le=8)
    comments_enabled_default: bool = False
    token_limits: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_order(self) -> "BudgetDocument":
        if not self.min_video_count <= self.default_video_count <= self.max_video_count:
            raise ValueError("video count limits are out of order")
        for value in self.token_limits.values():
            if isinstance(value, int) and value < 0:
                raise ValueError("token limits cannot be negative")
            if isinstance(value, dict) and any(not isinstance(part, int) or part < 0 for part in value.values()):
                raise ValueError("token limits must be non-negative integers")
        return self


def version_payload(kind: Kind, row: Any, db: Session) -> dict:
    if kind == "agents":
        tools = list(db.scalars(select(AgentVersionTool.tool_version_id).where(AgentVersionTool.agent_version_id == row.id)))
        payload = {key: getattr(row, key) for key in (
            "system_prompt", "purpose", "prohibited_behaviors", "input_schema", "output_schema",
            "retrieval_policy", "generation_config", "execution_limits", "model_policy_version_id",
            "budget_policy_version_id",
        )}
        for key in ("model_policy_version_id", "budget_policy_version_id"):
            payload[key] = str(payload[key]) if payload[key] else None
        return payload | {"tool_version_ids": sorted(str(item) for item in tools)}
    if kind == "workflows":
        return {"dag": row.dag}
    if kind == "model-policies":
        return {"policy": row.policy}
    if kind == "embedding-policies":
        return {key: getattr(row, key) for key in (
            "model_slug", "provider_policy", "dimensions", "chunking_version",
            "eligible_node_types", "active_collection",
        )}
    payload = {key: getattr(row, key) for key in BudgetDocument.model_fields}
    for key in ("public_run_cost_cap_usd", "public_daily_cost_cap_usd"):
        payload[key] = str(payload[key])
    return payload


def version_dict(kind: Kind, row: Any, db: Session) -> dict:
    return {
        "id": str(row.id), "definition_id": str(row.definition_id), "version_number": row.version_number,
        "lifecycle": row.lifecycle, "version": row.version, "content_hash": row.content_hash,
        "change_note": row.change_note, "published_at": row.published_at.isoformat() if row.published_at else None,
        "payload": version_payload(kind, row, db),
        "evaluation": row.evaluation_metadata if kind == "agents" else None,
    }


def _definition(db: Session, kind: Kind, definition_id: uuid.UUID) -> Any:
    definition = db.get(MODELS[kind][0], definition_id)
    if definition is None:
        raise V2Error(404, "not_found", "Configuration definition not found.")
    return definition


def _version(db: Session, kind: Kind, definition_id: uuid.UUID, version_id: uuid.UUID, *, lock: bool = False) -> Any:
    model = MODELS[kind][1]
    statement = select(model).where(model.id == version_id, model.definition_id == definition_id)
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise V2Error(404, "not_found", "Configuration version not found.")
    return row


def _validate_agent(db: Session, definition: AgentDefinition, payload: dict) -> dict:
    spec = AGENT_REGISTRY.get(definition.key)
    if spec is None:
        raise ValueError("agent role is not implemented")
    prefix = UNIVERSAL_POLICY + "\n\n"
    prompt = payload.get("system_prompt")
    if not isinstance(prompt, str) or not prompt.startswith(prefix) or not prompt[len(prefix):].strip():
        raise ValueError("agent prompt must keep the universal safety policy and a role prompt")
    if payload.get("input_schema") != spec.input_model.model_json_schema() or payload.get("output_schema") != spec.output_model.model_json_schema():
        raise ValueError("agent input and output schemas must match the implemented role")
    Draft202012Validator.check_schema(payload["input_schema"])
    Draft202012Validator.check_schema(payload["output_schema"])
    RetrievalPolicy.model_validate(payload.get("retrieval_policy"))
    generation = payload.get("generation_config", {})
    limits = payload.get("execution_limits", {})
    if not 0 <= float(generation.get("temperature", -1)) <= 2:
        raise ValueError("temperature is outside allowed range")
    if int(limits.get("max_input_tokens", 0)) < 1 or int(generation.get("max_output_tokens", 0)) < 1:
        raise ValueError("agent token limits must be positive")
    if int(limits.get("max_total_tokens", 0)) < int(limits["max_input_tokens"]) + int(generation["max_output_tokens"]):
        raise ValueError("agent total token cap is too small")
    if not 1 <= int(limits.get("max_attempts", 0)) <= 2 or not 0 <= int(limits.get("correction_attempts", -1)) <= 1:
        raise ValueError("agent retry limits must fit the bounded workflow")
    policy = db.get(ModelPolicyVersion, uuid.UUID(str(payload["model_policy_version_id"])))
    budget = db.get(BudgetPolicyVersion, uuid.UUID(str(payload["budget_policy_version_id"])))
    if not policy or policy.lifecycle != "published" or not budget or budget.lifecycle != "published":
        raise ValueError("agent references must use published model and budget policies")
    tool_ids = [uuid.UUID(str(value)) for value in payload.get("tool_version_ids", [])]
    if len(tool_ids) != len(set(tool_ids)):
        raise ValueError("agent tool versions must be unique")
    for tool_id in tool_ids:
        tool = db.get(ToolVersion, tool_id)
        definition_row = db.get(ToolDefinition, tool.definition_id) if tool else None
        if not tool or tool.lifecycle != "published" or not definition_row:
            raise ValueError("agent tool version is unavailable")
        if spec.key not in tool.capability_metadata.get("allowed_roles", []):
            raise ValueError("agent role is incompatible with a selected tool")
    return {"valid": True, "tool_count": len(tool_ids)}


def _validate_workflow(db: Session, payload: dict) -> dict:
    dag = WorkflowDag.model_validate(payload["dag"])
    templates = dag.templates if dag.schema_version == 2 else dag.tasks
    handlers = {"analysis.validate_request", "analysis.discover_candidates", "analysis.fetch_transcript",
                "analysis.fetch_comments", "analysis.publish_report"}
    keys = set()
    for task in templates:
        keys.add(task.template_key if dag.schema_version == 2 else task.task_key)
        if task.handler not in handlers and task.handler not in {f"analysis.agent.{key}" for key in AGENT_REGISTRY}:
            raise ValueError("workflow contains an unimplemented handler")
        if (task.executor_kind == "agent") != task.handler.startswith("analysis.agent."):
            raise ValueError("workflow handler and executor kind disagree")
        if task.executor_kind == "deterministic" and task.agent_version_id:
            raise ValueError("deterministic task cannot reference an agent version")
        if task.agent_version_id:
            agent = db.get(AgentVersion, task.agent_version_id)
            if not agent or agent.lifecycle != "published":
                raise ValueError("workflow references an unpublished agent")
            definition = db.get(AgentDefinition, agent.definition_id)
            if not definition or task.handler != f"analysis.agent.{definition.key}":
                raise ValueError("workflow agent handler does not match its version")
            allowed = set(db.scalars(select(AgentVersionTool.tool_version_id).where(
                AgentVersionTool.agent_version_id == agent.id)))
            referenced = set(task.allowed_tool_version_ids)
            if task.tool_version_id:
                referenced.add(task.tool_version_id)
            if not referenced.issubset(allowed):
                raise ValueError("workflow agent task exceeds the agent tool allowlist")
        for tool_id in (*task.allowed_tool_version_ids, *((task.tool_version_id,) if task.tool_version_id else ())):
            tool = db.get(ToolVersion, tool_id)
            if not tool or tool.lifecycle != "published":
                raise ValueError("workflow references an unpublished tool")
            allowed_roles = tool.capability_metadata.get("allowed_roles", [])
            expected_role = definition.key if task.agent_version_id else "deterministic"
            if expected_role not in allowed_roles:
                raise ValueError("workflow task uses a tool outside its declared role")
    if "publish_report" not in keys:
        raise ValueError("workflow requires a publication task")
    for count in (3, 8):
        for comments in (False, True):
            concrete = dag.materialize(source_count=count, comments_enabled=comments)
            if "publish_report" not in {task.task_key for task in concrete.tasks}:
                raise ValueError("publication is unreachable")
    return {"valid": True, "task_templates": len(templates)}


def validate_payload(db: Session, kind: Kind, definition: Any, payload: dict, *, acknowledge_stale: bool = False) -> dict:
    try:
        if kind == "agents":
            return _validate_agent(db, definition, payload)
        if kind == "workflows":
            return _validate_workflow(db, payload)
        if kind == "model-policies":
            document = ModelPolicyDocument.model_validate(payload["policy"])
            return validate_model_policy(db, document, acknowledge_stale=acknowledge_stale)
        if kind == "embedding-policies":
            document = EmbeddingPolicyDocument(
                model=payload["model_slug"], provider=ProviderRouting.model_validate(payload["provider_policy"]),
                dimensions=payload.get("dimensions"),
            )
            return validate_embedding_policy(db, document, acknowledge_stale=acknowledge_stale)
        BudgetDocument.model_validate(payload)
        return {"valid": True}
    except (ValidationError, ValueError, KeyError, TypeError, PolicyCompatibilityError) as exc:
        return {"valid": False, "errors": getattr(exc, "errors", None) if isinstance(exc, PolicyCompatibilityError) else [str(exc)[:400]]}


def create_draft(db: Session, kind: Kind, definition_id: uuid.UUID, source_id: uuid.UUID, change_note: str) -> Any:
    _definition(db, kind, definition_id)
    source = _version(db, kind, definition_id, source_id)
    model = MODELS[kind][1]
    number = int(db.scalar(select(func.coalesce(func.max(model.version_number), 0)).where(model.definition_id == definition_id)) or 0) + 1
    payload = version_payload(kind, source, db)
    values = {"definition_id": definition_id, "version_number": number, "lifecycle": "draft",
              "content_hash": canonical_json_hash(payload), "change_note": change_note}
    values.update({key: value for key, value in payload.items() if key != "tool_version_ids"})
    if kind == "agents":
        for key in ("model_policy_version_id", "budget_policy_version_id"):
            values[key] = uuid.UUID(values[key]) if values[key] else None
    row = model(**values)
    if kind == "agents":
        row.evaluation_metadata = {}
    db.add(row)
    db.flush()
    if kind == "agents":
        for tool_id in payload["tool_version_ids"]:
            db.add(AgentVersionTool(agent_version_id=row.id, tool_version_id=uuid.UUID(tool_id)))
        db.flush()
        # Hash the payload as persisted so a rollback draft can be evaluated
        # and published without requiring a no-op edit first.
        row.content_hash = canonical_json_hash(version_payload(kind, row, db))
        db.flush()
    return row


def update_draft(db: Session, kind: Kind, definition_id: uuid.UUID, version_id: uuid.UUID,
                 payload: dict, expected_version: int, change_note: str) -> Any:
    row = _version(db, kind, definition_id, version_id, lock=True)
    if row.lifecycle != "draft":
        raise V2Error(409, "published_version_immutable", "Published configuration cannot be edited.")
    if row.version != expected_version:
        raise V2Error(409, "edit_conflict", "This draft changed in another session.")
    current = version_payload(kind, row, db)
    if set(payload) != set(current):
        raise V2Error(422, "invalid_configuration", "The draft payload fields are incomplete or unknown.")
    if kind == "agents":
        db.query(AgentVersionTool).filter(AgentVersionTool.agent_version_id == row.id).delete()
        for tool_id in payload["tool_version_ids"]:
            db.add(AgentVersionTool(agent_version_id=row.id, tool_version_id=uuid.UUID(str(tool_id))))
        row.evaluation_metadata = {}
    for key, value in payload.items():
        if key != "tool_version_ids":
            if kind == "agents" and key in {"model_policy_version_id", "budget_policy_version_id"}:
                value = uuid.UUID(str(value)) if value else None
            setattr(row, key, value)
    row.change_note = change_note
    row.content_hash = canonical_json_hash(payload)
    db.flush()
    return row


def publish_draft(db: Session, kind: Kind, definition_id: uuid.UUID, version_id: uuid.UUID,
                  *, acknowledge_stale: bool = False) -> Any:
    definition = _definition(db, kind, definition_id)
    row = _version(db, kind, definition_id, version_id, lock=True)
    if row.lifecycle != "draft":
        raise V2Error(409, "published_version_immutable", "Only a draft can be published.")
    payload = version_payload(kind, row, db)
    if row.content_hash != canonical_json_hash(payload):
        raise V2Error(409, "configuration_hash_mismatch", "The draft content hash does not match.")
    validation = validate_payload(db, kind, definition, payload, acknowledge_stale=acknowledge_stale)
    if not validation["valid"]:
        raise V2Error(422, "configuration_invalid", "The draft failed validation.", details=validation["errors"])
    if kind == "agents":
        from app.admin.evaluation import EVALUATION_SUITE_HASH
        metadata = row.evaluation_metadata or {}
        evaluation = db.scalar(select(AgentEvaluationResult).where(
            AgentEvaluationResult.agent_version_id == row.id,
            AgentEvaluationResult.suite_hash == EVALUATION_SUITE_HASH,
            AgentEvaluationResult.status == "passed",
        ).order_by(AgentEvaluationResult.created_at.desc()).limit(1))
        if metadata.get("evaluated_hash") != row.content_hash or metadata.get("suite_hash") != EVALUATION_SUITE_HASH or evaluation is None:
            raise V2Error(409, "evaluation_required", "Run and pass the draft evaluation before publishing.")
    row.lifecycle = "published"
    row.published_at = datetime.now(timezone.utc)
    db.flush()
    return row


def activate_version(db: Session, kind: str, version_id: uuid.UUID) -> tuple[str | None, str]:
    field = {"workflows": "workflow_version_id", "budget-policies": "budget_policy_version_id",
             "embedding-policies": "embedding_policy_version_id", "system-settings": "system_settings_version_id"}.get(kind)
    model = {"workflows": WorkflowVersion, "budget-policies": BudgetPolicyVersion,
             "embedding-policies": EmbeddingPolicyVersion, "system-settings": SystemSettingsVersion}.get(kind)
    if not field or model is None:
        raise V2Error(422, "invalid_activation", "This configuration cannot be activated directly.")
    row = db.get(model, version_id)
    if row is None or row.lifecycle != "published":
        raise V2Error(422, "invalid_activation", "Only a published version can be activated.")
    active = db.scalar(select(ActiveConfiguration).where(ActiveConfiguration.id == 1).with_for_update())
    if active is None:
        raise V2Error(503, "configuration_unavailable", "Active configuration is missing.")
    prior = getattr(active, field)
    setattr(active, field, version_id)
    db.flush()
    return str(prior) if prior else None, str(version_id)
