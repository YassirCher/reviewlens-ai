from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.admin.common import decode_cursor, encode_cursor
from app.api.v2.dependencies import get_v2_db, require_admin
from app.db.models import AnalysisRun, OpenRouterAccountState, UsageAggregate, UsageEvent
from app.errors import V2Error
from app.platform.alerts import collect_operational_alerts
from app.admin.cutover import retirement_observation, serialize_observation
from app.services.admin_auth import AuthenticatedAdmin

router = APIRouter(prefix="/admin", tags=["admin-analytics"])


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = end or (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    start = start or end - timedelta(days=7)
    if start.tzinfo is None or end.tzinfo is None or start >= end or end - start > timedelta(days=366):
        raise V2Error(422, "invalid_date_range", "Select a valid UTC range of at most 366 days.")
    return start, end


def _row(row: UsageAggregate) -> dict:
    return {"bucket_start": row.bucket_start.isoformat(), "dimension": row.dimension,
            "dimension_key": row.dimension_key, "request_count": row.request_count,
            "error_count": row.error_count, "total_tokens": row.total_tokens,
            "total_cost_microusd": row.total_cost_microusd,
            "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens,
            "reasoning_tokens": row.reasoning_tokens, "cached_tokens": row.cached_tokens}


def _safe_csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _credits(db: Session) -> dict:
    from app.config import settings
    if not settings.openrouter_management_key:
        return {"status": "not_configured", "remaining_microusd": None}
    account = db.get(OpenRouterAccountState, 1)
    if not account or not account.checked_at:
        return {"status": "unavailable", "remaining_microusd": None}
    age = datetime.now(timezone.utc) - account.checked_at
    status = "stale" if age > timedelta(minutes=settings.openrouter_credit_refresh_minutes * 3) else account.status
    remaining = (account.total_credits_microusd - account.total_usage_microusd
                 if account.total_credits_microusd is not None and account.total_usage_microusd is not None else None)
    return {"status": status, "remaining_microusd": remaining,
            "checked_at": account.checked_at.isoformat()}


@router.get("/overview")
def overview(db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    run_counts = db.execute(select(AnalysisRun.status, func.count(AnalysisRun.id))
                            .where(AnalysisRun.created_at >= cutoff).group_by(AnalysisRun.status)).all()
    usage = db.execute(select(func.coalesce(func.sum(UsageEvent.total_cost_microusd), 0),
                              func.coalesce(func.sum(UsageEvent.total_tokens), 0),
                              func.count(UsageEvent.id))
                       .where(UsageEvent.created_at >= cutoff)).one()
    recent = list(db.scalars(select(UsageAggregate).where(
        UsageAggregate.granularity == "hour", UsageAggregate.dimension == "all",
        UsageAggregate.bucket_start >= cutoff,
    ).order_by(UsageAggregate.bucket_start)))
    latest = db.scalar(select(func.max(UsageAggregate.refreshed_at)))
    cutover = retirement_observation(db)
    cutover_payload = serialize_observation(cutover) if cutover is not None else None
    return {"period": "24h", "run_counts": {status: count for status, count in run_counts},
            "local_usage": {"cost_microusd": int(usage[0]), "tokens": int(usage[1]),
                            "calls": int(usage[2])},
            "hourly": [_row(row) for row in recent],
            "aggregates_refreshed_at": latest.isoformat() if latest else None,
            "aggregates_stale": latest is None or now - latest > timedelta(minutes=10),
            "openrouter_credits": _credits(db),
            "operations": collect_operational_alerts(db).model_dump(mode="json"),
            "cutover": cutover_payload}


@router.get("/analytics")
def analytics(granularity: Literal["hour", "day"] = "day",
              dimension: Literal["all", "model", "provider", "agent", "operation", "initiator"] = "all",
              dimension_key: str | None = Query(default=None, max_length=320),
              start: datetime | None = None, end: datetime | None = None,
              cursor: str | None = None, limit: int = Query(default=25, ge=1, le=100),
              db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    start, end = _range(start, end)
    filters = {"granularity": granularity, "dimension": dimension, "dimension_key": dimension_key,
               "start": start.isoformat(), "end": end.isoformat()}
    statement = select(UsageAggregate).where(
        UsageAggregate.granularity == granularity, UsageAggregate.dimension == dimension,
        UsageAggregate.bucket_start >= start, UsageAggregate.bucket_start < end,
    )
    if dimension_key:
        statement = statement.where(UsageAggregate.dimension_key == dimension_key)
    after = decode_cursor(cursor, filters)
    if after:
        statement = statement.where(or_(
            UsageAggregate.bucket_start < after[0],
            and_(UsageAggregate.bucket_start == after[0], UsageAggregate.id < after[1]),
        ))
    rows = list(db.scalars(statement.order_by(
        UsageAggregate.bucket_start.desc(), UsageAggregate.id.desc(),
    ).limit(limit + 1)))
    page = rows[:limit]
    return {"items": [_row(row) for row in page],
            "next_cursor": encode_cursor(page[-1].bucket_start, page[-1].id, filters) if len(rows) > limit else None,
            "openrouter_credits": _credits(db)}


@router.get("/analytics/export.csv")
def export_csv(granularity: Literal["hour", "day"] = "day",
               dimension: Literal["all", "model", "provider", "agent", "operation", "initiator"] = "all",
               dimension_key: str | None = Query(default=None, max_length=320),
               start: datetime | None = None, end: datetime | None = None,
               db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> StreamingResponse:
    start, end = _range(start, end)
    statement = select(UsageAggregate).where(
        UsageAggregate.granularity == granularity, UsageAggregate.dimension == dimension,
        UsageAggregate.bucket_start >= start, UsageAggregate.bucket_start < end,
    )
    if dimension_key:
        statement = statement.where(UsageAggregate.dimension_key == dimension_key)
    rows = list(db.scalars(statement.order_by(UsageAggregate.bucket_start, UsageAggregate.dimension_key).limit(50_001)))
    if len(rows) > 50_000:
        raise V2Error(422, "export_too_large", "Narrow the export date range.")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(_row(rows[0]).keys()) if rows else
                            ["bucket_start", "dimension", "dimension_key", "request_count", "error_count",
                             "total_tokens", "total_cost_microusd", "prompt_tokens", "completion_tokens",
                             "reasoning_tokens", "cached_tokens"])
    writer.writeheader()
    writer.writerows({key: _safe_csv_cell(value) for key, value in _row(row).items()} for row in rows)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="reviewlens-analytics.csv"',
                                      "Cache-Control": "private, no-store"})
