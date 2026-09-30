from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.contracts import FinalReport
from app.analysis.product_info import ProductInfo, ProductEvidence, merge_product_info
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


def display_report_limitations(payload: dict, report_id: uuid.UUID, internal_source_ids: list[str]) -> dict:
    """Render catalog aliases as public source labels without changing stored records."""
    sources = payload.get("sources", [])
    labels = {source["id"]: f"Source {index} ({source.get('channel') or source.get('title') or 'review video'})"
              for index, source in enumerate(sources, 1)}
    aliases = {f"s{index}": labels.get(public_id(report_id, "source", source_id), "an unidentified source")
               for index, source_id in enumerate(sorted(internal_source_ids), 1)}

    def readable(text: str) -> str:
        return re.sub(r"\bs[1-8]\b", lambda match: aliases.get(match[0], "an unidentified source"), text)

    return {**payload, "limitations": [readable(text) for text in payload.get("limitations", [])],
            "sources": [{**source, "limitations": [readable(text) for text in source.get("limitations", [])]}
                        for source in sources]}


def public_display_payload(db: Session, publication: ReportPublication) -> dict:
    report = db.get(Report, publication.report_id)
    stored = (report.payload or {}) if report else {}
    source_ids = [str(source["source_id"]) for source in stored.get("source_analyses", [])]
    return display_report_limitations(publication.payload, publication.report_id, source_ids)


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


def _decision_guide(validated: FinalReport, sources: list[dict], pros: list[dict], cons: list[dict], source_ids: dict[str, str]) -> dict:
    """Reframe published evidence as purchase checks without another inference call."""
    tested = [source["id"] for source in sources if (source.get("sample_used") or {}).get("units")]
    unknowns = ["Current price in your market", "Local availability", "Current warranty terms"]
    if not tested:
        unknowns.append("Exact configuration reviewers tested")
    if not validated.longest_usage_period:
        unknowns.append("Long-term reliability")
    source_claims = [claim["claim"] for source in sources for claim in source["claims"]]
    fact_labels = (
        [f"{fact.group} {fact.label}" for fact in validated.product_info.facts]
        if validated.product_info else []
    )
    corpus = " ".join([validated.product_canonical_name, *source_claims, *fact_labels]).casefold()
    category_checks = (
        (("comfort", "Long-session comfort"), ("battery", "Battery life under your usage"))
        if any(word in corpus for word in ("headphone", "earbud", "earphone", "audio")) else
        (("battery", "Battery life under your usage"), ("performance", "Performance in your workload"))
        if any(word in corpus for word in ("phone", "laptop", "tablet", "computer")) else
        (("maintenance", "Ongoing maintenance"), ("durability", "Long-term durability"))
    )
    evidenced = " ".join(source_claims).casefold()
    unknowns.extend(label for keyword, label in category_checks if keyword not in evidenced)
    return {
        "buy_if_finding_ids": [item["id"] for item in pros[:3]],
        "caveat_finding_ids": [item["id"] for item in cons[:3]],
        "tested_source_ids": tested,
        "long_term_period": validated.longest_usage_period,
        "long_term_source_id": source_ids.get(str(validated.longest_usage_source_id)),
        "unknowns": unknowns,
    }


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


def product_info_from_tasks(db: Session, run_id: uuid.UUID) -> ProductInfo | None:
    rows = db.execute(
        select(TaskRun.workflow_task_key, TaskAttempt.output_payload)
        .join(TaskAttempt, TaskAttempt.task_run_id == TaskRun.id)
        .where(
            TaskRun.run_id == run_id,
            TaskRun.workflow_task_key.like("extract_product_information.source_%"),
            TaskAttempt.status == "succeeded",
        )
        .order_by(TaskRun.workflow_task_key, TaskAttempt.attempt_number.desc())
    ).all()
    outputs: list[dict] = []
    seen: set[str] = set()
    for key, payload in rows:
        if key not in seen and isinstance(payload, dict):
            outputs.append(payload)
            seen.add(key)
    return merge_product_info(outputs)


def _validate_product_links(info: ProductInfo, allowed_video_ids: set[str]) -> None:
    def check(ref: ProductEvidence) -> None:
        if not VIDEO_ID_PATTERN.fullmatch(ref.video_id) or ref.video_id not in allowed_video_ids:
            raise PublicProjectionError("product evidence source is invalid")
        expected = f"https://www.youtube.com/watch?v={ref.video_id}"
        if ref.timestamp_seconds is not None:
            expected += f"&t={int(ref.timestamp_seconds)}s"
        if ref.source_url != expected:
            raise PublicProjectionError("product evidence URL is invalid")

    for fact in info.facts:
        for ref in fact.evidence:
            check(ref)
    for variant in info.variants:
        for ref in variant.evidence:
            check(ref)


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
    allowed_video_ids = {str(item.get("video_id")) for item in candidates.values()}
    if validated.product_info is not None:
        _validate_product_links(validated.product_info, allowed_video_ids)
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
                **({"sample_used": validated.sample_used_by_source[source_key].model_dump(mode="json")}
                   if source_key in validated.sample_used_by_source else {}),
            }
        )
        if source_key in validated.sample_used_by_source:
            for unit in validated.sample_used_by_source[source_key].units:
                for detail in unit.details:
                    ref = detail.evidence
                    expected = f"https://www.youtube.com/watch?v={video_id}"
                    if ref.timestamp_seconds is not None:
                        expected += f"&t={int(ref.timestamp_seconds)}s"
                    if ref.video_id != video_id or ref.source_url != expected:
                        raise PublicProjectionError("sample evidence source is invalid")
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
    for index, disagreement in enumerate(validated.disagreements):
        disagreement_id = public_id(report.id, "finding", f"disagreement:{index}")
        graph_nodes.append({"id": disagreement_id, "type": "finding", "label": disagreement.topic, "polarity": "disagreement"})
        for source_id in disagreement.side_a_source_ids:
            linked_source_id = source_ids.get(str(source_id))
            if linked_source_id:
                graph_edges.append({"source": linked_source_id, "target": disagreement_id, "type": "SUPPORTS"})
        for source_id in disagreement.side_b_source_ids:
            linked_source_id = source_ids.get(str(source_id))
            if linked_source_id:
                graph_edges.append({"source": linked_source_id, "target": disagreement_id, "type": "CONTRADICTS"})
        disagreements.append(
            {
                "topic": disagreement.topic,
                "side_a": disagreement.side_a,
                "side_a_source_ids": [source_ids[str(sid)] for sid in disagreement.side_a_source_ids if str(sid) in source_ids],
                "side_b": disagreement.side_b,
                "side_b_source_ids": [source_ids[str(sid)] for sid in disagreement.side_b_source_ids if str(sid) in source_ids],
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
        "decision_guide": _decision_guide(validated, source_cards, pros, cons, source_ids),
        **({"product_info": validated.product_info.model_dump(mode="json")} if validated.product_info is not None else {}),
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
