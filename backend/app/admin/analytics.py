from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, case, cast, delete, func, literal, select, text
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, UsageAggregate, UsageEvent


def reconcile_usage_aggregates(db: Session) -> dict[str, int]:
    """Replace aggregate projections from the append-only local usage ledger."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('usage_aggregates_reconcile'))"))
    dimensions = {
        "all": lambda: literal("all"),
        "model": lambda: func.coalesce(UsageEvent.actual_model, "unknown"),
        "provider": lambda: func.coalesce(UsageEvent.actual_provider, "unknown"),
        "agent": lambda: cast(UsageEvent.agent_version_id, String()),
        "operation": lambda: UsageEvent.operation,
        "initiator": lambda: AnalysisRun.initiator_type,
    }
    db.execute(delete(UsageAggregate))
    count = 0
    for granularity in ("hour", "day"):
        bucket = func.date_trunc(granularity, UsageEvent.created_at)
        for dimension, key_factory in dimensions.items():
            key = key_factory()
            statement = (
                select(
                    bucket.label("bucket"), key.label("key"),
                    func.count(UsageEvent.id),
                    func.sum(case((UsageEvent.status == "failed", 1), else_=0)),
                    func.coalesce(func.sum(UsageEvent.total_tokens), 0),
                    func.coalesce(func.sum(UsageEvent.total_cost_microusd), 0),
                    func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
                    func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
                    func.coalesce(func.sum(UsageEvent.reasoning_tokens), 0),
                    func.coalesce(func.sum(UsageEvent.cached_tokens), 0),
                )
                .join(AnalysisRun, AnalysisRun.id == UsageEvent.run_id)
                .group_by(bucket, key)
            )
            for row in db.execute(statement):
                db.add(UsageAggregate(
                    id=uuid.uuid4(), granularity=granularity, bucket_start=row[0],
                    dimension=dimension, dimension_key=str(row[1]),
                    request_count=int(row[2]), error_count=int(row[3]),
                    total_tokens=int(row[4]), total_cost_microusd=int(row[5]),
                    prompt_tokens=int(row[6]), completion_tokens=int(row[7]),
                    reasoning_tokens=int(row[8]), cached_tokens=int(row[9]),
                    refreshed_at=datetime.now(timezone.utc),
                ))
                count += 1
    db.flush()
    return {"aggregate_rows": count}
