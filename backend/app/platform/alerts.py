from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    AnalysisRun,
    DailyBudgetState,
    OpenRouterAccountState,
    OpenRouterCatalogRefresh,
    ProjectionOutbox,
    TaskRun,
    UsageEvent,
    Workspace,
)
from app.platform.health import scheduler_heartbeat_is_fresh, worker_is_reachable


class OperationalAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "warning"]
    code: str
    title: str
    detail: str
    observed_at: datetime
    threshold: str | int | float | None
    observed_value: str | int | float | None
    recovery_link: str


class OperationalStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: list[OperationalAlert]
    worker_available: bool
    scheduler_fresh: bool


def _age_seconds(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds()))


def collect_operational_alerts(
    db: Session,
    *,
    config: Settings = settings,
    now: datetime | None = None,
    probe_processes: bool = True,
) -> OperationalStatus:
    now = now or datetime.now(timezone.utc)
    alerts: list[OperationalAlert] = []

    def add(
        severity: Literal["critical", "warning"],
        code: str,
        title: str,
        detail: str,
        threshold: str | int | float | None,
        observed_value: str | int | float | None,
        recovery_link: str,
    ) -> None:
        alerts.append(OperationalAlert(
            severity=severity,
            code=code,
            title=title,
            detail=detail,
            observed_at=now,
            threshold=threshold,
            observed_value=observed_value,
            recovery_link=recovery_link,
        ))

    account = db.get(OpenRouterAccountState, 1)
    if account and account.status in {"authentication_blocked", "payment_blocked"}:
        add(
            "critical",
            f"openrouter_{account.status}",
            "OpenRouter requests are blocked",
            "Restore the configured OpenRouter account before model work can continue.",
            "healthy",
            account.status,
            "/admin/models",
        )

    budget = db.get(DailyBudgetState, (now.date(), "public"))
    if budget:
        observed_cost = budget.reserved_cost_microusd + budget.consumed_cost_microusd
        if observed_cost >= budget.max_cost_microusd:
            add(
                "critical",
                "public_daily_budget_exhausted",
                "Public daily budget is exhausted",
                "New public model calls will remain blocked until capacity is restored.",
                budget.max_cost_microusd,
                observed_cost,
                "/admin/settings",
            )

    recent_cutoff = now - timedelta(minutes=15)
    terminal = int(db.scalar(select(func.count()).select_from(AnalysisRun).where(
        AnalysisRun.status.in_(("complete", "partial", "failed", "cancelled")),
        AnalysisRun.completed_at >= recent_cutoff,
    )) or 0)
    failed = int(db.scalar(select(func.count()).select_from(AnalysisRun).where(
        AnalysisRun.status == "failed",
        AnalysisRun.completed_at >= recent_cutoff,
    )) or 0)
    failure_rate = failed / terminal if terminal else 0.0
    if terminal >= config.operations_error_rate_min_runs and failure_rate > 0.20:
        add(
            "critical",
            "run_failure_rate_high",
            "Run failure rate is elevated",
            "More than 20% of recently completed runs failed.",
            0.20,
            round(failure_rate, 4),
            "/admin/runs?status=failed",
        )

    usage_cutoff = now - timedelta(minutes=15)
    oldest_usage = db.scalar(select(func.min(UsageEvent.created_at)).where(
        UsageEvent.usage_status == "pending",
        UsageEvent.created_at < usage_cutoff,
    ))
    if oldest_usage:
        age = _age_seconds(oldest_usage, now)
        add(
            "warning",
            "usage_reconciliation_stale",
            "Usage reconciliation is delayed",
            "At least one usage event has remained pending for more than 15 minutes.",
            900,
            age,
            "/admin/analytics",
        )

    projection_backlog = int(db.scalar(select(func.count()).select_from(ProjectionOutbox).where(
        ProjectionOutbox.status.in_(("pending", "processing", "failed")),
    )) or 0)
    if projection_backlog >= config.projection_backlog_alert_threshold:
        add(
            "warning",
            "projection_backlog_high",
            "Projection backlog is elevated",
            "Neo4j or Qdrant projection work has reached the configured threshold.",
            config.projection_backlog_alert_threshold,
            projection_backlog,
            "/admin/knowledge",
        )

    queued_before = now - timedelta(minutes=2)
    queued_runs = int(db.scalar(select(func.count()).select_from(AnalysisRun).where(
        AnalysisRun.status == "queued", AnalysisRun.created_at < queued_before,
    )) or 0)
    queued_tasks = int(db.scalar(select(func.count()).select_from(TaskRun).where(
        TaskRun.status == "queued", TaskRun.created_at < queued_before,
    )) or 0)
    if queued_runs + queued_tasks:
        add(
            "warning",
            "queued_work_stale",
            "Queued work is delayed",
            "Run or task work has remained queued for more than two minutes.",
            120,
            queued_runs + queued_tasks,
            "/admin/runs?status=queued",
        )

    catalog_refreshed_at = db.scalar(select(func.max(OpenRouterCatalogRefresh.completed_at)).where(
        OpenRouterCatalogRefresh.status == "succeeded",
    ))
    catalog_age = _age_seconds(catalog_refreshed_at, now)
    if catalog_age is None or catalog_age > 3600:
        add(
            "warning",
            "catalog_refresh_stale",
            "Model catalog is stale",
            "The most recent successful OpenRouter catalog refresh is more than one hour old.",
            3600,
            catalog_age if catalog_age is not None else "unavailable",
            "/admin/models",
        )

    markdown_failures = int(db.scalar(select(func.count()).select_from(Workspace).where(
        Workspace.projection_error_code == "markdown_reconciliation_failed",
    )) or 0)
    if markdown_failures:
        add(
            "warning",
            "markdown_reconciliation_failed",
            "Markdown reconciliation failed",
            "One or more workspaces require Markdown repair or quarantine review.",
            0,
            markdown_failures,
            "/admin/knowledge",
        )

    worker_available = True
    scheduler_fresh = True
    if probe_processes:
        try:
            worker_available = worker_is_reachable()
        except Exception:
            worker_available = False
        try:
            scheduler_fresh = scheduler_heartbeat_is_fresh()
        except Exception:
            scheduler_fresh = False
    if not worker_available:
        add(
            "critical",
            "worker_unavailable",
            "Worker is unavailable",
            "Background run and maintenance tasks cannot be dispatched.",
            "available",
            "unavailable",
            "/admin/runs",
        )
    if not scheduler_fresh:
        add(
            "critical",
            "scheduler_heartbeat_stale",
            "Scheduler heartbeat is stale",
            "Scheduled catalog, usage, and projection maintenance may be delayed.",
            90,
            "stale",
            "/admin/settings",
        )

    priority = {"critical": 0, "warning": 1}
    alerts.sort(key=lambda item: (priority[item.severity], item.code))
    return OperationalStatus(
        alerts=alerts,
        worker_available=worker_available,
        scheduler_fresh=scheduler_fresh,
    )
