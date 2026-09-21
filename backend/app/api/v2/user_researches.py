from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v2.dependencies import get_v2_db, require_user
from app.config import settings
from app.db.models import AnalysisRun, ReportPublication
from app.public.reports import report_token
from app.services.user_auth import AuthenticatedUser, UserAuthService

router = APIRouter(prefix="/user", tags=["user-researches"])


class UserResearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: uuid.UUID
    product_name: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    source_count_requested: int
    source_count_analyzed: int
    report_url: str | None = None
    public_token: str | None = None
    pdf_url: str | None = None
    overall_score: int | None = None
    verdict: str | None = None
    summary: str | None = None


class UserResearchesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    researches: list[UserResearchItem]
    total: int


@router.get("/researches", response_model=UserResearchesResponse)
def list_user_researches(
    db: Session = Depends(get_v2_db),
    authenticated: AuthenticatedUser = Depends(require_user),
) -> UserResearchesResponse:
    runs = list(
        db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.user_id == authenticated.user.id)
            .order_by(AnalysisRun.created_at.desc())
        )
    )

    items: list[UserResearchItem] = []
    run_ids = [run.id for run in runs]

    publications: dict[uuid.UUID, ReportPublication] = {}
    if run_ids:
        pub_list = list(
            db.scalars(
                select(ReportPublication)
                .where(ReportPublication.run_id.in_(run_ids), ReportPublication.revoked_at.is_(None))
            )
        )
        publications = {pub.run_id: pub for pub in pub_list}

    for run in runs:
        duration: float | None = None
        if run.completed_at and (run.started_at or run.created_at):
            start = run.started_at or run.created_at
            duration = max(0.0, (run.completed_at - start).total_seconds())

        pub = publications.get(run.id)
        report_url: str | None = None
        pdf_url: str | None = None
        token: str | None = None
        overall_score: int | None = None
        verdict: str | None = None
        summary: str | None = None
        analyzed = int(run.requested_options.get("source_count", 5))

        if pub and isinstance(pub.payload, dict):
            token = report_token(pub.report_id)
            report_url = f"/r/{token}"
            pdf_url = f"/api/v2/reports/{token}/pdf"
            overall_score = pub.payload.get("overall_score") or pub.payload.get("score")
            raw_verdict = pub.payload.get("verdict")
            verdict = raw_verdict.replace("_", " ").title() if isinstance(raw_verdict, str) else None
            summary = pub.payload.get("summary")
            analyzed = pub.payload.get("source_count_analyzed", analyzed)

        items.append(
            UserResearchItem(
                run_id=run.id,
                product_name=run.product_input,
                status=run.status,
                created_at=run.created_at,
                started_at=run.started_at,
                completed_at=run.completed_at,
                duration_seconds=duration,
                source_count_requested=int(run.requested_options.get("source_count", 5)),
                source_count_analyzed=analyzed,
                report_url=report_url,
                public_token=token,
                pdf_url=pdf_url,
                overall_score=overall_score,
                verdict=verdict,
                summary=summary,
            )
        )

    return UserResearchesResponse(researches=items, total=len(items))


@router.post("/researches/adopt")
def adopt_researches(
    request: Request,
    db: Session = Depends(get_v2_db),
    authenticated: AuthenticatedUser = Depends(require_user),
) -> dict[str, int]:
    service = UserAuthService(db)
    count = service.adopt_anonymous_runs(
        authenticated.user.id, request.cookies.get(settings.anonymous_session_cookie)
    )
    return {"adopted_count": count}
