from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CutoverObservation


def serialize_observation(observation: CutoverObservation) -> dict[str, Any]:
    return {
        "id": str(observation.id),
        "environment": observation.environment,
        "test_evidence": observation.test_evidence,
        "status": observation.status,
        "root_mode": observation.root_mode,
        "thresholds": observation.thresholds,
        "result": observation.latest_result,
        "started_at": observation.started_at.isoformat(),
        "evaluated_at": observation.evaluated_at.isoformat() if observation.evaluated_at else None,
        "ended_at": observation.ended_at.isoformat() if observation.ended_at else None,
        "version": observation.version,
    }


def retirement_observation(db: Session) -> CutoverObservation | None:
    return db.scalar(
        select(CutoverObservation)
        .where(
            CutoverObservation.status == "passed",
            CutoverObservation.test_evidence.is_(False),
        )
        .order_by(CutoverObservation.ended_at.desc(), CutoverObservation.id.desc())
        .limit(1)
    )
