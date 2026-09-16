from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.config import Settings, settings
from app.db.models import ProjectionOutbox, Workspace
from app.db.session import session_scope
from app.knowledge.service import utc_now


def knowledge_health(config: Settings = settings) -> dict:
    with session_scope() as db:
        workspace_count = int(db.scalar(select(func.count()).select_from(Workspace)) or 0)
        degraded = int(
            db.scalar(
                select(func.count()).select_from(Workspace).where(
                    (Workspace.status.in_(("degraded", "quarantined")))
                    | (Workspace.neo4j_status == "degraded")
                    | (Workspace.qdrant_status == "degraded")
                )
            )
            or 0
        )
        pending = int(
            db.scalar(
                select(func.count()).select_from(ProjectionOutbox).where(
                    ProjectionOutbox.status.in_(("pending", "processing", "failed"))
                )
            )
            or 0
        )
        overdue = int(
            db.scalar(
                select(func.count()).select_from(ProjectionOutbox).where(
                    ProjectionOutbox.status.in_(("pending", "failed")),
                    ProjectionOutbox.created_at < utc_now() - timedelta(minutes=15),
                )
            )
            or 0
        )
    if degraded or overdue or pending >= config.projection_backlog_alert_threshold:
        status = "degraded"
    else:
        status = "healthy"
    return {
        "status": status,
        "workspaces": workspace_count,
        "degraded_workspaces": degraded,
        "projection_backlog": pending,
        "projection_overdue": overdue,
    }
