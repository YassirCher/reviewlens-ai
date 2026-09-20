from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.admin.common import StrictModel
from app.admin.cutover import retirement_observation, serialize_observation
from app.api.v2.dependencies import get_v2_db, require_admin
from app.services.admin_auth import AuthenticatedAdmin

router = APIRouter(prefix="/admin/cutover", tags=["admin-cutover"])


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
    started_at: str
    evaluated_at: str | None
    ended_at: str | None
    version: int


class CutoverHistoryResponse(StrictModel):
    phase: str
    retirement_authorized: bool
    observation: ObservationResponse | None


@router.get("", response_model=CutoverHistoryResponse)
def read_cutover_history(
    db: Session = Depends(get_v2_db),
    _: AuthenticatedAdmin = Depends(require_admin),
) -> dict[str, Any]:
    observation = retirement_observation(db)
    return {
        "phase": "retired",
        "retirement_authorized": observation is not None,
        "observation": serialize_observation(observation) if observation else None,
    }
