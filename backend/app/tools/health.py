from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.config import Settings, settings
from app.db.models import ToolInvocation, YouTubeQuotaState
from app.db.session import session_scope
from app.tools.runner import utc_now
from app.tools.youtube import youtube_quota_date


def research_health(config: Settings = settings) -> dict:
    today = youtube_quota_date()
    with session_scope() as db:
        stale = db.scalar(
            select(func.count())
            .select_from(ToolInvocation)
            .where(
                ToolInvocation.status == "running",
                ToolInvocation.started_at
                < utc_now() - timedelta(seconds=config.runtime_task_lease_seconds),
            )
        )
        states = list(
            db.scalars(
                select(YouTubeQuotaState).where(YouTubeQuotaState.quota_date == today)
            )
        )
    buckets = {
        state.bucket: {
            "limit_units": state.limit_units,
            "reserved_units": state.reserved_units,
            "consumed_units": state.consumed_units,
            "remaining_units": state.limit_units - state.reserved_units - state.consumed_units,
        }
        for state in states
    }
    return {
        "status": "degraded" if stale else "ok",
        "youtube_configured": bool(config.youtube_api_key),
        "quota_date_pacific": today.isoformat(),
        "quota_buckets": buckets,
        "stale_invocations": int(stale or 0),
    }
