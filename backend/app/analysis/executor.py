from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app.analysis.contracts import (
    AudienceAnalysis,
    AudienceAnalysisDraft,
    AuditIssue,
    AuditResult,
    CandidateContext,
    FinalReport,
    FinalReportDraft,
    GraphMutationPlan,
    QueryPlan,
    SourceAnalysis,
    SourceAnalysisDraft,
    SourceCuration,
)
from app.analysis.prompting import build_prompt_envelope
from app.analysis.registry import AGENT_REGISTRY, AgentSpec, UNIVERSAL_POLICY
from app.config import Settings, settings
from app.db.models import (
    AgentDefinition,
    AgentVersion,
    AnalysisRun,
    ConfigurationSnapshot,
    ContextNode,
    ContextNodeVersion,
    ModelPolicyVersion,
    OpenRouterModelSnapshot,
    Report,
    TaskAttempt,
    TaskRun,
    UsageEvent,
    Workspace,
)
from app.db.session import session_scope
from app.knowledge.contracts import NodeDraft, NodeType, RelationDraft, RelationType, RetrievalPolicy, RetrievalRequest, TrustLevel
from app.knowledge.retrieval import ContextBudgetExceeded, build_context_packet, estimate_tokens
from app.knowledge.service import create_node, create_relation, create_workspace
from app.llmops.accounting import BudgetRejected
from app.llmops.contracts import ChatInvocation, ChatMessage, InvocationContext, ModelPolicyDocument, OpenRouterError
from app.llmops.gateway import OpenRouterGateway
from app.runtime.contracts import canonical_json_hash
from app.runtime.service import RuntimeTaskError, task_is_cancelled
from app.tools.contracts import (
    GraphCreateEdgeItem,
    GraphCreateEdgesInput,
    GraphCreateNodeItem,
    GraphCreateNodesInput,
    ScoringPreviewInput,
    YouTubeCommentsOutput,
    YouTubeSearchOutput,
    YouTubeTranscriptOutput,
    YouTubeVideoDetailsOutput,
)
from app.tools.errors import ToolExecutionError
from app.tools.runner import invoke_tool
from app.tools.scoring import preview_scoring
from app.tools.youtube import chunk_transcript, rank_candidates
import logging

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_body(label: str, payload: dict[str, Any]) -> str:
    return f"# {label}\n\n```json\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n```\n"


def _transcript_body(transcript: YouTubeTranscriptOutput) -> str:
    lines = ["# Timestamped transcript", "", '<untrusted-data source="youtube-transcript">']
    for segment in transcript.segments:
        end = segment.start_seconds + (segment.duration_seconds or 0)
        lines.append(f"[{segment.start_seconds:.3f}-{end:.3f}] {segment.text}")
    lines.extend(["</untrusted-data>", ""])
    return "\n".join(lines)


def _chunk_body(segments: tuple[Any, ...]) -> str:
    lines = ["# Transcript chunk", "", '<untrusted-data source="youtube-transcript">']
    for segment in segments:
        end = segment.start_seconds + (segment.duration_seconds or 0)
        lines.append(f"[{segment.start_seconds:.3f}-{end:.3f}] {segment.text}")
    lines.extend(["</untrusted-data>", ""])
    return "\n".join(lines)


def _comment_body(comments: YouTubeCommentsOutput) -> str:
    lines = ["# Selected audience comments", "", '<untrusted-data source="youtube-comments">']
    for comment in comments.comments:
        published = comment.published_at.isoformat() if comment.published_at else "unknown"
        lines.append(
            f"- [{comment.comment_id}] likes={comment.like_count} published={published}: {comment.text}"
        )
    lines.extend(["</untrusted-data>", ""])
    return "\n".join(lines)


def _attempt_context(attempt_id: uuid.UUID) -> tuple[TaskAttempt, TaskRun, AnalysisRun, ConfigurationSnapshot]:
    with session_scope() as db:
        attempt = db.get(TaskAttempt, attempt_id)
        task = db.get(TaskRun, attempt.task_run_id) if attempt else None
        run = db.get(AnalysisRun, task.run_id) if task else None
        snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id) if run else None
        if not attempt or not task or not run or not snapshot:
            raise RuntimeTaskError("analysis_attempt_missing", category="configuration")
        db.expunge(attempt)
        db.expunge(task)
        db.expunge(run)
        db.expunge(snapshot)
        return attempt, task, run, snapshot


def _task_output(run_id: uuid.UUID, task_key: str) -> dict[str, Any] | None:
    with session_scope() as db:
        task = db.scalar(
            select(TaskRun).where(
                TaskRun.run_id == run_id,
                TaskRun.workflow_task_key == task_key,
            )
        )
        if task is None:
            return None
        attempt = db.scalar(
            select(TaskAttempt)
            .where(
                TaskAttempt.task_run_id == task.id,
                TaskAttempt.status == "succeeded",
            )
            .order_by(TaskAttempt.attempt_number.desc())
            .limit(1)
        )
        return dict(attempt.output_payload) if attempt and attempt.output_payload else None


def _outputs_with_prefix(run_id: uuid.UUID, prefix: str) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.execute(
            select(TaskRun.workflow_task_key, TaskAttempt.output_payload)
            .join(TaskAttempt, TaskAttempt.task_run_id == TaskRun.id)
            .where(
                TaskRun.run_id == run_id,
                TaskRun.workflow_task_key.like(f"{prefix}%"),
                TaskAttempt.status == "succeeded",
            )
            .order_by(TaskRun.workflow_task_key, TaskAttempt.attempt_number.desc())
        ).all()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, payload in rows:
        if key in seen or not payload:
            continue
        seen.add(key)
        result.append(dict(payload))
    return result


