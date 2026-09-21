from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.contracts import FinalReport
from app.config import Settings, settings
from app.db.models import (
    AnalysisRun,
    ContextNode,
    Report,
    ReportPublication,
    TaskAttempt,
    TaskRun,
    UsageEvent,
)
from app.runtime.contracts import canonical_json_hash
from app.tools.contracts import VIDEO_ID_PATTERN


class PublicProjectionError(RuntimeError):
    pass


def report_token(report_id: uuid.UUID, config: Settings = settings) -> str:
    digest = hmac.new(
        config.public_token_hash_secret.encode("utf-8"),
        b"reviewlens-report-token-v1:" + report_id.bytes,
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def token_hash(token: str, config: Settings = settings) -> str:
    return hmac.new(
        config.public_token_hash_secret.encode("utf-8"),
        b"reviewlens-report-lookup-v1:" + token.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def public_id(report_id: uuid.UUID, kind: str, value: str) -> str:
    return str(uuid.uuid5(report_id, f"public:{kind}:{value}"))


def _task_output(db: Session, run_id: uuid.UUID, task_key: str) -> dict[str, Any]:
    row = db.scalar(
        select(TaskAttempt)
        .join(TaskRun, TaskRun.id == TaskAttempt.task_run_id)
        .where(
            TaskRun.run_id == run_id,
            TaskRun.workflow_task_key == task_key,
            TaskAttempt.status == "succeeded",
        )
        .order_by(TaskAttempt.attempt_number.desc())
        .limit(1)
    )
    return row.output_payload or {} if row else {}


def _require_nodes(db: Session, report: Report, typed_ids: dict[uuid.UUID, str]) -> None:
    if not typed_ids:
        raise PublicProjectionError("public report has no source lineage")
    rows = list(db.scalars(select(ContextNode).where(ContextNode.id.in_(typed_ids))))
    found = {row.id: row for row in rows}
    for node_id, kind in typed_ids.items():
        node = found.get(node_id)
        if (
            node is None
            or node.workspace_id != report.workspace_id
            or node.status != "active"
            or node.current_version_id is None
            or node.node_type != kind
        ):
            raise PublicProjectionError("public report lineage is unavailable")


def build_public_projection(db: Session, report: Report, run: AnalysisRun) -> tuple[dict, dict]:
    if report.run_id != run.id or report.audit_status not in {"pass", "pass_with_warnings"}:
        raise PublicProjectionError("public report audit or run binding is invalid")
    validated = FinalReport.model_validate(report.payload)
    discovery = _task_output(db, run.id, "discover_candidates")
    candidates = {item["source_node_id"]: item for item in discovery.get("candidates", [])}
    typed_ids: dict[uuid.UUID, str] = {}
    source_ids: dict[str, str] = {}
    evidence_ids: dict[str, str] = {}
    evidence_by_id: dict[str, dict] = {}
    source_cards: list[dict] = []
    graph_nodes: list[dict] = []
    graph_edges: list[dict] = []
    product_id = public_id(report.id, "product", str(run.id))
    graph_nodes.append({"id": product_id, "type": "product", "label": validated.product_display_name})

    for review in validated.source_analyses:
        source_key = str(review.source_id)
        candidate = candidates.get(source_key)
        if candidate is None:
            raise PublicProjectionError("public source metadata is unavailable")
        video_id = str(candidate["video_id"])
        if not VIDEO_ID_PATTERN.fullmatch(video_id):
            raise PublicProjectionError("public source identity is invalid")
        typed_ids[review.source_id] = "source"
        typed_ids[review.source_analysis_node_id] = "source_analysis"
        sid = public_id(report.id, "source", source_key)
        source_ids[source_key] = sid
        claims = []
        for claim in review.claims:
            public_evidence = []
            for evidence in claim.evidence:
                typed_ids[evidence.evidence_node_id] = "evidence"
                eid = public_id(report.id, "evidence", str(evidence.evidence_node_id))
                evidence_ids[str(evidence.evidence_node_id)] = eid
                item = {
                    "id": eid,
                    "text": evidence.evidence_text,
                    "timestamp_start_seconds": evidence.timestamp_start_seconds,
                    "timestamp_end_seconds": evidence.timestamp_end_seconds,
                    "support_type": evidence.support_type,
                    "confidence": evidence.confidence,
                }
                evidence_by_id[eid] = item
                public_evidence.append(item)
            claims.append({"claim": claim.claim, "central": claim.central, "evidence": public_evidence})
        source_cards.append(
            {
                "id": sid,
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": candidate["title"],
                "channel": candidate.get("channel_title") or candidate.get("channel_id") or "Unknown channel",
                "views": candidate.get("view_count"),
                "duration_seconds": candidate.get("duration_seconds"),
                "published_at": candidate.get("published_at"),
                "review_type": review.review_type,
                "ownership_context": review.ownership_context,
                "usage_period": review.usage_period_raw,
                "source_score": review.source_score,
                "evidence_quality_score": review.evidence_quality_score,
                "recommendation_summary": review.recommendation_summary,
                "pros": list(review.pros),
                "cons": list(review.cons),
                "limitations": list(review.limitations),
                "transcript_language": review.transcript_language,
                "translated": review.translated,
                "caption_kind": review.caption_kind,
                "claims": claims,
            }
        )
        graph_nodes.append({"id": sid, "type": "source", "label": candidate["title"], "video_id": video_id})
        graph_edges.append({"source": sid, "target": product_id, "type": "ABOUT"})

    _require_nodes(db, report, typed_ids)

    def visible_finding(item: Any, kind: str, index: int) -> dict:
        fid = public_id(report.id, "finding", f"{kind}:{index}")
        ids = [source_ids[str(source_id)] for source_id in item.source_ids if str(source_id) in source_ids]
        refs = [evidence_ids[str(eid)] for eid in item.evidence_node_ids if str(eid) in evidence_ids]
        if not ids or not refs:
            raise PublicProjectionError("a public finding lacks visible source evidence")
        graph_nodes.append({"id": fid, "type": "finding", "label": item.statement, "polarity": kind})
        for sid in ids:
            graph_edges.append({"source": sid, "target": fid, "type": "SUPPORTS"})
        for eid in refs[:2]:
            evidence = evidence_by_id[eid]
            if not any(node["id"] == eid for node in graph_nodes):
                graph_nodes.append({"id": eid, "type": "evidence", "label": evidence["text"]})
            graph_edges.append({"source": eid, "target": fid, "type": "CONTRADICTS" if evidence["support_type"] == "contradicts" else "SUPPORTS"})
        return {"id": fid, "statement": item.statement, "source_ids": ids, "evidence_ids": refs}

    pros = [visible_finding(item, "pro", idx) for idx, item in enumerate(validated.consensus_pros)]
    cons = [visible_finding(item, "con", idx) for idx, item in enumerate(validated.consensus_cons)]
    disagreements = []
    for index, item in enumerate(validated.disagreements):
        disagreement_id = public_id(report.id, "finding", f"disagreement:{index}")
        graph_nodes.append({"id": disagreement_id, "type": "finding", "label": item.topic, "polarity": "disagreement"})
        for source_id in item.side_a_source_ids:
            sid = source_ids.get(str(source_id))
            if sid:
                graph_edges.append({"source": sid, "target": disagreement_id, "type": "SUPPORTS"})
        for source_id in item.side_b_source_ids:
            sid = source_ids.get(str(source_id))
            if sid:
                graph_edges.append({"source": sid, "target": disagreement_id, "type": "CONTRADICTS"})
        disagreements.append(
            {
                "topic": item.topic,
                "side_a": item.side_a,
                "side_a_source_ids": [source_ids[str(sid)] for sid in item.side_a_source_ids if str(sid) in source_ids],
                "side_b": item.side_b,
                "side_b_source_ids": [source_ids[str(sid)] for sid in item.side_b_source_ids if str(sid) in source_ids],
            }
        )
    payload = {
        "schema_version": 1,
        "report_id": str(report.id),
        "product_name": validated.product_display_name,
        "status": validated.status,
        "source_count_requested": validated.source_count_requested,
        "source_count_analyzed": validated.source_count_analyzed,
        "overall_score": validated.overall_score,
        "verdict": validated.verdict,
        "confidence": validated.confidence,
        "confidence_band": validated.confidence_band,
        "summary": validated.summary,
        "consensus_pros": pros,
        "consensus_cons": cons,
        "disagreements": disagreements,
        "longest_usage_period": validated.longest_usage_period,
        "longest_usage_source_id": source_ids.get(str(validated.longest_usage_source_id)),
        "who_should_buy": list(validated.who_should_buy),
        "who_should_avoid": list(validated.who_should_avoid),
        "limitations": list(validated.limitations),
        "warnings": list(validated.warnings),
        "sources": source_cards,
        "generated_at": validated.generated_at.isoformat(),
    }
    return payload, {"nodes": graph_nodes, "edges": graph_edges}


def publish_report(db: Session, report: Report, run: AnalysisRun, config: Settings = settings) -> ReportPublication:
    existing = db.scalar(select(ReportPublication).where(ReportPublication.report_id == report.id))
    if existing:
        return existing
    payload, graph = build_public_projection(db, report, run)
    publication = ReportPublication(
        id=uuid.uuid4(),
        report_id=report.id,
        run_id=run.id,
        token_hash=token_hash(report_token(report.id, config), config),
        payload=payload,
        graph_payload=graph,
        content_hash=canonical_json_hash({"payload": payload, "graph": graph}),
        published_at=datetime.now(timezone.utc),
    )
    db.add(publication)
    db.flush()
    return publication


def usage_summary(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    total, calls, pending = db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
            func.count(UsageEvent.id).filter(UsageEvent.status.in_(("succeeded", "failed"))),
            func.count(UsageEvent.id).filter(UsageEvent.usage_status == "pending"),
        ).where(UsageEvent.run_id == run_id)
    ).one()
    return {"total_tokens": int(total), "model_call_count": int(calls), "usage_pending": bool(pending)}
