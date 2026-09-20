from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any

from sqlalchemy import select

from app.config import Settings, settings
from app.db.models import AnalysisRun, CompatibilityRequest, ReportPublication
from app.db.session import session_scope
from app.models import AnalyzeResponse


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def deprecation_headers(config: Settings = settings) -> dict[str, str]:
    headers = {
        "Deprecation": "true",
        "Link": '</api/v2/analyses>; rel="successor-version"',
        "Cache-Control": "private, no-store",
    }
    if config.legacy_api_sunset_at:
        try:
            sunset = datetime.fromisoformat(config.legacy_api_sunset_at.replace("Z", "+00:00"))
            if sunset.tzinfo is not None:
                headers["Sunset"] = format_datetime(sunset.astimezone(timezone.utc), usegmt=True)
        except ValueError:
            pass
    return headers


def begin_request(*, request_id: uuid.UUID, run_id: uuid.UUID, endpoint: str, transport: str) -> None:
    with session_scope() as db:
        db.add(
            CompatibilityRequest(
                id=uuid.uuid4(),
                request_id=request_id,
                run_id=run_id,
                endpoint=endpoint,
                transport=transport,
                status="accepted",
                started_at=utc_now(),
            )
        )


def record_rejection(
    *, request_id: uuid.UUID, endpoint: str, transport: str, http_status: int, error_code: str
) -> None:
    with session_scope() as db:
        existing = db.scalar(select(CompatibilityRequest).where(CompatibilityRequest.request_id == request_id))
        if existing is not None:
            return
        now = utc_now()
        db.add(
            CompatibilityRequest(
                id=uuid.uuid4(),
                request_id=request_id,
                run_id=None,
                endpoint=endpoint,
                transport=transport,
                status="rejected",
                http_status=http_status,
                error_code=error_code,
                started_at=now,
                completed_at=now,
                duration_ms=0,
            )
        )


def finish_request(
    request_id: uuid.UUID,
    *,
    status: str,
    http_status: int,
    mapped_response: bool,
    error_code: str | None = None,
) -> None:
    with session_scope() as db:
        row = db.scalar(
            select(CompatibilityRequest)
            .where(CompatibilityRequest.request_id == request_id)
            .with_for_update()
        )
        if row is None or row.status != "accepted":
            return
        now = utc_now()
        row.status = status
        row.http_status = http_status
        row.mapped_response = mapped_response
        row.error_code = error_code
        row.completed_at = now
        row.duration_ms = max(0, int((now - row.started_at).total_seconds() * 1000))


def run_status(run_id: uuid.UUID) -> str:
    with session_scope() as db:
        status = db.scalar(select(AnalysisRun.status).where(AnalysisRun.id == run_id))
    if status is None:
        raise LookupError("analysis run not found")
    return status


def wait_for_terminal(run_id: uuid.UUID, timeout_seconds: int) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = run_status(run_id)
        if status in {"complete", "partial", "failed", "cancelled"}:
            return status
        time.sleep(0.25)
    return None


def _legacy_verdict(score: int) -> str:
    if score >= 80:
        return "buy"
    if score >= 65:
        return "buy_with_caveats"
    if score >= 45:
        return "mixed"
    return "do_not_buy"


def map_publication(run_id: uuid.UUID) -> AnalyzeResponse:
    with session_scope() as db:
        run = db.get(AnalysisRun, run_id)
        publication = db.scalar(
            select(ReportPublication).where(
                ReportPublication.run_id == run_id,
                ReportPublication.revoked_at.is_(None),
            )
        )
        if run is None or publication is None or run.status not in {"complete", "partial"}:
            raise LookupError("published compatibility result is unavailable")
        payload = publication.payload

    videos: list[dict[str, Any]] = []
    for source in payload.get("sources", []):
        evidence = []
        for claim in source.get("claims", []):
            for item in claim.get("evidence", []):
                evidence.append(
                    {
                        "claim": str(claim.get("claim", "")),
                        "evidence_text": str(item.get("text", "")),
                        "timestamp_seconds": item.get("timestamp_start_seconds"),
                        "confidence": int(item.get("confidence", 0)),
                    }
                )
        score = int(source.get("source_score", 0))
        quality = int(source.get("evidence_quality_score", 0))
        videos.append(
            {
                "video": {
                    "video_id": source.get("video_id", ""),
                    "title": source.get("title", "Untitled review"),
                    "channel": source.get("channel", "Unknown channel"),
                    "url": source.get("url", ""),
                    "thumbnail_url": None,
                    "view_count": int(source.get("views") or 0),
                    "duration_seconds": source.get("duration_seconds"),
                    "published_at": source.get("published_at"),
                    "relevance_score": 0.0,
                },
                "review_type": source.get("review_type", "unknown"),
                "usage_period_mentioned": bool(source.get("usage_period")),
                "usage_period_raw": source.get("usage_period"),
                "usage_period_days_estimate": None,
                "ownership_context": source.get("ownership_context", "unknown"),
                "product_score": score,
                "reviewer_sentiment_score": score,
                "purchase_recommendation_score": score,
                "confidence_score": quality,
                "purchase_verdict": _legacy_verdict(score),
                "recommendation_summary": source.get("recommendation_summary", ""),
                "pros": list(source.get("pros", [])),
                "cons": list(source.get("cons", [])),
                "major_issues": list(source.get("limitations", [])),
                "recommended_for": [],
                "not_recommended_for": [],
                "evidence": evidence,
                "comments": None,
            }
        )

    overall = {
        "score": int(payload.get("overall_score", 0)),
        "verdict": payload.get("verdict", "unclear"),
        "confidence": int(payload.get("confidence", 0)),
        "summary": payload.get("summary", ""),
        "consensus_pros": [item.get("statement", "") for item in payload.get("consensus_pros", [])],
        "consensus_cons": [item.get("statement", "") for item in payload.get("consensus_cons", [])],
        "disagreements": [
            f"{item.get('topic', 'Disagreement')}: {item.get('side_a', '')} / {item.get('side_b', '')}"
            for item in payload.get("disagreements", [])
        ],
        "longest_usage_period": payload.get("longest_usage_period"),
        "who_should_buy": list(payload.get("who_should_buy", [])),
        "who_should_avoid": list(payload.get("who_should_avoid", [])),
    }
    warnings = list(payload.get("warnings", []))
    warnings.append("This legacy response uses policy-managed V2 routing.")
    return AnalyzeResponse.model_validate(
        {
            "analysis_id": str(run_id),
            "product_name": payload.get("product_name", run.product_input),
            "analyze_comments": bool(run.requested_options.get("analyze_comments", False)),
            "provider_used": "openrouter",
            "model_used": "policy-managed",
            "videos": videos,
            "overall": overall,
            "warnings": warnings,
        }
    )


def legacy_progress(data: dict[str, Any], event_type: str) -> dict[str, Any]:
    return {
        "stage": str(data.get("task_key") or event_type).replace(".", "_"),
        "label": str(data.get("label") or "Research in progress"),
        "percent": int(data.get("percent") or 0),
        "detail": str(data.get("detail") or "ReviewLens is processing the durable V2 run."),
    }