def _node_identity(version_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as db:
        version = db.get(ContextNodeVersion, version_id)
        if version is None:
            raise RuntimeTaskError("context_node_missing", category="storage")
        return version.node_id, version.id


async def _discover_candidates(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    plan: QueryPlan,
    *,
    config: Settings,
) -> dict[str, Any]:
    candidate_pool = min(config.youtube_candidate_cap, max(20, plan.requested_source_count * 4))
    search = YouTubeSearchOutput.model_validate(
        await invoke_tool(
            attempt_id,
            "youtube.search",
            {
                "queries": list(plan.queries),
                "max_results": candidate_pool,
                "region_code": config.youtube_region_code,
                "relevance_language": plan.requested_language,
            },
            call_key="analysis.search",
            config=config,
        )
    )
    if not search.hits:
        raise RuntimeTaskError("youtube_no_candidates", category="not_found")
    details = YouTubeVideoDetailsOutput.model_validate(
        await invoke_tool(
            attempt_id,
            "youtube.video_details",
            {"video_ids": [item.video_id for item in search.hits]},
            call_key="analysis.video_details",
            config=config,
        )
    )
    ranked = rank_candidates(plan.canonical_label, details.videos, config=config)
    score_by_id = {video.video_id: score for video, score in ranked}

    nodes = [
        GraphCreateNodeItem(
            node_type="product",
            title=plan.canonical_label,
            body=_json_body("Product scope", {"canonical_product": plan.canonical_label}),
            trust_level="operational",
            tags=("product",),
            provenance={"phase": 6, "untrusted": False},
            public_visibility="admin",
        )
    ]
    for video in details.videos:
        nodes.append(
            GraphCreateNodeItem(
                node_type="source",
                title=video.title or video.video_id,
                body=_json_body("YouTube source metadata", video.model_dump(mode="json")),
                trust_level="primary_source",
                source_uri=f"https://www.youtube.com/watch?v={video.video_id}",
                source_language=plan.requested_language,
                tags=("youtube", "candidate"),
                provenance={"video_id": video.video_id, "untrusted": True},
                public_visibility="admin",
            )
        )
    created = await invoke_tool(
        attempt_id,
        "graph.create_nodes",
        GraphCreateNodesInput(nodes=tuple(nodes)).model_dump(mode="json"),
        call_key="analysis.raw_sources",
        config=config,
    )
    version_ids = tuple(uuid.UUID(item) for item in created["node_version_ids"])
    product_node_id, product_version_id = _node_identity(version_ids[0])
    source_refs: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    for video, version_id in zip(details.videos, version_ids[1:], strict=True):
        source_refs[video.video_id] = _node_identity(version_id)
    edges = tuple(
        GraphCreateEdgeItem(
            source_version_id=source_version_id,
            target_version_id=product_version_id,
            relation_type="ABOUT",
            idempotency_key=canonical_json_hash(
                ["ABOUT", str(source_version_id), str(product_version_id)]
            ),
        )
        for _, source_version_id in source_refs.values()
    )
    if edges:
        await invoke_tool(
            attempt_id,
            "graph.create_edges",
            GraphCreateEdgesInput(edges=edges).model_dump(mode="json"),
            call_key="analysis.source_about_product",
            config=config,
        )

    candidates: list[dict[str, Any]] = []
    for video in details.videos:
        score = score_by_id[video.video_id]
        node_id, version_id = source_refs[video.video_id]
        candidate = CandidateContext(
            video_id=video.video_id,
            title=video.title or video.video_id,
            channel_id=video.channel_id or "unknown-channel",
            channel_title=video.channel_title,
            duration_seconds=video.duration_seconds or 0,
            view_count=video.view_count,
            caption_available=video.caption_available,
            deterministic_score=score.total,
            deterministic_exclusion=score.excluded_reason,
        ).model_dump(mode="json")
        candidate.update(
            {
                "source_node_id": str(node_id),
                "source_version_id": str(version_id),
                "published_at": video.published_at.isoformat() if video.published_at else None,
            }
        )
        candidates.append(candidate)
    return {
        "product_node_id": str(product_node_id),
        "canonical_product": plan.canonical_label,
        "requested_source_count": plan.requested_source_count,
        "requested_language": plan.requested_language,
        "candidates": candidates,
    }


async def _fetch_transcript(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    source_index: int,
    *,
    config: Settings,
) -> dict[str, Any]:
    discovery = _task_output(run.id, "discover_candidates") or {}
    curation = SourceCuration.model_validate(_task_output(run.id, "curate_sources") or {})
    candidates = {item["video_id"]: item for item in discovery.get("candidates", [])}
    source_count = int(run.requested_options.get("source_count", config.default_video_count))
    ordered = [item for item in curation.ordered_video_ids if item in candidates]
    queue = ordered[source_index - 1 :: source_count]
    for video_id in queue:
        candidate = candidates[video_id]
        try:
            payload = await invoke_tool(
                attempt_id,
                "youtube.transcript",
                {
                    "video_id": video_id,
                    "requested_language": discovery.get("requested_language", "en"),
                },
                call_key=f"analysis.transcript.{source_index}.{video_id}",
                config=config,
            )
        except ToolExecutionError as exc:
            if exc.code.startswith("transcript_"):
                continue
            raise RuntimeTaskError(exc.code, category=exc.category, retryable=exc.retryable) from exc
        transcript = YouTubeTranscriptOutput.model_validate(payload)
        chunks = chunk_transcript(transcript, config=config)
        duration = max(
            segment.start_seconds + (segment.duration_seconds or 0)
            for segment in transcript.segments
        )
        transcript_nodes = [
            GraphCreateNodeItem(
                node_type="transcript",
                title=f"Transcript — {candidate['title']}",
                body=_transcript_body(transcript),
                trust_level="primary_source",
                source_uri=f"https://www.youtube.com/watch?v={video_id}",
                source_language=transcript.source_language,
                tags=("youtube", "transcript"),
                provenance={
                    "video_id": video_id,
                    "source_language": transcript.source_language,
                    "delivered_language": transcript.delivered_language,
                    "caption_kind": transcript.caption_kind,
                    "translated": transcript.translated,
                    "duration_seconds": duration,
                    "untrusted": True,
                },
                public_visibility="admin",
            )
        ]
        for index, chunk in enumerate(chunks):
            transcript_nodes.append(
                GraphCreateNodeItem(
                    node_type="transcript_chunk",
                    title=f"Transcript chunk {index + 1} — {candidate['title']}",
                    body=_chunk_body(chunk),
                    trust_level="primary_source",
                    source_uri=f"https://www.youtube.com/watch?v={video_id}",
                    source_language=transcript.source_language,
                    tags=("youtube", "transcript", "chunk"),
                    provenance={
                        "video_id": video_id,
                        "segment_start": chunk[0].index,
                        "segment_end": chunk[-1].index,
                        "duration_seconds": duration,
                        "untrusted": True,
                    },
                    public_visibility="admin",
                )
            )
        created = await invoke_tool(
            attempt_id,
            "graph.create_nodes",
            GraphCreateNodesInput(nodes=tuple(transcript_nodes)).model_dump(mode="json"),
            call_key=f"analysis.transcript_nodes.{source_index}.{video_id}",
            config=config,
        )
        transcript_version_ids = tuple(uuid.UUID(item) for item in created["node_version_ids"])
        transcript_node_id, transcript_version_id = _node_identity(transcript_version_ids[0])
        source_version_id = uuid.UUID(candidate["source_version_id"])
        lineage = [
            GraphCreateEdgeItem(
                source_version_id=transcript_version_id,
                target_version_id=source_version_id,
                relation_type="DERIVED_FROM",
                idempotency_key=canonical_json_hash(
                    ["DERIVED_FROM", str(transcript_version_id), str(source_version_id)]
                ),
            )
        ]
        for chunk_version_id in transcript_version_ids[1:]:
            lineage.extend(
                (
                    GraphCreateEdgeItem(
                        source_version_id=transcript_version_id,
                        target_version_id=chunk_version_id,
                        relation_type="CONTAINS",
                        idempotency_key=canonical_json_hash(
                            ["CONTAINS", str(transcript_version_id), str(chunk_version_id)]
                        ),
                    ),
                    GraphCreateEdgeItem(
                        source_version_id=chunk_version_id,
                        target_version_id=transcript_version_id,
                        relation_type="DERIVED_FROM",
                        idempotency_key=canonical_json_hash(
                            ["DERIVED_FROM", str(chunk_version_id), str(transcript_version_id)]
                        ),
                    ),
                )
            )
        await invoke_tool(
            attempt_id,
            "graph.create_edges",
            GraphCreateEdgesInput(edges=tuple(lineage)).model_dump(mode="json"),
            call_key=f"analysis.transcript_edges.{source_index}.{video_id}",
            config=config,
        )
        return {
            "available": True,
            "source_index": source_index,
            "video_id": video_id,
            "source_id": candidate["source_node_id"],
            "source_version_id": candidate["source_version_id"],
            "source_title": candidate["title"],
            "channel_id": candidate["channel_id"],
            "transcript_node_id": str(transcript_node_id),
            "transcript_version_id": str(transcript_version_id),
            "transcript_chunk_ids": [str(_node_identity(item)[0]) for item in transcript_version_ids[1:]],
            "transcript_language": transcript.delivered_language,
            "translated": transcript.translated,
            "caption_kind": transcript.caption_kind,
        }
    return {"available": False, "source_index": source_index, "reason": "transcript_unavailable"}


async def _fetch_comments(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    source_index: int,
    *,
    config: Settings,
) -> dict[str, Any]:
    source = _task_output(run.id, f"fetch_transcript.source_{source_index}") or {}
    if not source.get("available"):
        return {"available": False, "source_index": source_index, "reason": "source_unavailable"}
    comments = YouTubeCommentsOutput.model_validate(
        await invoke_tool(
            attempt_id,
            "youtube.comments",
            {
                "video_id": source["video_id"],
                "fetch_limit": config.comments_fetch_limit,
                "retain_limit": config.comments_retain_limit,
            },
            call_key=f"analysis.comments.{source_index}",
            config=config,
        )
    )
    if not comments.comments:
        return {
            "available": False,
            "source_index": source_index,
            "source_id": source["source_id"],
            "comments_sampled": comments.comments_sampled,
            "reason": "comments_unavailable",
        }
    created = await invoke_tool(
        attempt_id,
        "graph.create_nodes",
        GraphCreateNodesInput(
            nodes=(
                GraphCreateNodeItem(
                    node_type="comment_set",
                    title=f"Audience comments — {source['source_title']}",
                    body=_comment_body(comments),
                    trust_level="secondary_source",
                    source_uri=f"https://www.youtube.com/watch?v={source['video_id']}",
                    source_language=source["transcript_language"],
                    tags=("youtube", "comments", "audience"),
                    provenance={
                        "video_id": source["video_id"],
                        "comments_sampled": comments.comments_sampled,
                        "comments_retained": len(comments.comments),
                        "untrusted": True,
                    },
                    public_visibility="admin",
                ),
            )
        ).model_dump(mode="json"),
        call_key=f"analysis.comment_node.{source_index}",
        config=config,
    )
    comment_version_id = uuid.UUID(created["node_version_ids"][0])
    comment_node_id, _ = _node_identity(comment_version_id)
    await invoke_tool(
        attempt_id,
        "graph.create_edges",
        GraphCreateEdgesInput(
            edges=(
                GraphCreateEdgeItem(
                    source_version_id=comment_version_id,
                    target_version_id=uuid.UUID(source["source_version_id"]),
                    relation_type="DERIVED_FROM",
                    idempotency_key=canonical_json_hash(
                        ["DERIVED_FROM", str(comment_version_id), str(source["source_version_id"])]
                    ),
                ),
            )
        ).model_dump(mode="json"),
        call_key=f"analysis.comment_edge.{source_index}",
        config=config,
    )
    return {
        "available": True,
        "source_index": source_index,
        "source_id": source["source_id"],
        "comment_set_node_id": str(comment_node_id),
        "comments_sampled": comments.comments_sampled,
        "comments_retained": len(comments.comments),
    }


def _agent_task_input(spec: AgentSpec, task: TaskRun, run: AnalysisRun) -> tuple[dict[str, Any], tuple[uuid.UUID, ...]]:
    source_index = int(task.input_payload.get("source_index", 0) or 0)
    if spec.key == "research_coordinator":
        return {
            "product_name": run.product_input,
            "requested_source_count": int(run.requested_options.get("source_count", 5)),
            "requested_language": str(run.requested_options.get("language", "en")),
            "analyze_comments": bool(run.requested_options.get("analyze_comments", False)),
        }, ()
    if spec.key == "source_curator":
        discovery = _task_output(run.id, "discover_candidates") or {}
        clean = [
            {
                **{key: value for key, value in item.items() if key in CandidateContext.model_fields},
                # Retain both ends of a long title so trailing model identifiers stay
                # visible. Full metadata remains in the authoritative source node.
                "title": (
                    item["title"][:125] + " … " + item["title"][-30:]
                    if len(item["title"]) > 160 else item["title"]
                ),
                "channel_title": item.get("channel_title", "")[:80],
            }
            for item in discovery.get("candidates", [])
        ]
        seeds = (
            (uuid.UUID(discovery["product_node_id"]),)
            if discovery.get("product_node_id")
            else ()
        )
        return {"canonical_product": discovery["canonical_product"], "candidates": clean}, seeds
    if spec.key == "review_analyst":
        source = _task_output(run.id, f"fetch_transcript.source_{source_index}") or {}
        if not source.get("available"):
            return {"_skip": "source_unavailable", "source_index": source_index}, ()
        chunk_ids = source.get("transcript_chunk_ids") or []
        if not chunk_ids:
            raise RuntimeTaskError("transcript_chunks_missing", category="quality")
        return {
            "source_id": source["source_id"],
            "transcript_node_id": source["transcript_node_id"],
            "source_title": source["source_title"],
            "channel_id": source["channel_id"],
            "transcript_language": source["transcript_language"],
            "translated": source["translated"],
            "caption_kind": source["caption_kind"],
        }, (uuid.UUID(chunk_ids[0]),)
    if spec.key == "audience_analyst":
        comments = _task_output(run.id, f"fetch_comments.source_{source_index}") or {}
        if not comments.get("available"):
            return {"_skip": "comments_unavailable", "source_index": source_index}, ()
        return {
            "source_id": comments["source_id"],
            "comment_set_node_id": comments["comment_set_node_id"],
            "comments_sampled": comments["comments_sampled"],
            "comments_retained": comments["comments_retained"],
        }, (uuid.UUID(comments["comment_set_node_id"]),)
    reviews = [item["analysis"] for item in _outputs_with_prefix(run.id, "analyze_review.source_") if item.get("analysis")]
    audiences = [item["analysis"] for item in _outputs_with_prefix(run.id, "analyze_audience.source_") if item.get("analysis")]
    if spec.key == "knowledge_curator":
        if not reviews:
            raise RuntimeTaskError("no_valid_source_analyses", category="quality")
        return {"source_analyses": reviews, "audience_analyses": audiences}, ()
    if spec.key == "consensus_analyst":
        discovery = _task_output(run.id, "discover_candidates") or {}
        if task.input_payload.get("correction_stage"):
            audit = _task_output(run.id, "audit_report") or {}
            original = _task_output(run.id, "build_consensus") or {}
            if (audit.get("audit") or {}).get("verdict") in {"pass", "pass_with_warnings"}:
                return {"_shortcut": original}, ()
            issues = (audit.get("audit") or {}).get("issues", [])
        else:
            issues = []
        return {
            "product_display_name": run.product_input,
            "product_canonical_name": discovery.get("canonical_product", run.canonical_product),
            "requested_source_count": int(run.requested_options.get("source_count", 5)),
            "source_analyses": reviews,
            "audience_analyses": audiences,
            "correction_issues": issues,
        }, ()
    if spec.key == "quality_auditor":
        if task.input_payload.get("reaudit_stage"):
            first_audit = _task_output(run.id, "audit_report") or {}
            if (first_audit.get("audit") or {}).get("verdict") in {"pass", "pass_with_warnings"}:
                return {"_shortcut": first_audit}, ()
            consensus = _task_output(run.id, "correct_consensus") or {}
        else:
            consensus = _task_output(run.id, "build_consensus") or {}
        return {
            "report_draft": consensus.get("draft", {}),
            "source_analyses": reviews,
        }, ()
    raise RuntimeTaskError("unknown_agent_role", category="configuration")


def _all_source_candidates_excluded(payload: dict[str, Any]) -> bool:
    """Return true when deterministic filtering left nothing for curation."""
    candidates = payload.get("candidates")
    return bool(candidates) and isinstance(candidates, list) and all(
        isinstance(candidate, dict) and bool(candidate.get("deterministic_exclusion"))
        for candidate in candidates
    )


def _model_estimated_cost(db, slug: str, prompt_tokens: int, completion_tokens: int) -> int:
    model = db.scalar(
        select(OpenRouterModelSnapshot)
        .where(OpenRouterModelSnapshot.slug == slug, OpenRouterModelSnapshot.model_kind == "chat")
        .order_by(OpenRouterModelSnapshot.fetched_at.desc())
        .limit(1)
    )
    if model is None:
        raise RuntimeTaskError("agent_model_catalog_missing", category="configuration")
    try:
        prompt_price = Decimal(str(model.pricing.get("prompt", "0")))
        completion_price = Decimal(str(model.pricing.get("completion", "0")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeTaskError("agent_model_pricing_invalid", category="configuration") from exc
    if any(not value.is_finite() or value < 0 for value in (prompt_price, completion_price)):
        raise RuntimeTaskError("agent_model_pricing_invalid", category="configuration")
    estimate = (prompt_price * prompt_tokens + completion_price * completion_tokens) * Decimal(1_000_000)
    return int(estimate.quantize(Decimal("1"), rounding=ROUND_CEILING))


def _bounded_agent_policy(policy: ModelPolicyDocument, spec: AgentSpec) -> ModelPolicyDocument:
    """Apply the published agent's generation bounds to its model policy."""
    return policy.model_copy(
        update={
            "temperature": spec.temperature,
            "max_completion_tokens": min(
                spec.max_output_tokens + spec.max_reasoning_tokens,
                policy.max_completion_tokens,
            ),
            # Reasoning tokens consume the completion ceiling. Without this cap,
            # a reasoning model can exhaust the request before emitting JSON.
            # OpenRouter enforces that only one of "effort" and "max_tokens" can be specified.
            # Using "effort": "none" eliminates reasoning token overhead, preventing chat_content_truncated.
            "reasoning": {"effort": "none", "exclude": True},
        }
    )


def _safe_validation(exc: ValidationError) -> dict[str, Any]:
    issues = []
    for error in exc.errors(include_input=False, include_context=False)[:50]:
        issues.append(
            {
                "path": ".".join(str(item) for item in error["loc"]),
                "type": str(error["type"])[:120],
            }
        )
    return {"status": "failed", "issues": issues}


def _context_packet(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    spec: AgentSpec,
    seeds: tuple[uuid.UUID, ...],
    query: str,
    *,
    config: Settings,
    budget_override: int | None = None,
) -> tuple[str, uuid.UUID | None, int]:
    if not seeds:
        return "<no-authorized-context />", None, 0
    if budget_override is not None and budget_override < 200:
        return "<no-authorized-context />", None, 0
    with session_scope() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        if workspace is None:
            raise RuntimeTaskError("analysis_workspace_missing", category="storage")
        policy_payload = spec.retrieval_policy.model_dump(mode="json")
        if budget_override is not None:
            policy_payload["input_token_budget"] = max(100, budget_override)
        policy = RetrievalPolicy.model_validate(policy_payload)
        try:
            packet = build_context_packet(
                db,
                RetrievalRequest(
                    workspace_id=workspace.id,
                    task_attempt_id=attempt_id,
                    query=query,
                    seed_node_ids=seeds,
                    policy=policy,
                ),
                config=config,
            )
            return packet.rendered, packet.manifest_id, packet.estimated_tokens
        except ContextBudgetExceeded:
            if not spec.retrieval_policy.required_seed_node_types:
                logger.warning(
                    "Context budget exceeded for optional seeds in %s (budget=%d); omitting context packet",
                    spec.key,
                    policy_payload.get("input_token_budget", 0),
                )
                return "<no-authorized-context />", None, 0
            raise


async def _call_agent(
    attempt_id: uuid.UUID,
    task: TaskRun,
    run: AnalysisRun,
    snapshot: ConfigurationSnapshot,
    spec: AgentSpec,
    payload: dict[str, Any],
    seeds: tuple[uuid.UUID, ...],
    attempt_input: dict[str, Any],
    *,
    config: Settings,
) -> BaseModel:
    # Published agent versions, not the newest checked-in registry, govern runs
    # already in flight when a compatible successor is seeded.
    with session_scope() as db:
        version = db.get(AgentVersion, task.agent_version_id)
        if version is None or version.content_hash != next(
            (item["content_hash"] for item in snapshot.snapshot.get("agents", []) if item["id"] == str(version.id)),
            None,
        ):
            raise RuntimeTaskError("agent_snapshot_mismatch", category="configuration")
        prefix = UNIVERSAL_POLICY + "\n\n"
        if not version.system_prompt.startswith(prefix):
            raise RuntimeTaskError("agent_snapshot_prompt_invalid", category="configuration")
        spec = replace(
            spec,
            role_prompt=version.system_prompt[len(prefix):],
            prohibited_behaviors=tuple(version.prohibited_behaviors),
            retrieval_policy=RetrievalPolicy.model_validate(version.retrieval_policy),
            temperature=float(version.generation_config.get("temperature", 0.1)),
            max_input_tokens=int(version.execution_limits["max_input_tokens"]),
            max_output_tokens=int(version.generation_config["max_output_tokens"]),
            max_reasoning_tokens=int(version.generation_config["max_reasoning_tokens"]),
            max_total_tokens=int(version.execution_limits["max_total_tokens"]),
            timeout_seconds=int(version.execution_limits["timeout_seconds"]),
        )
    try:
        validated_input = spec.input_model.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeTaskError(
            "agent_input_invalid",
            category="validation",
            validator_results=_safe_validation(exc),
        ) from exc
    task_input_data = validated_input.model_dump(mode="json")
    task_input_tokens = estimate_tokens(json.dumps(task_input_data))
    # Reserve tokens for system prompt (~600 tokens), task wrapper (~300 tokens), and safety margin (400 tokens)
    available_context = max(0, spec.max_input_tokens - task_input_tokens - 1300)
    budget_override = min(spec.retrieval_policy.input_token_budget, available_context) if available_context > 0 else 0

    rendered, manifest_id, context_tokens = _context_packet(
        attempt_id,
        run,
        spec,
        seeds,
        run.canonical_product,
        config=config,
        budget_override=budget_override,
    )
    correction = attempt_input.get("correction")
    task_instruction = spec.purpose
    if spec.key == "quality_auditor":
        task_instruction = (
            "Verify the report_draft against the supplied source_analyses. "
            "Return verdict 'pass' with empty issues if the consensus claims are supported by the reviews. "
            "Return 'pass_with_warnings' if there are minor limitations or caveats noted in the reviews. "
            "Return 'fail' only if there are critical fabricated claims contradicted by the sources. "
            "Note: Only consensus_pros and consensus_cons require evidence_node_ids (which are already linked to the sources). "
            "Narrative fields (summary, who_should_buy, who_should_avoid, limitations, product names) synthesize findings and do not take evidence_node_ids. "
            "Do not return missing_central_evidence on narrative fields or on items that already have evidence_node_ids."
        )
    envelope = build_prompt_envelope(
        spec,
        task_instruction=task_instruction,
        task_input=task_input_data,
        context_manifest_id=str(manifest_id) if manifest_id else None,
        rendered_context=rendered,
        correction=correction if isinstance(correction, dict) else None,
    )
    prompt_tokens = estimate_tokens(envelope.system) + estimate_tokens(envelope.user)
    if prompt_tokens > spec.max_input_tokens:
        logger.error(
            "Agent input token limit exceeded for %s (%s): prompt_tokens=%d > max_input_tokens=%d (task_input=%d, context=%d)",
            task.workflow_task_key,
            spec.key,
            prompt_tokens,
            spec.max_input_tokens,
            task_input_tokens,
            context_tokens,
        )
        raise RuntimeTaskError("agent_input_token_limit_exceeded", category="limit")

    with session_scope() as db:
        locked_attempt = db.get(TaskAttempt, attempt_id)
        agent = db.get(AgentVersion, task.agent_version_id)
        definition = db.get(AgentDefinition, agent.definition_id) if agent else None
        policy_row = db.get(ModelPolicyVersion, agent.model_policy_version_id) if agent else None
        if (
            not locked_attempt
            or not agent
            or not definition
            or definition.key != spec.key
            or agent.lifecycle != "published"
            or agent.content_hash != next(
                (item["content_hash"] for item in snapshot.snapshot.get("agents", []) if item["id"] == str(agent.id)),
                None,
            )
            or not policy_row
            or policy_row.lifecycle != "published"
        ):
            raise RuntimeTaskError("agent_snapshot_mismatch", category="configuration")
        policy = _bounded_agent_policy(ModelPolicyDocument.model_validate(policy_row.policy), spec)
        estimated_cost = _model_estimated_cost(
            db, policy.models[0], prompt_tokens, policy.max_completion_tokens
        )
        locked_attempt.context_manifest_id = manifest_id
        locked_attempt.prompt_hash = envelope.prompt_hash
        agent_id = agent.id
        policy_id = policy_row.id

    if task_is_cancelled(run.id, task.id):
        raise RuntimeTaskError("analysis_cancelled", category="cancelled")
    invocation = ChatInvocation(
        context=InvocationContext(
            run_id=run.id,
            task_run_id=task.id,
            task_attempt_id=attempt_id,
            agent_version_id=agent_id,
            workflow_version_id=snapshot.workflow_version_id,
            model_policy_version_id=policy_id,
            embedding_policy_version_id=snapshot.embedding_policy_version_id,
            call_key=task.workflow_task_key,
            deadline_at=task.deadline_at,
            initiator_type=run.initiator_type,
        ),
        policy=policy,
        messages=(
            ChatMessage(role="system", content=envelope.system),
            ChatMessage(role="user", content=envelope.user),
        ),
        response_schema=spec.output_model.model_json_schema(),
        schema_name=spec.output_model.__name__,
        estimated_prompt_tokens=max(prompt_tokens, context_tokens),
        estimated_cost_microusd=estimated_cost,
        max_network_attempts=1,
    )
    try:
        result = await OpenRouterGateway(config=config).chat(invocation)
    except BudgetRejected as exc:
        raise RuntimeTaskError(str(exc), category="budget") from exc
    except OpenRouterError as exc:
        category = "transient" if exc.retryable else "provider"
        if exc.provider_code in {"schema_validation_failed", "invalid_chat_shape"} or (
            exc.provider_code is not None and exc.provider_code.startswith("chat_")
        ):
            category = "validation"
        logger.error(
            "OpenRouter call failed for %s (%s): provider_code=%s, category=%s, retryable=%s",
            task.workflow_task_key,
            spec.key,
            exc.provider_code,
            category,
            exc.retryable,
        )
        raise RuntimeTaskError(
            exc.provider_code or exc.category.value,
            category=category,
            retryable=exc.retryable or category == "validation",
            validator_results={
                "status": "failed",
                "provider_category": exc.category.value,
                "provider_code": exc.provider_code,
            },
        ) from exc
    try:
        return spec.output_model.model_validate(result.content)
    except ValidationError as exc:
        raise RuntimeTaskError(
            "agent_output_invalid",
            category="validation",
            retryable=True,
            invalid_output_hash=canonical_json_hash(result.content),
            validator_results=_safe_validation(exc),
        ) from exc


async def _postprocess_review(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    draft: SourceAnalysisDraft,
    *,
    config: Settings,
) -> dict[str, Any]:
    source = next(
        (
            item
            for item in _outputs_with_prefix(run.id, "fetch_transcript.source_")
            if item.get("available") and item.get("source_id") == str(draft.source_id)
        ),
        None,
    )
    if source is None:
        raise RuntimeTaskError("review_source_mismatch", category="validation", retryable=True)
    if not any(claim.central for claim in draft.claims):
        raise RuntimeTaskError(
            "central_claim_missing",
            category="validation",
            retryable=True,
            invalid_output_hash=canonical_json_hash(draft.model_dump(mode="json")),
            validator_results={"status": "failed", "issues": [{"path": "claims", "type": "central_claim_missing"}]},
        )
    with session_scope() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        transcript = db.get(ContextNode, uuid.UUID(source["transcript_node_id"]))
        source_node = db.get(ContextNode, draft.source_id)
        if not workspace or not transcript or not source_node or not transcript.current_version_id or not source_node.current_version_id:
            raise RuntimeTaskError("review_graph_context_missing", category="storage")
        transcript_version_id = transcript.current_version_id
        source_version_id = source_node.current_version_id
        source_version = db.get(ContextNodeVersion, source_version_id)
        source_uri = source_version.source_uri if source_version else None
        source_duration: float | None = None
        if source_version and isinstance(source_version.provenance.get("duration_seconds"), (int, float)):
            source_duration = float(source_version.provenance["duration_seconds"])
        claims: list[dict[str, Any]] = []
        for claim_index, claim in enumerate(draft.claims):
            evidence_rows: list[dict[str, Any]] = []
            for evidence_index, evidence in enumerate(claim.evidence):
                start_sec = evidence.timestamp_start_seconds
                end_sec = evidence.timestamp_end_seconds
                if source_duration is not None and source_duration > 0:
                    if end_sec is not None and end_sec > source_duration:
                        end_sec = source_duration
                    if start_sec is not None and start_sec > source_duration:
                        start_sec = max(0.0, source_duration - 1.0)
                    if start_sec is not None and end_sec is not None and end_sec < start_sec:
                        end_sec = start_sec
                version = create_node(
                    db,
                    workspace.id,
                    NodeDraft(
                        node_type=NodeType.EVIDENCE,
                        title=f"Evidence {claim_index + 1}.{evidence_index + 1}",
                        body=evidence.evidence_text,
                        trust_level=TrustLevel.DERIVED,
                        source_uri=source_uri,
                        source_language=source["transcript_language"],
                        confidence=evidence.confidence,
                        tags=("evidence", evidence.support_type),
                        provenance={
                            "timestamp_start_seconds": start_sec,
                            "timestamp_end_seconds": end_sec,
                            "central_claim": claim.central,
                        },
                        public_visibility="admin",
                        created_by_attempt_id=attempt_id,
                    ),
                    config=config,
                )
                create_relation(
                    db,
                    workspace.id,
                    RelationDraft(
                        source_version_id=version.id,
                        target_version_id=transcript_version_id,
                        relation_type=RelationType.DERIVED_FROM,
                        confidence=evidence.confidence,
                        created_by_attempt_id=attempt_id,
                        idempotency_key=canonical_json_hash(
                            ["evidence", str(version.id), str(transcript_version_id)]
                        ),
                    ),
                )
                evidence_dict = evidence.model_dump(mode="json")
                evidence_dict["timestamp_start_seconds"] = start_sec
                evidence_dict["timestamp_end_seconds"] = end_sec
                evidence_rows.append(
                    {
                        **evidence_dict,
                        "source_node_id": str(draft.source_id),
                        "evidence_node_id": str(version.node_id),
                    }
                )
            claims.append({"claim": claim.claim, "central": claim.central, "evidence": evidence_rows})
    for claim_index, claim in enumerate(claims):
        for evidence_index, evidence in enumerate(claim["evidence"]):
            result = await invoke_tool(
                attempt_id,
                "evidence.validate",
                {
                    "evidence_node_id": evidence["evidence_node_id"],
                    "source_node_id": str(draft.source_id),
                    "evidence_text": evidence["evidence_text"],
                    "timestamp_start_seconds": evidence["timestamp_start_seconds"],
                    "timestamp_end_seconds": evidence["timestamp_end_seconds"],
                    "central_claim": claim["central"],
                },
                call_key=f"analysis.evidence.{claim_index}.{evidence_index}",
                config=config,
            )
            if not result["valid"]:
                raise RuntimeTaskError(
                    "evidence_validation_failed",
                    category="validation",
                    retryable=True,
                    invalid_output_hash=canonical_json_hash(draft.model_dump(mode="json")),
                    validator_results={"status": "failed", "issues": result["error_codes"]},
                )
    source_score = round(
        0.60 * draft.purchase_recommendation_score + 0.40 * draft.reviewer_sentiment_score
    )
    analysis_payload = {
        **draft.model_dump(mode="json", exclude={"claims"}),
        "channel_id": source["channel_id"],
        "source_score": source_score,
        "claims": claims,
        "transcript_language": source["transcript_language"],
        "translated": source["translated"],
        "caption_kind": source["caption_kind"],
    }
    with session_scope() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        assert workspace is not None
        version = create_node(
            db,
            workspace.id,
            NodeDraft(
                node_type=NodeType.SOURCE_ANALYSIS,
                title=f"Source analysis — {source['source_title']}",
                body=_json_body("Validated source analysis", analysis_payload),
                trust_level=TrustLevel.DERIVED,
                source_uri=f"https://www.youtube.com/watch?v={source['video_id']}",
                source_language=source["transcript_language"],
                confidence=draft.evidence_quality_score,
                tags=("analysis", "review"),
                provenance={"source_id": str(draft.source_id), "source_score": source_score},
                public_visibility="admin",
                created_by_attempt_id=attempt_id,
            ),
            config=config,
        )
        create_relation(
            db,
            workspace.id,
            RelationDraft(
                source_version_id=version.id,
                target_version_id=uuid.UUID(source["source_version_id"]),
                relation_type=RelationType.DERIVED_FROM,
                confidence=draft.evidence_quality_score,
                created_by_attempt_id=attempt_id,
                idempotency_key=canonical_json_hash(
                    ["source-analysis", str(version.id), str(source["source_version_id"])]
                ),
            ),
        )
        analysis_payload["source_analysis_node_id"] = str(version.node_id)
    analysis = SourceAnalysis.model_validate(analysis_payload)
    return {"analysis": analysis.model_dump(mode="json")}


def _postprocess_audience(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    draft: AudienceAnalysisDraft,
    *,
    config: Settings,
) -> dict[str, Any]:
    comments = next(
        (
            item
            for item in _outputs_with_prefix(run.id, "fetch_comments.source_")
            if item.get("available") and item.get("source_id") == str(draft.source_id)
        ),
        None,
    )
    if comments is None:
        raise RuntimeTaskError("audience_source_mismatch", category="validation", retryable=True)
    with session_scope() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        comment_node = db.get(ContextNode, uuid.UUID(comments["comment_set_node_id"]))
        if not workspace or not comment_node or not comment_node.current_version_id:
            raise RuntimeTaskError("audience_graph_context_missing", category="storage")
        version = create_node(
            db,
            workspace.id,
            NodeDraft(
                node_type=NodeType.AUDIENCE_SIGNAL,
                title="Bounded audience signal",
                body=_json_body("Validated audience analysis", draft.model_dump(mode="json")),
                trust_level=TrustLevel.DERIVED,
                confidence=draft.confidence_score,
                tags=("analysis", "audience", "secondary"),
                provenance={"source_id": str(draft.source_id), "secondary_evidence": True},
                public_visibility="admin",
                created_by_attempt_id=attempt_id,
            ),
            config=config,
        )
        create_relation(
            db,
            workspace.id,
            RelationDraft(
                source_version_id=version.id,
                target_version_id=comment_node.current_version_id,
                relation_type=RelationType.DERIVED_FROM,
                confidence=draft.confidence_score,
                created_by_attempt_id=attempt_id,
                idempotency_key=canonical_json_hash(
                    ["audience", str(version.id), str(comment_node.current_version_id)]
                ),
            ),
        )
        payload = {**draft.model_dump(mode="json"), "audience_signal_node_id": str(version.node_id)}
    return {"analysis": AudienceAnalysis.model_validate(payload).model_dump(mode="json")}


def _postprocess_knowledge(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    plan: GraphMutationPlan,
    *,
    config: Settings,
) -> dict[str, Any]:
    finding_ids: list[str] = []
    with session_scope() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        if workspace is None:
            raise RuntimeTaskError("analysis_workspace_missing", category="storage")
        for index, finding in enumerate(plan.findings):
            source_versions: list[uuid.UUID] = []
            for source_id in finding.source_ids:
                analysis_node = db.scalar(
                    select(ContextNodeVersion)
                    .join(ContextNode, ContextNode.id == ContextNodeVersion.node_id)
                    .where(
                        ContextNode.workspace_id == workspace.id,
                        ContextNode.node_type == NodeType.SOURCE_ANALYSIS.value,
                        ContextNodeVersion.provenance["source_id"].astext == str(source_id),
                    )
                    .order_by(ContextNodeVersion.created_at.desc())
                    .limit(1)
                )
                if analysis_node:
                    source_versions.append(analysis_node.id)
            evidence_nodes = [db.get(ContextNode, item) for item in finding.evidence_node_ids]
            if not source_versions or any(
                item is None
                or item.workspace_id != workspace.id
                or item.node_type != NodeType.EVIDENCE.value
                or item.status != "active"
                for item in evidence_nodes
            ):
                raise RuntimeTaskError("finding_lineage_invalid", category="validation", retryable=True)
            version = create_node(
                db,
                workspace.id,
                NodeDraft(
                    node_type=NodeType.FINDING,
                    title=f"Finding {index + 1}",
                    body=finding.statement,
                    trust_level=TrustLevel.DERIVED,
                    confidence=finding.confidence,
                    tags=("finding", finding.relation),
                    provenance={
                        "source_ids": [str(item) for item in finding.source_ids],
                        "evidence_node_ids": [str(item) for item in finding.evidence_node_ids],
                    },
                    public_visibility="admin",
                    created_by_attempt_id=attempt_id,
                ),
                config=config,
            )
            finding_ids.append(str(version.node_id))
            for source_version_id in source_versions:
                create_relation(
                    db,
                    workspace.id,
                    RelationDraft(
                        source_version_id=version.id,
                        target_version_id=source_version_id,
                        relation_type=RelationType.DERIVED_FROM,
                        confidence=finding.confidence,
                        created_by_attempt_id=attempt_id,
                        idempotency_key=canonical_json_hash(
                            ["finding", str(version.id), str(source_version_id)]
                        ),
                    ),
                )
    return {"plan": plan.model_dump(mode="json"), "finding_node_ids": finding_ids}


def _score(reviews: list[dict[str, Any]], audiences: list[dict[str, Any]], requested: int) -> dict[str, Any]:
    positive = sum(item["positive_pct"] - item["negative_pct"] for item in audiences)
    delta = round(positive / len(audiences)) if audiences else 0
    confidence = round(sum(item["confidence_score"] for item in audiences) / len(audiences)) if audiences else 0
    channels = {item["channel_id"] for item in reviews}
    recurrence = min(1.0, max(0.0, len(channels) / max(1, requested)))
    source_signs = [item["source_score"] >= 60 for item in reviews]
    agreement = max(sum(source_signs), len(source_signs) - sum(source_signs)) / len(source_signs)
    request = ScoringPreviewInput(
        sources=tuple(
            {
                "source_id": item["source_id"],
                "channel_id": item["channel_id"],
                "reviewer_sentiment_score": item["reviewer_sentiment_score"],
                "purchase_recommendation_score": item["purchase_recommendation_score"],
                "evidence_quality_score": item["evidence_quality_score"],
                "review_type": item["review_type"],
                "translated": item["translated"],
            }
            for item in reviews
        ),
        requested_source_count=requested,
        audience_sentiment_delta=delta,
        audience_confidence=confidence,
        independent_recurrence=recurrence,
        agreement_ratio=agreement,
        central_conflict_ratio=0,
        has_long_term_evidence=any(item["review_type"] in {"long_term", "retrospective"} for item in reviews),
        central_claims_valid=all(any(claim["central"] for claim in item["claims"]) for item in reviews),
    )
    return preview_scoring(request).model_dump(mode="json")


def _deterministic_audit(draft: FinalReportDraft, reviews: list[dict[str, Any]], model_audit: AuditResult) -> AuditResult:
    evidence_ids = {
        evidence["evidence_node_id"]
        for review in reviews
        for claim in review["claims"]
        for evidence in claim["evidence"]
    }
    central_ids = {
        evidence["evidence_node_id"]
        for review in reviews
        for claim in review["claims"]
        if claim["central"]
        for evidence in claim["evidence"]
    }
    referenced = {
        str(item)
        for consensus in (*draft.consensus_pros, *draft.consensus_cons)
        for item in consensus.evidence_node_ids
    }
    fatal_issues: list[AuditIssue] = []
    if not central_ids:
        fatal_issues.append(AuditIssue(code="central_evidence_missing", field_path="source_analyses", retryable=False))
    if referenced - evidence_ids:
        fatal_issues.append(AuditIssue(code="untraceable_report_evidence", field_path="consensus", retryable=True))

    narrative_fields = {
        "report_draft.summary",
        "report_draft.who_should_buy",
        "report_draft.who_should_avoid",
        "report_draft.limitations",
        "report_draft.longest_usage_period",
        "report_draft.longest_usage_source_id",
        "report_draft.product_canonical_name",
        "report_draft.product_display_name",
    }
    valid_pro_indices = {
        i for i, item in enumerate(draft.consensus_pros)
        if item.evidence_node_ids and set(str(eid) for eid in item.evidence_node_ids) <= evidence_ids
    }
    valid_con_indices = {
        i for i, item in enumerate(draft.consensus_cons)
        if item.evidence_node_ids and set(str(eid) for eid in item.evidence_node_ids) <= evidence_ids
    }

    filtered_model_issues: list[AuditIssue] = []
    for issue in model_audit.issues:
        if issue.code == "missing_central_evidence":
            if any(issue.field_path.startswith(prefix) for prefix in narrative_fields):
                continue
            if issue.field_path.startswith("report_draft.disagreements"):
                continue
            pro_match = re.search(r"consensus_pros\[(\d+)\]", issue.field_path)
            if pro_match and int(pro_match.group(1)) in valid_pro_indices:
                continue
            con_match = re.search(r"consensus_cons\[(\d+)\]", issue.field_path)
            if con_match and int(con_match.group(1)) in valid_con_indices:
                continue
        filtered_model_issues.append(issue)

    all_issues = tuple({(item.code, item.field_path): item for item in (*fatal_issues, *filtered_model_issues)}.values())
    if fatal_issues:
        return AuditResult(verdict="fail", issues=all_issues)
    if not all_issues:
        return AuditResult(verdict="pass", issues=())
    if model_audit.verdict == "pass_with_warnings":
        return AuditResult(verdict="pass_with_warnings", issues=all_issues)
    return AuditResult(verdict="fail", issues=all_issues)


async def _execute_agent(
    attempt_id: uuid.UUID,
    task: TaskRun,
    run: AnalysisRun,
    snapshot: ConfigurationSnapshot,
    attempt_input: dict[str, Any],
    *,
    config: Settings,
) -> dict[str, Any]:
    with session_scope() as db:
        agent = db.get(AgentVersion, task.agent_version_id)
        definition = db.get(AgentDefinition, agent.definition_id) if agent else None
        if not definition or definition.key not in AGENT_REGISTRY:
            raise RuntimeTaskError("agent_definition_missing", category="configuration")
        spec = AGENT_REGISTRY[definition.key]
    payload, seeds = _agent_task_input(spec, task, run)
    if "_skip" in payload:
        return {"skipped": True, "reason": payload["_skip"], "source_index": payload.get("source_index")}
    if "_shortcut" in payload:
        return {**payload["_shortcut"], "correction_skipped": True}
    if spec.key == "source_curator" and _all_source_candidates_excluded(payload):
        # A valid curation must select at least one source. Stop before spending
        # tokens on schema retries when deterministic matching rejected all of them.
        raise RuntimeTaskError("youtube_no_candidates", category="not_found")
    result = await _call_agent(
        attempt_id,
        task,
        run,
        snapshot,
        spec,
        payload,
        seeds,
        attempt_input,
        config=config,
    )
    if spec.key == "review_analyst":
        return await _postprocess_review(attempt_id, run, SourceAnalysisDraft.model_validate(result), config=config)
    if spec.key == "audience_analyst":
        return _postprocess_audience(attempt_id, run, AudienceAnalysisDraft.model_validate(result), config=config)
    if spec.key == "knowledge_curator":
        return _postprocess_knowledge(attempt_id, run, GraphMutationPlan.model_validate(result), config=config)
    if spec.key == "consensus_analyst":
        reviews = [item["analysis"] for item in _outputs_with_prefix(run.id, "analyze_review.source_") if item.get("analysis")]
        audiences = [item["analysis"] for item in _outputs_with_prefix(run.id, "analyze_audience.source_") if item.get("analysis")]
        scoring = _score(reviews, audiences, int(run.requested_options.get("source_count", 5)))
        return {
            "draft": FinalReportDraft.model_validate(result).model_dump(mode="json"),
            "scoring": scoring,
            "source_analyses": reviews,
            "audience_analyses": audiences,
        }
    if spec.key == "quality_auditor":
        consensus_key = "correct_consensus" if task.input_payload.get("reaudit_stage") else "build_consensus"
        consensus = _task_output(run.id, consensus_key) or {}
        reviews = consensus.get("source_analyses", [])
        audit = _deterministic_audit(
            FinalReportDraft.model_validate(consensus.get("draft", {})),
            reviews,
            AuditResult.model_validate(result),
        )
        return {"audit": audit.model_dump(mode="json"), "consensus_task": consensus_key}
    return result.model_dump(mode="json")


def _publish_report(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    snapshot: ConfigurationSnapshot,
    *,
    config: Settings,
) -> dict[str, Any]:
    audit_payload = _task_output(run.id, "reaudit_report") or {}
    audit = AuditResult.model_validate(audit_payload.get("audit", {}))
    if audit.verdict == "fail":
        raise RuntimeTaskError("report_audit_failed", category="quality")
    consensus_key = audit_payload.get("consensus_task", "correct_consensus")
    consensus = _task_output(run.id, consensus_key) or _task_output(run.id, "build_consensus") or {}
    draft = FinalReportDraft.model_validate(consensus.get("draft", {}))
    scoring = consensus.get("scoring", {})
    reviews = consensus.get("source_analyses", [])
    audiences = consensus.get("audience_analyses", [])
    if not reviews or not scoring.get("publishable"):
        raise RuntimeTaskError("report_publication_gate_failed", category="quality")
    warnings = list(scoring.get("warning_codes", []))
    if audit.verdict == "pass_with_warnings":
        warnings.append("quality_audit_warning")
    requested = int(run.requested_options.get("source_count", 5))
    status = "partial" if len(reviews) < requested or warnings else "complete"
    report_id = uuid.uuid5(run.id, "validated-internal-report")
    with session_scope() as db:
        locked_run = db.get(AnalysisRun, run.id)
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        if not locked_run or not workspace:
            raise RuntimeTaskError("analysis_workspace_missing", category="storage")
        existing = db.scalar(select(Report).where(Report.run_id == run.id))
        if existing:
            return {"report_id": str(existing.id), "status": existing.payload["status"]}
        total_tokens = int(
            db.scalar(select(func.coalesce(func.sum(UsageEvent.total_tokens), 0)).where(UsageEvent.run_id == run.id))
            or 0
        )
        payload = FinalReport(
            report_id=report_id,
            run_id=run.id,
            workspace_id=workspace.id,
            product_display_name=draft.product_display_name,
            product_canonical_name=draft.product_canonical_name,
            status=status,
            source_count_requested=requested,
            source_count_analyzed=len(reviews),
            base_score=scoring["base_score"],
            audience_adjustment=scoring["audience_adjustment"],
            overall_score=scoring["overall_score"],
            verdict=scoring["verdict"],
            confidence=scoring["confidence"],
            confidence_band=scoring["confidence_band"],
            summary=draft.summary,
            consensus_pros=draft.consensus_pros,
            consensus_cons=draft.consensus_cons,
            disagreements=draft.disagreements,
            longest_usage_period=draft.longest_usage_period,
            longest_usage_source_id=draft.longest_usage_source_id,
            who_should_buy=draft.who_should_buy,
            who_should_avoid=draft.who_should_avoid,
            limitations=draft.limitations,
            warnings=tuple(dict.fromkeys(warnings)),
            source_analyses=tuple(reviews),
            audience_analyses=tuple(audiences),
            total_tokens=total_tokens,
            configuration_snapshot_id=snapshot.id,
            generated_at=utc_now(),
        ).model_dump(mode="json")
        report_node_id = uuid.uuid5(run.id, "validated-internal-report-node")
        node_version = create_node(
            db,
            workspace.id,
            NodeDraft(
                node_type=NodeType.REPORT,
                title=f"Internal report — {draft.product_display_name}",
                body=_json_body("Validated internal report", payload),
                trust_level=TrustLevel.DERIVED,
                confidence=scoring["confidence"],
                tags=("report", status),
                provenance={"audit_status": audit.verdict, "schema_version": 1},
                public_visibility="admin",
                created_by_attempt_id=attempt_id,
            ),
            node_id=report_node_id,
            config=config,
        )
        for review in reviews:
            analysis_node = db.get(ContextNode, uuid.UUID(review["source_analysis_node_id"]))
            if (
                analysis_node is None
                or analysis_node.workspace_id != workspace.id
                or analysis_node.node_type != NodeType.SOURCE_ANALYSIS.value
                or analysis_node.current_version_id is None
            ):
                raise RuntimeTaskError("report_lineage_invalid", category="quality")
            create_relation(
                db,
                workspace.id,
                RelationDraft(
                    source_version_id=node_version.id,
                    target_version_id=analysis_node.current_version_id,
                    relation_type=RelationType.DERIVED_FROM,
                    confidence=scoring["confidence"],
                    created_by_attempt_id=attempt_id,
                    idempotency_key=canonical_json_hash(
                        ["report", str(node_version.id), str(analysis_node.current_version_id)]
                    ),
                ),
            )
        report = Report(
            id=report_id,
            run_id=run.id,
            workspace_id=workspace.id,
            configuration_snapshot_id=snapshot.id,
            report_node_id=node_version.node_id,
            schema_version=1,
            payload=payload,
            content_hash=canonical_json_hash(payload),
            audit_result=audit.model_dump(mode="json"),
            audit_status=audit.verdict,
            status="published",
            published_at=utc_now(),
        )
        db.add(report)
        db.flush()
        locked_run.report_id = report.id
    return {"report_id": str(report_id), "status": status}


async def _execute(
    attempt_id: uuid.UUID,
    handler: str,
    input_payload: dict[str, Any],
    *,
    config: Settings,
) -> dict[str, Any]:
    _, task, run, snapshot = _attempt_context(attempt_id)
    if handler == "analysis.validate_request":
        source_count = int(run.requested_options.get("source_count", config.default_video_count))
        if source_count < 3 or source_count > 8:
            raise RuntimeTaskError("source_count_invalid", category="validation")
        with session_scope() as db:
            workspace = create_workspace(db, run.id, config=config)
            workspace_id = workspace.id
        return {
            "product_name": run.product_input,
            "source_count": source_count,
            "analyze_comments": bool(run.requested_options.get("analyze_comments", False)),
            "workspace_id": str(workspace_id),
        }
    if handler == "analysis.discover_candidates":
        plan = QueryPlan.model_validate(_task_output(run.id, "plan_research") or {})
        return await _discover_candidates(attempt_id, run, plan, config=config)
    if handler == "analysis.fetch_transcript":
        return await _fetch_transcript(
            attempt_id,
            run,
            int(input_payload["source_index"]),
            config=config,
        )
    if handler == "analysis.fetch_comments":
        return await _fetch_comments(
            attempt_id,
            run,
            int(input_payload["source_index"]),
            config=config,
        )
    if handler == "analysis.publish_report":
        return _publish_report(attempt_id, run, snapshot, config=config)
    if handler.startswith("analysis.agent."):
        return await _execute_agent(
            attempt_id,
            task,
            run,
            snapshot,
            input_payload,
            config=config,
        )
    raise RuntimeTaskError("analysis_handler_unknown", category="configuration")


async def _execute_with_heartbeat(
    attempt_id: uuid.UUID,
    handler: str,
    input_payload: dict[str, Any],
    *,
    config: Settings,
) -> dict[str, Any]:
    import contextlib
    from app.runtime.service import heartbeat_attempt

    async def _heartbeat_loop() -> None:
        interval = max(5, min(15, config.runtime_task_lease_seconds // 3))
        while True:
            await asyncio.sleep(interval)
            try:
                heartbeat_attempt(attempt_id, config)
            except Exception:
                pass

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        return await _execute(attempt_id, handler, input_payload, config=config)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


def execute_analysis_handler(
    attempt_id: uuid.UUID,
    handler: str,
    input_payload: dict[str, Any],
    *,
    config: Settings = settings,
) -> dict[str, Any]:
    try:
        return asyncio.run(_execute_with_heartbeat(attempt_id, handler, input_payload, config=config))
    except ContextBudgetExceeded as exc:
        raise RuntimeTaskError("context_budget_exceeded", category="limit", retryable=False) from exc
    except ToolExecutionError as exc:
        raise RuntimeTaskError(exc.code, category=exc.category, retryable=exc.retryable) from exc
