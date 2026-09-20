from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.common import StrictModel, not_found, require_admin_mutation
from app.admin.cutover import (
    current_observation,
    evaluate_observation,
    serialize_observation,
    threshold_snapshot,
    utc_now,
)
from app.api.v2.dependencies import get_v2_db, require_admin
from app.config import settings
from app.db.models import ActiveConfiguration, BudgetPolicyVersion, CutoverObservation
from app.errors import V2Error
from app.services.admin_auth import AuthenticatedAdmin
from app.services.audit_service import add_audit_event

router = APIRouter(prefix="/admin/cutover", tags=["admin-cutover"])


class ObservationCreateRequest(StrictModel):
    confirmation: str
    change_note: str = Field(default="", max_length=500)


class ObservationMutationRequest(StrictModel):
    expected_version: int = Field(ge=1)
    confirmation: str


class MetricResponse(StrictModel):
    code: str
    observed_value: str | int | float | bool | None
    threshold: str | int | float | bool | None
    passed: bool
    unit: str


class ResultResponse(StrictModel):
    observed_at: str
    ready: bool
    blockers: list[str]
    metrics: list[MetricResponse]
    samples: dict[str, int]
    distributions: dict[str, int | None]


class ObservationResponse(StrictModel):
    id: str
    environment: str
    test_evidence: bool
    status: str
    root_mode: str
    thresholds: dict[str, int | float]
    result: ResultResponse | dict[str, Any]
    change_note: str
    started_at: str
    evaluated_at: str | None
    ended_at: str | None
    version: int


class CutoverStatusResponse(StrictModel):
    root_mode: str
    adapter_enabled: bool
    production_defaults: dict[str, int | float]
    current: ObservationResponse | None


def _locked_observation(db: Session, observation_id: uuid.UUID, expected_version: int) -> CutoverObservation:
    observation = db.scalar(
        select(CutoverObservation)
        .where(CutoverObservation.id == observation_id)
        .with_for_update()
    )
    if observation is None:
        raise not_found("cutover observation")
    if observation.version != expected_version:
        raise V2Error(409, "edit_conflict", "The cutover observation changed. Refresh and try again.")
    return observation


def _current_payload(db: Session) -> dict[str, Any] | None:
    observation = current_observation(db)
    if observation is None:
        return None
    result = (
        evaluate_observation(db, observation, persist=False)
        if observation.status == "observing"
        else observation.latest_result
    )
    return serialize_observation(observation, result)


@router.get("", response_model=CutoverStatusResponse)
def read_cutover_status(
    db: Session = Depends(get_v2_db),
    _: AuthenticatedAdmin = Depends(require_admin),
) -> dict[str, Any]:
    return {
        "root_mode": settings.public_root_experience,
        "adapter_enabled": settings.legacy_analysis_adapter_enabled,
        "production_defaults": threshold_snapshot(),
        "current": _current_payload(db),
    }


@router.post("/observations", response_model=ObservationResponse, status_code=201)
def start_cutover_observation(
    payload: ObservationCreateRequest,
    request: Request,
    db: Session = Depends(get_v2_db),
    admin: AuthenticatedAdmin = Depends(require_admin_mutation),
) -> dict[str, Any]:
    if payload.confirmation != "start cutover observation":
        raise V2Error(422, "confirmation_required", "Confirm the cutover observation.")
    if settings.public_root_experience != "v2":
        raise V2Error(409, "root_not_cut_over", "The public root must use V2 before observation starts.")
    active = db.scalar(
        select(CutoverObservation).where(
            CutoverObservation.environment == settings.app_env,
            CutoverObservation.status == "observing",
        )
    )
    if active is not None:
        raise V2Error(409, "observation_active", "A cutover observation is already active.")
    configuration = db.get(ActiveConfiguration, 1)
    budget_policy = (
        db.get(BudgetPolicyVersion, configuration.budget_policy_version_id)
        if configuration and configuration.budget_policy_version_id
        else None
    )
    if budget_policy is None or budget_policy.lifecycle != "published":
        raise V2Error(409, "budget_not_configured", "A published budget policy is required.")
    observation = CutoverObservation(
        id=uuid.uuid4(),
        environment=settings.app_env,
        test_evidence=settings.app_env.lower() == "test",
        status="observing",
        root_mode=settings.public_root_experience,
        thresholds=threshold_snapshot(budget_policy=budget_policy),
        latest_result={},
        change_note=payload.change_note,
        started_by_admin_id=admin.admin.id,
        started_at=utc_now(),
    )
    db.add(observation)
    db.flush()
    add_audit_event(
        db,
        action="cutover.observation_started",
        actor_type="admin",
        actor_id=admin.admin.id,
        target_type="cutover_observation",
        target_id=str(observation.id),
        request_id=request.state.request_id,
        safe_metadata={"environment": settings.app_env, "test_evidence": observation.test_evidence},
    )
    db.commit()
    return serialize_observation(observation, evaluate_observation(db, observation, persist=False))


@router.post("/observations/{observation_id}/evaluate", response_model=ObservationResponse)
def evaluate_cutover(
    observation_id: uuid.UUID,
    payload: ObservationMutationRequest,
    request: Request,
    db: Session = Depends(get_v2_db),
    admin: AuthenticatedAdmin = Depends(require_admin_mutation),
) -> dict[str, Any]:
    if payload.confirmation != "evaluate cutover observation":
        raise V2Error(422, "confirmation_required", "Confirm the cutover evaluation.")
    observation = _locked_observation(db, observation_id, payload.expected_version)
    if observation.status != "observing":
        raise V2Error(409, "observation_terminal", "This cutover observation is already terminal.")
    result = evaluate_observation(db, observation)
    add_audit_event(
        db,
        action="cutover.observation_evaluated",
        actor_type="admin",
        actor_id=admin.admin.id,
        target_type="cutover_observation",
        target_id=str(observation.id),
        request_id=request.state.request_id,
        safe_metadata={"ready": result["ready"], "blockers": result["blockers"]},
    )
    db.commit()
    return serialize_observation(observation)


@router.post("/observations/{observation_id}/record-rollback", response_model=ObservationResponse)
def record_cutover_rollback(
    observation_id: uuid.UUID,
    payload: ObservationMutationRequest,
    request: Request,
    db: Session = Depends(get_v2_db),
    admin: AuthenticatedAdmin = Depends(require_admin_mutation),
) -> dict[str, Any]:
    if payload.confirmation != "record cutover rollback":
        raise V2Error(422, "confirmation_required", "Confirm the cutover rollback record.")
    active = db.get(ActiveConfiguration, 1)
    if active is None or not active.kill_switch:
        raise V2Error(409, "kill_switch_required", "Enable the kill switch before recording rollback.")
    observation = _locked_observation(db, observation_id, payload.expected_version)
    if observation.status == "rolled_back":
        return serialize_observation(observation)
    observation.status = "rolled_back"
    observation.ended_at = utc_now()
    add_audit_event(
        db,
        action="cutover.rollback_recorded",
        actor_type="admin",
        actor_id=admin.admin.id,
        target_type="cutover_observation",
        target_id=str(observation.id),
        request_id=request.state.request_id,
        safe_metadata={"kill_switch": True},
    )
    db.commit()
    return serialize_observation(observation)
