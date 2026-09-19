from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin.common import StrictModel, decode_list_cursor, encode_list_cursor, not_found, require_admin_mutation
from app.admin.configuration import (
    MODELS, Kind, activate_version, create_draft, publish_draft, update_draft,
    validate_payload, version_dict, version_payload,
)
from app.admin.jobs import create_job
from app.api.v2.dependencies import get_v2_db, require_admin
from app.config import settings
from app.db.models import (
    ActiveConfiguration, AgentVersion, SystemSettingsVersion, WorkflowVersion,
)
from app.errors import V2Error
from app.runtime.contracts import WorkflowDag, canonical_json_hash
from app.services.admin_auth import AuthenticatedAdmin
from app.services.audit_service import add_audit_event
from app.worker import execute_admin_job_task

router = APIRouter(prefix="/admin", tags=["admin-configuration"])


class DraftRequest(StrictModel):
    source_version_id: uuid.UUID
    change_note: str = Field(min_length=1, max_length=2000)


class EditRequest(StrictModel):
    expected_version: int = Field(ge=1)
    payload: dict[str, Any]
    change_note: str = Field(min_length=1, max_length=2000)


class PublishRequest(StrictModel):
    acknowledge_stale: bool = False


class ActivationRequest(StrictModel):
    confirmation: str
    expected_active_version_id: uuid.UUID | None = None


class SettingsDraftRequest(StrictModel):
    source_version_id: uuid.UUID
    catalog_refresh_minutes: int = Field(ge=1, le=1440)
    raw_content_retention: bool = False
    raw_content_ttl_hours: int = Field(default=24, ge=24, le=24)
    change_note: str = Field(min_length=1, max_length=2000)


class SettingsEditRequest(StrictModel):
    expected_version: int = Field(ge=1)
    catalog_refresh_minutes: int = Field(ge=1, le=1440)
    raw_content_retention: bool = False
    raw_content_ttl_hours: int = Field(default=24, ge=24, le=24)
    change_note: str = Field(min_length=1, max_length=2000)


class EvaluationRequest(StrictModel):
    confirmation: str = "run evaluation"


def _kind(value: str) -> Kind:
    if value not in MODELS:
        raise not_found("configuration type")
    return value  # type: ignore[return-value]


def _definition(db: Session, kind: Kind, definition_id: uuid.UUID) -> Any:
    row = db.get(MODELS[kind][0], definition_id)
    if row is None:
        raise not_found("configuration definition")
    return row


def _version(db: Session, kind: Kind, definition_id: uuid.UUID, version_id: uuid.UUID) -> Any:
    row = db.get(MODELS[kind][1], version_id)
    if row is None or row.definition_id != definition_id:
        raise not_found("configuration version")
    return row


def _active_ids(db: Session) -> dict[str, str | None]:
    active = db.get(ActiveConfiguration, 1)
    if not active:
        return {}
    return {
        "workflows": str(active.workflow_version_id) if active.workflow_version_id else None,
        "budget-policies": str(active.budget_policy_version_id) if active.budget_policy_version_id else None,
        "embedding-policies": str(active.embedding_policy_version_id) if active.embedding_policy_version_id else None,
        "system-settings": str(active.system_settings_version_id) if active.system_settings_version_id else None,
    }


def _active_references(db: Session) -> dict[str, set[str]]:
    active = db.get(ActiveConfiguration, 1)
    result: dict[str, set[str]] = {kind: set() for kind in MODELS}
    if active is None or not active.workflow_version_id:
        return result
    workflow = db.get(WorkflowVersion, active.workflow_version_id)
    if workflow is None:
        return result
    dag = WorkflowDag.model_validate(workflow.dag)
    for task in dag.templates if dag.schema_version == 2 else dag.tasks:
        if task.agent_version_id:
            result["agents"].add(str(task.agent_version_id))
    for agent_id in result["agents"]:
        agent = db.get(AgentVersion, uuid.UUID(agent_id))
        if agent and agent.model_policy_version_id:
            result["model-policies"].add(str(agent.model_policy_version_id))
    return result


