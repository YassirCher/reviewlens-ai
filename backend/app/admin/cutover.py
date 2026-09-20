from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    AnalysisRun,
    CompatibilityRequest,
    CutoverObservation,
    DailyBudgetState,
    ReportPublication,
    RunBudgetState,
    UsageEvent,
)
from app.llmops.accounting import usd_to_microusd


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def threshold_snapshot(
    config: Settings = settings,
    budget_policy: Any | None = None,
) -> dict[str, int | float]:
    snapshot: dict[str, int | float] = {
        "stable_window_hours": config.cutover_stable_window_hours,
        "min_terminal_runs": config.cutover_min_terminal_runs,
        "min_compatibility_requests": config.cutover_min_compatibility_requests,
        "max_failure_rate": config.cutover_max_failure_rate,
        "max_p95_run_latency_seconds": config.cutover_max_p95_run_latency_seconds,
        "min_compatibility_success_rate": config.cutover_min_compatibility_success_rate,
    }
    if budget_policy is not None:
        snapshot.update(
            {
                "public_run_token_cap": int(
                    budget_policy.token_limits.get("run_total_tokens", 0) or 0
                ),
                "public_run_cost_cap_microusd": usd_to_microusd(
                    budget_policy.public_run_cost_cap_usd
                ),
                "public_daily_cost_cap_microusd": usd_to_microusd(
                    budget_policy.public_daily_cost_cap_usd
                ),
            }
        )
    return snapshot


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _metric(code: str, observed: Any, threshold: Any, passed: bool, unit: str) -> dict[str, Any]:
    return {
        "code": code,
        "observed_value": observed,
        "threshold": threshold,
        "passed": passed,
        "unit": unit,
    }


def _central_claim_counts(payload: dict) -> tuple[int, int]:
    total = 0
    linked = 0
    for source in payload.get("sources", []):
        if not isinstance(source, dict):
            continue
        for claim in source.get("claims", []):
            if not isinstance(claim, dict) or claim.get("central") is not True:
                continue
            total += 1
            evidence = claim.get("evidence")
            if isinstance(evidence, list) and evidence and all(
                isinstance(item, dict) and item.get("id") and item.get("text") for item in evidence
            ):
                linked += 1
    return total, linked