def _audit(db: Session, request: Request, admin: AuthenticatedAdmin, action: str,
           kind: str, identifier: str, before: str | None, after: str | None,
           metadata: dict | None = None) -> None:
    add_audit_event(db, action=action, actor_type="admin", actor_id=admin.admin.id,
                    target_type=kind, target_id=identifier, request_id=request.state.request_id,
                    before_hash=before, after_hash=after, safe_metadata=metadata or {})


@router.get("/configuration/active")
def active_configuration(db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    active = db.get(ActiveConfiguration, 1)
    return {"versions": _active_ids(db), "kill_switch": active.kill_switch if active else None,
            "public_analysis_enabled": active.public_analysis_enabled if active else None,
            "feature_flags": active.feature_flags if active else {}}


@router.get("/configuration/{kind}")
def list_definitions(kind: str, cursor: str | None = None, limit: int = Query(default=25, ge=1, le=100),
                     db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    checked = _kind(kind)
    definition_model, version_model = MODELS[checked]
    rows = list(db.scalars(select(definition_model).order_by(definition_model.key)))
    after = decode_list_cursor(cursor, {"kind": checked})
    if after:
        rows = [row for row in rows if row.key > after]
    page = rows[:limit]
    active = _active_ids(db)
    references = _active_references(db)
    items = []
    for row in page:
        versions = list(db.scalars(select(version_model).where(version_model.definition_id == row.id)
                                   .order_by(version_model.version_number.desc())))
        items.append({"id": str(row.id), "key": row.key, "name": row.name,
                      "description": row.description, "versions": [
                          {"id": str(v.id), "number": v.version_number, "lifecycle": v.lifecycle,
                           "content_hash": v.content_hash,
                           "active": active.get(checked) == str(v.id) or str(v.id) in references[checked],
                           "published_at": v.published_at.isoformat() if v.published_at else None}
                          for v in versions]})
    return {"items": items, "next_cursor": encode_list_cursor(page[-1].key, {"kind": checked})
            if len(rows) > limit else None, "active_versions": active}


@router.get("/configuration/{kind}/{definition_id}")
def read_definition(kind: str, definition_id: uuid.UUID, db: Session = Depends(get_v2_db),
                    _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    checked = _kind(kind)
    row = _definition(db, checked, definition_id)
    version_model = MODELS[checked][1]
    versions = list(db.scalars(select(version_model).where(version_model.definition_id == row.id)
                               .order_by(version_model.version_number.desc())))
    active = _active_ids(db)
    references = _active_references(db)
    return {"id": str(row.id), "key": row.key, "name": row.name, "description": row.description,
            "active_versions": active,
            "versions": [version_dict(checked, version, db) | {
                "active": active.get(checked) == str(version.id) or str(version.id) in references[checked]
            } for version in versions]}


@router.post("/configuration/{kind}/{definition_id}/drafts", status_code=201)
def new_draft(kind: str, definition_id: uuid.UUID, payload: DraftRequest, request: Request,
              db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    checked = _kind(kind)
    row = create_draft(db, checked, definition_id, payload.source_version_id, payload.change_note)
    _audit(db, request, admin, "configuration.draft_created", checked, str(row.id), None, row.content_hash,
           {"source_version_id": str(payload.source_version_id)})
    db.commit()
    return version_dict(checked, row, db)


@router.put("/configuration/{kind}/{definition_id}/versions/{version_id}")
def edit_draft(kind: str, definition_id: uuid.UUID, version_id: uuid.UUID, payload: EditRequest,
               request: Request, db: Session = Depends(get_v2_db),
               admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    checked = _kind(kind)
    before = _version(db, checked, definition_id, version_id).content_hash
    try:
        row = update_draft(db, checked, definition_id, version_id, payload.payload,
                           payload.expected_version, payload.change_note)
    except (ValueError, TypeError) as exc:
        raise V2Error(422, "invalid_configuration", str(exc)[:300]) from exc
    _audit(db, request, admin, "configuration.draft_edited", checked, str(row.id), before, row.content_hash)
    db.commit()
    return version_dict(checked, row, db)


@router.post("/configuration/{kind}/{definition_id}/versions/{version_id}/validate")
def validate_draft(kind: str, definition_id: uuid.UUID, version_id: uuid.UUID,
                   request: Request, payload: PublishRequest,
                   db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    checked = _kind(kind)
    definition = _definition(db, checked, definition_id)
    row = _version(db, checked, definition_id, version_id)
    result = validate_payload(db, checked, definition, version_payload(checked, row, db),
                              acknowledge_stale=payload.acknowledge_stale)
    _audit(db, request, admin, "configuration.validated", checked, str(row.id), row.content_hash,
           row.content_hash, {"valid": result["valid"]})
    db.commit()
    return result


@router.post("/configuration/{kind}/{definition_id}/versions/{version_id}/publish")
def publish_version(kind: str, definition_id: uuid.UUID, version_id: uuid.UUID, payload: PublishRequest,
                    request: Request, db: Session = Depends(get_v2_db),
                    admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    checked = _kind(kind)
    row = publish_draft(db, checked, definition_id, version_id, acknowledge_stale=payload.acknowledge_stale)
    _audit(db, request, admin, "configuration.published", checked, str(row.id), None, row.content_hash)
    db.commit()
    return version_dict(checked, row, db)


@router.post("/configuration/{kind}/{definition_id}/versions/{version_id}/activate")
def activate(kind: str, definition_id: uuid.UUID, version_id: uuid.UUID, payload: ActivationRequest,
             request: Request, db: Session = Depends(get_v2_db),
             admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    checked = _kind(kind)
    definition = _definition(db, checked, definition_id)
    _version(db, checked, definition_id, version_id)
    if payload.confirmation != definition.key:
        raise V2Error(422, "confirmation_required", "Type the configuration key to activate it.")
    current = _active_ids(db).get(checked)
    if payload.expected_active_version_id and current != str(payload.expected_active_version_id):
        raise V2Error(409, "activation_conflict", "The active version changed.")
    before, after = activate_version(db, checked, version_id)
    _audit(db, request, admin, "configuration.activated", checked, after, before, after,
           {"prior_version_id": before})
    db.commit()
    return {"active_version_id": after, "prior_version_id": before}


@router.post("/configuration/agents/{definition_id}/versions/{version_id}/evaluate", status_code=202)
def evaluate_draft(definition_id: uuid.UUID, version_id: uuid.UUID, payload: EvaluationRequest,
                   request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                   db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    row = _version(db, "agents", definition_id, version_id)
    if row.lifecycle != "draft" or payload.confirmation != "run evaluation":
        raise V2Error(422, "evaluation_ineligible", "Confirm evaluation of an agent draft.")
    if not idempotency_key:
        raise V2Error(422, "idempotency_key_required", "Idempotency-Key is required.")
    try:
        job = create_job(db, actor_id=admin.admin.id, kind="agent_evaluation",
                         target_id=str(version_id), idempotency_key=idempotency_key)
    except ValueError as exc:
        raise V2Error(409, "idempotency_conflict", str(exc)) from exc
    _audit(db, request, admin, "agent.evaluation_requested", "agents", str(version_id),
           row.content_hash, row.content_hash, {"job_id": str(job.id)})
    db.commit()
    try:
        execute_admin_job_task.delay(str(job.id))
    except Exception:
        pass
    return {"job_id": str(job.id), "status": job.status}


@router.get("/settings")
def read_settings(db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    active = db.get(ActiveConfiguration, 1)
    versions = list(db.scalars(select(SystemSettingsVersion).order_by(SystemSettingsVersion.version_number.desc())))
    return {"active_version_id": str(active.system_settings_version_id) if active and active.system_settings_version_id else None,
            "kill_switch": active.kill_switch if active else None,
            "public_analysis_enabled": active.public_analysis_enabled if active else None,
            "versions": [{"id": str(row.id), "number": row.version_number, "version": row.version,
                          "lifecycle": row.lifecycle, "catalog_refresh_minutes": row.catalog_refresh_minutes,
                          "raw_content_retention": row.raw_content_retention,
                          "raw_content_ttl_hours": row.raw_content_ttl_hours,
                          "content_hash": row.content_hash, "change_note": row.change_note}
                         for row in versions],
            "evaluation_budget": _budget_state(db)}


def _budget_state(db: Session) -> dict:
    from app.db.models import EvaluationBudgetState
    row = db.get(EvaluationBudgetState, 1)
    return {"token_limit": row.token_limit, "cost_limit_microusd": row.cost_limit_microusd,
            "reserved_tokens": row.reserved_tokens, "consumed_tokens": row.consumed_tokens,
            "reserved_cost_microusd": row.reserved_cost_microusd,
            "consumed_cost_microusd": row.consumed_cost_microusd} if row else {}


@router.post("/settings/drafts", status_code=201)
def settings_draft(payload: SettingsDraftRequest, request: Request, db: Session = Depends(get_v2_db),
                   admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    source = db.get(SystemSettingsVersion, payload.source_version_id)
    if source is None:
        raise not_found("settings version")
    from app.admin.retention import validate_encryption_key
    if payload.raw_content_retention and not validate_encryption_key(settings.raw_content_encryption_key):
        raise V2Error(422, "encryption_key_required", "Configure a valid raw content encryption key first.")
    number = int(db.scalar(select(func.coalesce(func.max(SystemSettingsVersion.version_number), 0))) or 0) + 1
    document = payload.model_dump(exclude={"source_version_id", "change_note"})
    row = SystemSettingsVersion(version_number=number, lifecycle="draft",
                                content_hash=canonical_json_hash(document), change_note=payload.change_note,
                                created_by_admin_id=admin.admin.id, **document)
    db.add(row)
    db.flush()
    _audit(db, request, admin, "settings.draft_created", "system-settings", str(row.id),
           source.content_hash, row.content_hash)
    db.commit()
    return {"id": str(row.id), "version": row.version, **document}


@router.post("/settings/versions/{version_id}/publish")
def publish_settings(version_id: uuid.UUID, request: Request, db: Session = Depends(get_v2_db),
                     admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    row = db.scalar(select(SystemSettingsVersion).where(SystemSettingsVersion.id == version_id).with_for_update())
    if row is None:
        raise not_found("settings version")
    if row.lifecycle != "draft":
        raise V2Error(409, "published_version_immutable", "Only a draft can be published.")
    from app.admin.retention import validate_encryption_key
    if row.raw_content_retention and not validate_encryption_key(settings.raw_content_encryption_key):
        raise V2Error(422, "encryption_key_required", "Configure a valid raw content encryption key first.")
    row.lifecycle = "published"
    row.published_at = datetime.now(timezone.utc)
    _audit(db, request, admin, "settings.published", "system-settings", str(row.id), None, row.content_hash)
    db.commit()
    return {"id": str(row.id), "lifecycle": row.lifecycle}


@router.put("/settings/versions/{version_id}")
def edit_settings(version_id: uuid.UUID, payload: SettingsEditRequest, request: Request,
                  db: Session = Depends(get_v2_db),
                  admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    row = db.scalar(select(SystemSettingsVersion).where(SystemSettingsVersion.id == version_id).with_for_update())
    if row is None:
        raise not_found("settings version")
    if row.lifecycle != "draft":
        raise V2Error(409, "published_version_immutable", "Published settings cannot be edited.")
    if row.version != payload.expected_version:
        raise V2Error(409, "edit_conflict", "This settings draft changed in another session.")
    from app.admin.retention import validate_encryption_key
    if payload.raw_content_retention and not validate_encryption_key(settings.raw_content_encryption_key):
        raise V2Error(422, "encryption_key_required", "Configure a valid raw content encryption key first.")
    before = row.content_hash
    document = payload.model_dump(exclude={"expected_version", "change_note"})
    for field, value in document.items():
        setattr(row, field, value)
    row.change_note = payload.change_note
    row.content_hash = canonical_json_hash(document)
    db.flush()
    _audit(db, request, admin, "settings.draft_edited", "system-settings", str(row.id),
           before, row.content_hash)
    db.commit()
    return {"id": str(row.id), "version": row.version, "content_hash": row.content_hash, **document}


@router.post("/settings/versions/{version_id}/activate")
def activate_settings(version_id: uuid.UUID, payload: ActivationRequest, request: Request,
                      db: Session = Depends(get_v2_db),
                      admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    if payload.confirmation != "activate settings":
        raise V2Error(422, "confirmation_required", "Confirm settings activation.")
    from app.admin.retention import validate_encryption_key
    target = db.get(SystemSettingsVersion, version_id)
    if target and target.raw_content_retention and not validate_encryption_key(settings.raw_content_encryption_key):
        raise V2Error(422, "encryption_key_required", "Configure a valid raw content encryption key first.")
    before, after = activate_version(db, "system-settings", version_id)
    if payload.expected_active_version_id and before != str(payload.expected_active_version_id):
        raise V2Error(409, "activation_conflict", "The active settings changed.")
    _audit(db, request, admin, "settings.activated", "system-settings", after, before, after)
    db.commit()
    return {"active_version_id": after, "prior_version_id": before}


class BudgetCapRequest(StrictModel):
    token_limit: int = Field(ge=0)
    cost_limit_microusd: int = Field(ge=0)
    confirmation: str


@router.put("/settings/evaluation-budget")
def update_evaluation_budget(payload: BudgetCapRequest, request: Request, db: Session = Depends(get_v2_db),
                             admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    from app.db.models import EvaluationBudgetState
    if payload.confirmation != "change evaluation cap":
        raise V2Error(422, "confirmation_required", "Confirm evaluation cap change.")
    row = db.scalar(select(EvaluationBudgetState).where(EvaluationBudgetState.id == 1).with_for_update())
    if row is None:
        raise V2Error(503, "budget_unavailable", "Evaluation budget is unavailable.")
    before = canonical_json_hash(_budget_state(db))
    if payload.token_limit < row.reserved_tokens + row.consumed_tokens or payload.cost_limit_microusd < row.reserved_cost_microusd + row.consumed_cost_microusd:
        raise V2Error(409, "budget_below_spend", "The cap cannot be lower than committed evaluation usage.")
    row.token_limit = payload.token_limit
    row.cost_limit_microusd = payload.cost_limit_microusd
    after = canonical_json_hash(_budget_state(db))
    _audit(db, request, admin, "evaluation_budget.changed", "evaluation_budget", "1", before, after)
    db.commit()
    return _budget_state(db)


class KillSwitchRequest(StrictModel):
    enabled: bool
    confirmation: str


@router.put("/settings/kill-switch")
def set_kill_switch(payload: KillSwitchRequest, request: Request, db: Session = Depends(get_v2_db),
                    admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    if payload.confirmation != ("enable kill switch" if payload.enabled else "disable kill switch"):
        raise V2Error(422, "confirmation_required", "Confirm the kill switch change.")
    active = db.scalar(select(ActiveConfiguration).where(ActiveConfiguration.id == 1).with_for_update())
    if active is None:
        raise V2Error(503, "configuration_unavailable", "Active configuration is missing.")
    before = str(active.kill_switch).lower()
    active.kill_switch = payload.enabled
    _audit(db, request, admin, "kill_switch.changed", "active_configuration", "1", before,
           str(payload.enabled).lower())
    db.commit()
    return {"enabled": active.kill_switch}