def evaluate_metrics(
    db: Session,
    observation: CutoverObservation,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    thresholds = observation.thresholds
    entrypoint = AnalysisRun.requested_options["entrypoint"].astext
    runs = list(
        db.scalars(
            select(AnalysisRun).where(
                AnalysisRun.created_at >= observation.started_at,
                AnalysisRun.created_at <= now,
                AnalysisRun.initiator_type == "public",
                AnalysisRun.status.in_(("complete", "partial", "failed")),
                entrypoint.in_(("v2_public", "v1_sync", "v1_stream")),
            )
        )
    )
    run_ids = [run.id for run in runs]
    successful_runs = [run for run in runs if run.status in {"complete", "partial"}]
    failed_runs = [run for run in runs if run.status == "failed"]
    failure_rate = len(failed_runs) / len(runs) if runs else 0.0
    latencies = [
        max(0, int((run.completed_at - (run.started_at or run.created_at)).total_seconds()))
        for run in runs
        if run.completed_at is not None
    ]

    usage = list(db.scalars(select(UsageEvent).where(UsageEvent.run_id.in_(run_ids)))) if run_ids else []
    tokens_by_run: dict[uuid.UUID, int] = defaultdict(int)
    cost_by_run: dict[uuid.UUID, int] = defaultdict(int)
    unresolved_usage = 0
    for event in usage:
        tokens_by_run[event.run_id] += int(event.total_tokens or 0)
        cost_by_run[event.run_id] += int(event.total_cost_microusd or 0)
        if event.usage_status in {"pending", "unreconcilable"}:
            unresolved_usage += 1

    run_budget_breaches = 0
    if run_ids:
        for budget in db.scalars(select(RunBudgetState).where(RunBudgetState.run_id.in_(run_ids))):
            if budget.max_tokens is not None and budget.consumed_tokens > budget.max_tokens:
                run_budget_breaches += 1
            elif budget.max_cost_microusd is not None and budget.consumed_cost_microusd > budget.max_cost_microusd:
                run_budget_breaches += 1
    daily_budget_breaches = 0
    for budget in db.scalars(
        select(DailyBudgetState).where(
            DailyBudgetState.scope == "public",
            DailyBudgetState.budget_date >= observation.started_at.date(),
            DailyBudgetState.budget_date <= now.date(),
        )
    ):
        if budget.consumed_cost_microusd + budget.reserved_cost_microusd > budget.max_cost_microusd:
            daily_budget_breaches += 1

    publications = list(
        db.scalars(
            select(ReportPublication).where(
                ReportPublication.run_id.in_([run.id for run in successful_runs]),
                ReportPublication.revoked_at.is_(None),
            )
        )
    ) if successful_runs else []
    central_claims = 0
    linked_central_claims = 0
    for publication in publications:
        total, linked = _central_claim_counts(publication.payload)
        central_claims += total
        linked_central_claims += linked
    evidence_rate = linked_central_claims / central_claims if central_claims else 0.0
    evidence_passed = (
        bool(successful_runs)
        and len(publications) == len(successful_runs)
        and central_claims > 0
        and linked_central_claims == central_claims
    )

    compatibility = list(
        db.scalars(
            select(CompatibilityRequest).where(
                CompatibilityRequest.started_at >= observation.started_at,
                CompatibilityRequest.started_at <= now,
                CompatibilityRequest.status != "accepted",
            )
        )
    )
    mapped = sum(
        request.mapped_response and request.status in {"complete", "partial"}
        for request in compatibility
    )
    compatibility_rate = mapped / len(compatibility) if compatibility else 0.0
    age_hours = max(0.0, (now - observation.started_at).total_seconds() / 3600)
    p95_latency = _percentile(latencies, 0.95)
    p50_tokens = _percentile(list(tokens_by_run.values()), 0.50)
    p95_tokens = _percentile(list(tokens_by_run.values()), 0.95)
    p50_cost = _percentile(list(cost_by_run.values()), 0.50)
    p95_cost = _percentile(list(cost_by_run.values()), 0.95)

    metrics = [
        _metric("window_age", round(age_hours, 4), thresholds["stable_window_hours"],
                age_hours >= float(thresholds["stable_window_hours"]), "hours"),
        _metric("terminal_runs", len(runs), thresholds["min_terminal_runs"],
                len(runs) >= int(thresholds["min_terminal_runs"]), "runs"),
        _metric("compatibility_requests", len(compatibility), thresholds["min_compatibility_requests"],
                len(compatibility) >= int(thresholds["min_compatibility_requests"]), "requests"),
        _metric("failure_rate", round(failure_rate, 6), thresholds["max_failure_rate"],
                bool(runs) and failure_rate <= float(thresholds["max_failure_rate"]), "ratio"),
        _metric("p95_run_latency", p95_latency, thresholds["max_p95_run_latency_seconds"],
                p95_latency is not None and p95_latency <= int(thresholds["max_p95_run_latency_seconds"]), "seconds"),
        _metric("central_claim_evidence", round(evidence_rate, 6), 1.0, evidence_passed, "ratio"),
        _metric("compatibility_mapping", round(compatibility_rate, 6),
                thresholds["min_compatibility_success_rate"],
                bool(compatibility) and compatibility_rate >= float(thresholds["min_compatibility_success_rate"]),
                "ratio"),
        _metric("unresolved_usage", unresolved_usage, 0, unresolved_usage == 0 and bool(usage), "events"),
        _metric("budget_breaches", run_budget_breaches + daily_budget_breaches, 0,
                run_budget_breaches + daily_budget_breaches == 0, "breaches"),
    ]
    blockers = [metric["code"] for metric in metrics if not metric["passed"]]
    return {
        "observed_at": now.isoformat(),
        "ready": not blockers,
        "blockers": blockers,
        "metrics": metrics,
        "samples": {
            "terminal_runs": len(runs),
            "successful_runs": len(successful_runs),
            "failed_runs": len(failed_runs),
            "compatibility_requests": len(compatibility),
            "mapped_compatibility_requests": mapped,
            "published_reports": len(publications),
            "central_claims": central_claims,
            "linked_central_claims": linked_central_claims,
            "usage_events": len(usage),
        },
        "distributions": {
            "total_tokens": sum(tokens_by_run.values()),
            "p50_tokens_per_run": p50_tokens,
            "p95_tokens_per_run": p95_tokens,
            "total_cost_microusd": sum(cost_by_run.values()),
            "p50_cost_microusd_per_run": p50_cost,
            "p95_cost_microusd_per_run": p95_cost,
        },
    }


def serialize_observation(observation: CutoverObservation, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": str(observation.id),
        "environment": observation.environment,
        "test_evidence": observation.test_evidence,
        "status": observation.status,
        "root_mode": observation.root_mode,
        "thresholds": observation.thresholds,
        "result": result if result is not None else observation.latest_result,
        "change_note": observation.change_note,
        "started_at": observation.started_at.isoformat(),
        "evaluated_at": observation.evaluated_at.isoformat() if observation.evaluated_at else None,
        "ended_at": observation.ended_at.isoformat() if observation.ended_at else None,
        "version": observation.version,
    }


def current_observation(db: Session, config: Settings = settings) -> CutoverObservation | None:
    return db.scalar(
        select(CutoverObservation)
        .where(CutoverObservation.environment == config.app_env)
        .order_by(CutoverObservation.started_at.desc(), CutoverObservation.id.desc())
        .limit(1)
    )


def evaluate_observation(
    db: Session,
    observation: CutoverObservation,
    *,
    now: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    result = evaluate_metrics(db, observation, now=now)
    if persist and observation.status == "observing":
        evaluated_at = now or utc_now()
        observation.latest_result = result
        observation.evaluated_at = evaluated_at
        if result["ready"]:
            observation.status = "passed"
            observation.ended_at = evaluated_at
        db.flush()
    return result


def evaluate_active_observations(config: Settings = settings) -> dict[str, int]:
    from app.db.session import session_scope

    checked = 0
    passed = 0
    with session_scope() as db:
        observations = list(
            db.scalars(
                select(CutoverObservation).where(
                    CutoverObservation.environment == config.app_env,
                    CutoverObservation.status == "observing",
                ).with_for_update()
            )
        )
        for observation in observations:
            result = evaluate_observation(db, observation)
            checked += 1
            passed += int(result["ready"])
    return {"checked": checked, "passed": passed}
