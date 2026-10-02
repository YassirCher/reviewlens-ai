from __future__ import annotations

import asyncio
import json
import random
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
    AuditResult,
    AuditIssue,
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
from app.analysis.grounding import ground_report
from app.analysis.projection import project_claims
from app.analysis.product_info import (
    ProductExtractionDraft,
    SampleUsed,
    merge_product_info,
    select_product_chunk_indexes,
    source_metadata,
    validate_extraction,
    validate_span_products,
)
from app.analysis.registry import AGENT_REGISTRY, AgentSpec, UNIVERSAL_POLICY, snapshot_input_model, snapshot_output_model
from app.analysis.review import ClassifiedVideoExtraction, VideoExtraction, bind_review, normalize_usage, parse_video_extraction, video_extraction_schema
from app.analysis.rendering import NormalizedBuyingSynthesis, PrioritizedSynthesisInput, prioritized_synthesis_input
from app.analysis.synthesis import AtomicBuyingSynthesis, AtomicSynthesisInput, BuyingSynthesis, CatalogRepairSynthesisInput, EvidenceBoundBuyingSynthesis, QuoteSynthesisInput, RepairSynthesisInput, SynthesisBindingError, catalog_repair_synthesis_input, compact_synthesis_input, evidence_bound_synthesis_schema, quote_synthesis_input, repair_synthesis_input, unchanged_rejected_findings
from app.analysis.audit import AuditDecisionError, CatalogAuditorInput, CitedAuditorInput, DecisionAuditorInput, FindingAuditResult, cited_audit_input, compact_audit_input, decision_audit_input, finding_audit_schema
from app.analysis.audit_parts import PartAuditorInput, ReferencedAuditResult, part_audit_input, referenced_audit_schema
from app.analysis.audit_parts import OwnedAuditResult, owned_audit_schema
from app.analysis.spans import CaptionBindingError, SpanVideoExtraction, SpanReviewInput, caption_spans, span_extraction_schema, bind_span_extraction
from app.analysis.audience import AudienceBindingError, BoundAudienceDraft, audience_schema, comment_catalog, bind_audience
from app.analysis.rendering import CompleteBuyingSynthesis
from app.config import Settings, settings
from app.db.models import (
    AgentDefinition,
    AgentVersion,
    AnalysisRun,
    ConfigurationSnapshot,
    ContextNode,
    ContextEdge,
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
from app.knowledge.service import KnowledgeGraphError, create_node, create_relation, create_workspace, read_version_body
from app.knowledge.storage import MarkdownValidationError
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
    YouTubeTranscriptInput,
    YouTubeVideoDetailsOutput,
)
from app.tools.errors import ToolExecutionError
from app.tools.evidence import transcript_excerpt_matches
from app.tools.runner import invoke_tool
from app.tools.scoring import preview_scoring
from app.tools.youtube import chunk_transcript, rank_candidates, source_slot_queues
from app.tools.caption_cache import available_caption, caption_origin
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


def _has_task_prefix(run_id: uuid.UUID, prefix: str) -> bool:
    with session_scope() as db:
        return db.scalar(select(TaskRun.id).where(
            TaskRun.run_id == run_id,
            TaskRun.workflow_task_key.like(f"{prefix}%"),
        ).limit(1)) is not None


def _product_source_material(run: AnalysisRun, source: dict[str, Any], config: Settings) -> tuple[dict, str]:
    with session_scope() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        source_node = db.get(ContextNode, uuid.UUID(source["source_id"]))
        transcript_node = db.get(ContextNode, uuid.UUID(source["transcript_node_id"]))
        if (
            workspace is None or source_node is None or transcript_node is None
            or source_node.workspace_id != workspace.id or transcript_node.workspace_id != workspace.id
            or source_node.node_type != NodeType.SOURCE or transcript_node.node_type != NodeType.TRANSCRIPT
            or source_node.current_version_id is None or transcript_node.current_version_id is None
        ):
            raise RuntimeTaskError("product_source_lineage_invalid", category="validation")
        source_version = db.get(ContextNodeVersion, source_node.current_version_id)
        transcript_version = db.get(ContextNodeVersion, transcript_node.current_version_id)
        if source_version is None or transcript_version is None:
            raise RuntimeTaskError("product_source_lineage_invalid", category="validation")
        if (
            transcript_version.provenance.get("video_id") != source.get("video_id")
            or source_version.source_uri != f"https://www.youtube.com/watch?v={source.get('video_id')}"
        ):
            raise RuntimeTaskError("product_source_lineage_invalid", category="validation")
        metadata = source_metadata(read_version_body(workspace, source_version, config=config)[1])
        transcript_body = read_version_body(workspace, transcript_version, config=config)[1]
    if metadata.get("video_id") != source.get("video_id"):
        raise RuntimeTaskError("product_source_identity_invalid", category="validation")
    return metadata, transcript_body


def _product_context_seeds(
    run: AnalysisRun, task: TaskRun, *, config: Settings, token_budget: int, max_nodes_per_source: int,
) -> tuple[uuid.UUID, ...]:
    """Seed useful, separated spans of this video without increasing prompt size."""
    source_index = int(task.input_payload.get("source_index", 0) or 0)
    source = _task_output(run.id, f"fetch_transcript.source_{source_index}") or {}
    chunk_ids = [uuid.UUID(item) for item in source.get("transcript_chunk_ids") or []]
    source_id = uuid.UUID(source["source_id"])
    if len(chunk_ids) < 2 or token_budget < 200:
        return (source_id, *chunk_ids[:1])
    expected_uri = f"https://www.youtube.com/watch?v={source['video_id']}"
    with session_scope() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
        source_node = db.get(ContextNode, source_id)
        if (
            workspace is None or source_node is None or source_node.workspace_id != workspace.id
            or source_node.node_type != NodeType.SOURCE or source_node.current_version_id is None
        ):
            raise RuntimeTaskError("product_source_lineage_invalid", category="validation")
        source_version = db.get(ContextNodeVersion, source_node.current_version_id)
        if source_version is None or source_version.source_uri != expected_uri:
            raise RuntimeTaskError("product_source_lineage_invalid", category="validation")
        source_cost = estimate_tokens(read_version_body(workspace, source_version, config=config)[1]) + 160
        bodies: list[str] = []
        costs: list[int] = []
        for chunk_id in chunk_ids:
            node = db.get(ContextNode, chunk_id)
            version = db.get(ContextNodeVersion, node.current_version_id) if node and node.current_version_id else None
            if (
                node is None or node.workspace_id != workspace.id or node.node_type != NodeType.TRANSCRIPT_CHUNK
                or version is None or version.source_uri != expected_uri
                or version.provenance.get("video_id") != source["video_id"]
            ):
                bodies.append("")
                costs.append(token_budget + 1)
                continue
            body = read_version_body(workspace, version, config=config)[1]
            bodies.append(body)
            costs.append(estimate_tokens(body) + 160)
    indexes = select_product_chunk_indexes(
        bodies, costs, max(0, int(token_budget * 0.8) - source_cost),
        max_extra=max(0, min(4, max_nodes_per_source - 2)),
    )
    return (source_id, *(chunk_ids[index] for index in indexes))


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
    curated = _task_output(run.id, "curate_sources") or {}
    curation = SourceCuration.model_validate({key: curated[key] for key in ("decisions", "ordered_video_ids")})
    candidates = {item["video_id"]: item for item in discovery.get("candidates", [])}
    source_count = int(run.requested_options.get("source_count", config.default_video_count))
    queues = curated.get("source_queues") or source_slot_queues(list(curation.ordered_video_ids), candidates,
        [item.model_dump(mode="json") for item in curation.decisions], source_count)
    queue = queues[source_index - 1]
    language = discovery.get("requested_language", "en")
    if queue and queue[0] not in curated.get("cached_video_ids", []):
        await asyncio.sleep(0.35 * (source_index - 1) + random.uniform(0.05, 0.25))
    blocked = False

    for video_id in queue:
        candidate = candidates[video_id]
        request = YouTubeTranscriptInput(video_id=video_id, requested_language=language)
        if blocked and not await asyncio.to_thread(available_caption, request, config=config):
            continue
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
            if exc.code == "transcript_access_blocked":
                blocked = True
                continue
            if exc.code.startswith("transcript_"):
                continue
            raise RuntimeTaskError(exc.code, category=exc.category, retryable=exc.retryable) from exc
        transcript = YouTubeTranscriptOutput.model_validate(payload)
        cached = await asyncio.to_thread(available_caption, request, config=config)
        fetched_at = cached.fetched_at if cached else utc_now()
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
                    "caption_origin": caption_origin(config),
                    "caption_fetched_at": fetched_at.isoformat(),
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
    if blocked:
        raise RuntimeTaskError("transcript_access_blocked", category="upstream", retryable=False)
    return {"available": False, "source_index": source_index, "reason": "transcript_unavailable"}


async def _curated_source_queues(run: AnalysisRun, curation: SourceCuration, config: Settings) -> dict:
    discovery = _task_output(run.id, "discover_candidates") or {}
    candidates = {item["video_id"]: item for item in discovery.get("candidates", [])}
    eligible = {item.video_id for item in curation.decisions if item.eligible}
    language = discovery.get("requested_language", "en")
    allowed = list(dict.fromkeys(video_id for video_id in curation.ordered_video_ids
        if video_id in eligible and video_id in candidates and not candidates[video_id].get("deterministic_exclusion")))
    slots = asyncio.Semaphore(4)

    async def cached(video_id: str) -> bool:
        async with slots:
            return bool(await asyncio.to_thread(available_caption,
                YouTubeTranscriptInput(video_id=video_id, requested_language=language), config=config))

    hits = await asyncio.gather(*(cached(video_id) for video_id in allowed))
    cached_ids = [video_id for video_id, hit in zip(allowed, hits, strict=True) if hit]
    queues = source_slot_queues(list(curation.ordered_video_ids), candidates,
        [item.model_dump(mode="json") for item in curation.decisions],
        int(run.requested_options.get("source_count", 5)), frozenset(cached_ids))
    return {**curation.model_dump(mode="json"), "source_queues": queues, "cached_video_ids": cached_ids}


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
    if spec.key == "product_information_analyst":
        source = _task_output(run.id, f"fetch_transcript.source_{source_index}") or {}
        if not source.get("available"):
            return {"_skip": "source_unavailable", "source_index": source_index}, ()
        chunk_ids = source.get("transcript_chunk_ids") or []
        if not chunk_ids:
            return {"_skip": "transcript_unavailable", "source_index": source_index}, ()
        return {
            "canonical_product": run.canonical_product,
            "source_id": source["source_id"],
            "transcript_node_id": source["transcript_node_id"],
        }, (uuid.UUID(source["source_id"]), uuid.UUID(chunk_ids[0]))
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
    if spec.output_model is CompleteBuyingSynthesis:
        retained = {review["source_id"] for review in reviews}
        audiences = [audience for audience in audiences if audience["source_id"] in retained]
    if spec.key == "knowledge_curator":
        if not reviews:
            raise RuntimeTaskError("no_valid_source_analyses", category="quality")
        return {"source_analyses": reviews, "audience_analyses": audiences}, ()
    if spec.key == "consensus_analyst":
        discovery = _task_output(run.id, "discover_candidates") or {}
        report_under_repair = None
        if task.input_payload.get("correction_stage"):
            audit = _task_output(run.id, "audit_report") or {}
            original = _task_output(run.id, "build_consensus") or {}
            if audit.get("grounding_terminal"):
                return {"_shortcut": original}, ()
            if (audit.get("audit") or {}).get("verdict") in {"pass", "pass_with_warnings"}:
                return {"_shortcut": original}, ()
            issues = (audit.get("audit") or {}).get("issues", [])
            report_under_repair = original.get("draft")
        else:
            issues = []
        return {
            "product_display_name": run.product_input,
            "product_canonical_name": discovery.get("canonical_product", run.canonical_product),
            "requested_source_count": int(run.requested_options.get("source_count", 5)),
            "source_analyses": reviews,
            "audience_analyses": audiences,
            "correction_issues": issues,
            "report_under_repair": report_under_repair,
            "audit_diagnostics": audit.get("audit_diagnostics", {}) if task.input_payload.get("correction_stage") else {},
            "claim_catalog": [entry for output in _outputs_with_prefix(run.id, "analyze_review.source_")
                              for entry in output.get("claim_catalog", [])],
        }, ()
    if spec.key == "quality_auditor":
        if task.input_payload.get("reaudit_stage"):
            first_audit = _task_output(run.id, "audit_report") or {}
            if first_audit.get("grounding_terminal") or (first_audit.get("audit") or {}).get("verdict") in {"pass", "pass_with_warnings"}:
                return {"_shortcut": first_audit}, ()
            consensus = _task_output(run.id, "correct_consensus") or {}
        else:
            consensus = _task_output(run.id, "build_consensus") or {}
        draft = consensus.get("draft", {})
        if draft and not draft.get("consensus_pros") and not draft.get("consensus_cons"):
            central = any(claim.get("central") and any(ref["support_type"] == "supports" for ref in claim["evidence"])
                          for review in reviews for claim in review["claims"])
            return {"_shortcut": {"audit": {"verdict": "fail", "issues": [{"code": "grounded_conclusion_missing",
                    "field_path": "report_draft", "retryable": central}]}, "safe_draft": draft,
                    "consensus_task": "correct_consensus" if task.input_payload.get("reaudit_stage") else "build_consensus",
                    "grounding_terminal": not central, "audit_diagnostics": {"finding_checks": [],
                    "deterministic_empty_draft": True, "synthesis": consensus.get("synthesis_diagnostics", [])}}}, ()
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
            input_model=snapshot_input_model(spec.key, version.input_schema),
            output_model=snapshot_output_model(spec.key, version.output_schema),
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
        # Legacy snapshots keep their original input contract.
        input_payload = dict(payload)
        if spec.input_model is SpanReviewInput:
            input_payload["canonical_product"] = run.canonical_product
        if spec.input_model is AtomicSynthesisInput:
            input_payload = compact_synthesis_input(input_payload)
        if spec.input_model is QuoteSynthesisInput:
            input_payload = quote_synthesis_input(input_payload)
        if spec.input_model is RepairSynthesisInput:
            input_payload = repair_synthesis_input(input_payload)
        if spec.input_model is CatalogRepairSynthesisInput:
            input_payload = catalog_repair_synthesis_input(input_payload)
        if spec.input_model is PrioritizedSynthesisInput:
            input_payload = prioritized_synthesis_input(input_payload)
        input_payload.pop("claim_catalog", None)
        if spec.input_model is CatalogAuditorInput:
            input_payload = compact_audit_input(input_payload)
        if spec.input_model is CitedAuditorInput:
            input_payload = cited_audit_input(input_payload)
        if spec.input_model is DecisionAuditorInput:
            input_payload = decision_audit_input(input_payload)
        if spec.input_model is PartAuditorInput:
            input_payload = part_audit_input(input_payload)
        input_payload.pop("audit_diagnostics", None)
        if spec.key == "consensus_analyst" and "report_under_repair" not in spec.input_model.model_fields:
            input_payload.pop("report_under_repair", None)
        validated_input = spec.input_model.model_validate(input_payload)
    except ValidationError as exc:
        raise RuntimeTaskError(
            "agent_input_invalid",
            category="validation",
            validator_results=_safe_validation(exc),
        ) from exc
    except (KeyError, ValueError) as exc:
        raise RuntimeTaskError("agent_input_reference_invalid", category="validation") from exc
    task_input_data = validated_input.model_dump(mode="json")
    task_input_tokens = estimate_tokens(json.dumps(task_input_data))
    # Reserve tokens for system prompt (~600 tokens), task wrapper (~300 tokens), and safety margin (400 tokens)
    available_context = max(0, spec.max_input_tokens - task_input_tokens - 1300)
    budget_override = min(spec.retrieval_policy.input_token_budget, available_context) if available_context > 0 else 0
    if (spec.key == "product_information_analyst" or issubclass(spec.output_model, VideoExtraction) or issubclass(spec.output_model, SpanVideoExtraction)) and budget_override >= 200:
        seeds = _product_context_seeds(
            run, task, config=config, token_budget=budget_override,
            max_nodes_per_source=spec.retrieval_policy.maximum_nodes_per_source,
        )

    rendered, manifest_id, context_tokens = _context_packet(
        attempt_id,
        run,
        spec,
        seeds,
        run.canonical_product,
        config=config,
        budget_override=budget_override,
    )
    spans = ()
    comment_refs, comment_dates = set(), set()
    if issubclass(spec.output_model, SpanVideoExtraction):
        spans = caption_spans(rendered, run.canonical_product)
        if not spans:
            raise RuntimeTaskError("caption_span_catalog_empty", category="quality")
        rendered = '<untrusted-data source="assigned-video-caption-catalog">\n' + "\n".join(
            json.dumps(span.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) for span in spans) + '\n</untrusted-data>'
        context_tokens = estimate_tokens(rendered)
    if issubclass(spec.output_model, BoundAudienceDraft):
        rendered, comment_refs, comment_dates = comment_catalog(rendered, run.created_at)
        rendered = '<untrusted-data source="retained-audience-comments">\n' + rendered + '\n</untrusted-data>'
        context_tokens = estimate_tokens(rendered)
    correction = attempt_input.get("correction")
    task_instruction = spec.purpose
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
        response_schema=(span_extraction_schema(spans, spec.output_model) if issubclass(spec.output_model, SpanVideoExtraction) else
                         audience_schema(spec.output_model) if issubclass(spec.output_model, BoundAudienceDraft) else
                         owned_audit_schema(PartAuditorInput.model_validate(task_input_data)) if spec.output_model is OwnedAuditResult else
                         video_extraction_schema(spec.output_model) if issubclass(spec.output_model, VideoExtraction) else
                         referenced_audit_schema(PartAuditorInput.model_validate(task_input_data))
                         if spec.output_model is ReferencedAuditResult else
                         finding_audit_schema(DecisionAuditorInput.model_validate(task_input_data))
                         if spec.output_model is FindingAuditResult else
                         evidence_bound_synthesis_schema(spec.input_model.model_validate(task_input_data))
                         if issubclass(spec.output_model, EvidenceBoundBuyingSynthesis) else spec.output_model.model_json_schema()),
        schema_name=spec.output_model.__name__,
        estimated_prompt_tokens=max(prompt_tokens, context_tokens),
        estimated_cost_microusd=estimated_cost,
        max_network_attempts=1,
        optional_output_fields=("product_information",) if issubclass(spec.output_model, VideoExtraction) or issubclass(spec.output_model, SpanVideoExtraction) else (),
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
            invalid_output_hash=exc.invalid_output_hash,
            validator_results=exc.validation_diagnostics or {
                "status": "failed",
                "provider_category": exc.category.value,
                "provider_code": exc.provider_code,
            },
        ) from exc
    try:
        if issubclass(spec.output_model, SpanVideoExtraction):
            extraction, diagnostics = bind_span_extraction(result.content, spans, run.canonical_product)
            object.__setattr__(extraction, "_span_diagnostics", diagnostics)
            return extraction
        if issubclass(spec.output_model, BoundAudienceDraft):
            audience, diagnostics = bind_audience(result.content, source_id=uuid.UUID(payload["source_id"]),
                sampled=payload["comments_sampled"], refs=comment_refs, dates=comment_dates)
            object.__setattr__(audience, "_binding_diagnostics", diagnostics)
            return audience
        return (parse_video_extraction(result.content, spec.output_model) if issubclass(spec.output_model, VideoExtraction)
                else spec.output_model.model_validate(result.content))
    except ValidationError as exc:
        raise RuntimeTaskError(
            "agent_output_invalid",
            category="validation",
            retryable=True,
            invalid_output_hash=canonical_json_hash(result.content),
            validator_results=_safe_validation(exc),
        ) from exc
    except ValueError as exc:
        raise RuntimeTaskError("agent_output_reference_invalid", category="validation", retryable=True,
            invalid_output_hash=canonical_json_hash(result.content), validator_results={"status": "failed",
                "issues": [exc.issue if isinstance(exc, (CaptionBindingError, AudienceBindingError)) else {"path": "review.claims.span_refs", "type": str(exc)[:180]}]}) from exc


async def _postprocess_review(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    draft: SourceAnalysisDraft,
    *,
    config: Settings,
    normalize_extended_use: bool = False,
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
        transcript_version = db.get(ContextNodeVersion, transcript_version_id)
        if (
            not source_version or not transcript_version
            or source_version.provenance.get("video_id") != source["video_id"]
            or transcript_version.provenance.get("video_id") != source["video_id"]
        ):
            raise RuntimeTaskError("review_source_lineage_invalid", category="validation", retryable=False)
        transcript_body = read_version_body(workspace, transcript_version, config=config)[1]
        if normalize_extended_use:
            draft, _ = normalize_usage(draft, transcript_body)
        validated_claims = []
        invalid_evidence = 0
        wrong_source_ids = 0
        invalid_quotes_or_times = 0
        for claim in draft.claims:
            valid_refs = []
            for evidence in claim.evidence:
                if evidence.source_node_id != draft.source_id:
                    wrong_source_ids += 1
                    invalid_evidence += 1
                elif transcript_excerpt_matches(
                    transcript_body, evidence.evidence_text,
                    evidence.timestamp_start_seconds, evidence.timestamp_end_seconds,
                ):
                    valid_refs.append(evidence)
                else:
                    invalid_quotes_or_times += 1
                    invalid_evidence += 1
            if any(ref.support_type == "supports" for ref in valid_refs):
                validated_claims.append((claim, valid_refs))
        if not any(claim.central for claim, _ in validated_claims):
            return {
                "skipped": True,
                "reason": "central_evidence_unverified",
                "source_index": source["source_index"],
                "validation_counts": {
                    "claims": len(draft.claims),
                    "valid_noncentral_claims": len(validated_claims),
                    "wrong_source_ids": wrong_source_ids,
                    "invalid_quotes_or_times": invalid_quotes_or_times,
                },
            }
        source_uri = source_version.source_uri if source_version else None
        claims: list[dict[str, Any]] = []
        for claim_index, (claim, valid_refs) in enumerate(validated_claims):
            evidence_rows: list[dict[str, Any]] = []
            for evidence_index, evidence in enumerate(valid_refs):
                start_sec = evidence.timestamp_start_seconds
                end_sec = evidence.timestamp_end_seconds
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
            if evidence_rows:
                claims.append({"claim": claim.claim, "central": claim.central, "evidence": evidence_rows})
    for claim_index, claim_row in enumerate(claims):
        for evidence_index, evidence in enumerate(claim_row["evidence"]):
            result = await invoke_tool(
                attempt_id,
                "evidence.validate",
                {
                    "evidence_node_id": evidence["evidence_node_id"],
                    "source_node_id": str(draft.source_id),
                    "evidence_text": evidence["evidence_text"],
                    "timestamp_start_seconds": evidence["timestamp_start_seconds"],
                    "timestamp_end_seconds": evidence["timestamp_end_seconds"],
                    "central_claim": claim_row["central"],
                },
                call_key=f"analysis.evidence.{claim_index}.{evidence_index}",
                config=config,
            )
            if not result["valid"]:
                raise RuntimeTaskError(
                    "evidence_validation_failed",
                    category="validation",
                    retryable=False,
                    invalid_output_hash=canonical_json_hash(draft.model_dump(mode="json")),
                    validator_results={"status": "failed", "issues": result["error_codes"]},
                )
    source_score = round(
        0.60 * draft.purchase_recommendation_score + 0.40 * draft.reviewer_sentiment_score
    )
    safe_prose = {
        "recommendation_summary": "Some source details could not be verified; inspect the cited claims.",
        "pros": (), "cons": (), "major_issues": (),
        "recommended_for": (), "not_recommended_for": (),
    } if invalid_evidence else {}
    analysis_payload = {
        **draft.model_dump(mode="json", exclude={"claims"}),
        **safe_prose,
        "channel_id": source["channel_id"],
        "source_score": source_score,
        "evidence_quality_score": min(draft.evidence_quality_score, round(100 * len(claims) / len(draft.claims))),
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
    evidence_owners: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    finding_ids: list[str] = []
    with session_scope() as db:
        if evidence_owners is not None:
            db.scalar(select(AnalysisRun).where(AnalysisRun.id == run.id).with_for_update())
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
            if len(source_versions) != len(finding.source_ids) or any(
                item is None
                or item.workspace_id != workspace.id
                or item.node_type != NodeType.EVIDENCE.value
                or item.status != "active"
                for item in evidence_nodes
            ):
                raise RuntimeTaskError("finding_lineage_invalid", category="validation",
                                       retryable=evidence_owners is None)
            if evidence_owners is not None:
                for node in evidence_nodes:
                    owner = evidence_owners.get(str(node.id))
                    if owner is None or owner[0] not in {str(sid) for sid in finding.source_ids}:
                        raise RuntimeTaskError("finding_lineage_invalid", category="validation")
                    _verify_projection_lineage(db, workspace.id, node, uuid.UUID(owner[0]))
            stable_id = uuid.uuid5(run.id, "projected-finding:" + canonical_json_hash(
                finding.model_dump(mode="json"))) if evidence_owners is not None else None
            existing = db.get(ContextNode, stable_id) if stable_id is not None else None
            if existing and (existing.workspace_id != workspace.id or existing.status != "active"
                             or existing.node_type != NodeType.FINDING.value or existing.current_version_id is None):
                raise RuntimeTaskError("finding_lineage_invalid", category="validation")
            version = db.get(ContextNodeVersion, existing.current_version_id) if existing else create_node(
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
                config=config, node_id=stable_id,
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
            if evidence_owners is not None:
                for node in evidence_nodes:
                    relation = (RelationType.SUPPORTS if evidence_owners[str(node.id)][1] == "supports"
                                else RelationType.CONTRADICTS)
                    create_relation(db, workspace.id, RelationDraft(
                        source_version_id=node.current_version_id, target_version_id=version.id,
                        relation_type=relation, confidence=finding.confidence,
                        created_by_attempt_id=attempt_id,
                        idempotency_key=canonical_json_hash(["projection-evidence", str(node.current_version_id),
                                                            str(version.id), relation.value]),
                    ))
    return {"plan": plan.model_dump(mode="json"), "finding_node_ids": finding_ids}


def _verify_projection_lineage(db: Any, workspace_id: uuid.UUID, evidence: ContextNode,
                               source_id: uuid.UUID) -> None:
    """Check the directed evidence -> transcript -> assigned-source lineage."""
    source = db.get(ContextNode, source_id)
    if not source or source.workspace_id != workspace_id or source.status != "active" or source.node_type != "source":
        raise RuntimeTaskError("finding_lineage_invalid", category="validation")
    targets = db.scalars(select(ContextEdge.target_version_id).where(
        ContextEdge.workspace_id == workspace_id, ContextEdge.status == "active",
        ContextEdge.source_version_id == evidence.current_version_id,
        ContextEdge.relation_type == RelationType.DERIVED_FROM.value,
    )).all()
    for target_id in targets:
        transcript = db.get(ContextNodeVersion, target_id)
        node = db.get(ContextNode, transcript.node_id) if transcript else None
        if not node or node.status != "active" or node.node_type != "transcript" or node.current_version_id != target_id:
            continue
        linked = db.scalar(select(ContextEdge.id).where(
            ContextEdge.workspace_id == workspace_id, ContextEdge.status == "active",
            ContextEdge.source_version_id == target_id,
            ContextEdge.target_version_id == source.current_version_id,
            ContextEdge.relation_type == RelationType.DERIVED_FROM.value,
        ).limit(1))
        if linked:
            return
    raise RuntimeTaskError("finding_lineage_invalid", category="validation")


def _project_knowledge(attempt_id: uuid.UUID, run: AnalysisRun, *, config: Settings) -> dict[str, Any]:
    reviews = [item["analysis"] for item in _outputs_with_prefix(run.id, "analyze_review.source_") if item.get("analysis")]
    try:
        plan = project_claims(reviews)
    except (ValueError, ValidationError) as exc:
        raise RuntimeTaskError("finding_projection_invalid", category="validation") from exc
    owners: dict[str, tuple[str, str]] = {}
    for review in reviews:
        for claim in review["claims"]:
            for ref in claim["evidence"]:
                owner = (str(review["source_id"]), str(ref["support_type"]))
                eid = str(ref["evidence_node_id"])
                if eid in owners and owners[eid] != owner:
                    raise RuntimeTaskError("finding_lineage_invalid", category="validation")
                owners[eid] = owner
    return _postprocess_knowledge(attempt_id, run, plan, config=config, evidence_owners=owners)


def _score(
    reviews: list[dict[str, Any]], audiences: list[dict[str, Any]], requested: int,
    draft: FinalReportDraft,
) -> dict[str, Any]:
    positive = sum(item["positive_pct"] - item["negative_pct"] for item in audiences)
    delta = round(positive / len(audiences)) if audiences else 0
    confidence = round(sum(item["confidence_score"] for item in audiences) / len(audiences)) if audiences else 0
    channels = {item["channel_id"] for item in reviews}
    recurrence = min(1.0, max(0.0, len(channels) / max(1, requested)))
    channel_by_source = {str(item["source_id"]): str(item["channel_id"]) for item in reviews}
    findings = (*draft.consensus_pros, *draft.consensus_cons)
    agreement = (
        sum(max(0, len({channel_by_source[str(sid)] for sid in item.source_ids if str(sid) in channel_by_source}) - 1)
            / (len(channels) - 1) for item in findings) / len(findings)
        if findings and len(channels) > 1 else 0.0
    )
    conflict = len(draft.disagreements) / max(1, len(findings) + len(draft.disagreements))
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
        central_conflict_ratio=conflict,
        has_long_term_evidence=any(item["review_type"] in {"long_term", "retrospective"} for item in reviews),
        central_claims_valid=all(any(claim["central"] for claim in item["claims"]) for item in reviews),
    )
    return preview_scoring(request).model_dump(mode="json")


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
        if isinstance(result, VideoExtraction):
            source_index = int(task.input_payload.get("source_index", 0) or 0)
            source = _task_output(run.id, f"fetch_transcript.source_{source_index}") or {}
            draft = bind_review(result.review, uuid.UUID(source["source_id"]))
            output = await _postprocess_review(attempt_id, run, draft, config=config,
                                              normalize_extended_use=isinstance(result, ClassifiedVideoExtraction))
            if isinstance(result, ClassifiedVideoExtraction) and output.get("analysis"):
                kinds = {" ".join(claim.claim.casefold().split()): claim for claim in result.review.claims}
                output["claim_catalog"] = [{"source_id": output["analysis"]["source_id"], "kind": metadata.kind,
                    "topic": metadata.topic, "claim": claim["claim"], "central": claim["central"],
                    "evidence_node_ids": [ref["evidence_node_id"] for ref in claim["evidence"]]}
                    for claim in output["analysis"]["claims"]
                    for metadata in [kinds[" ".join(claim["claim"].casefold().split())]]]
            output["product_information"] = _postprocess_product_information(
                run, source, result.product_information, config=config,
                span_payload=getattr(result, "_span_product_payload", None), spans=getattr(result, "_caption_spans", None))
            if hasattr(result, "_span_diagnostics"):
                output["caption_binding_diagnostics"] = result._span_diagnostics
            return output
        return await _postprocess_review(attempt_id, run, SourceAnalysisDraft.model_validate(result), config=config)
    if spec.key == "product_information_analyst":
        source_index = int(task.input_payload.get("source_index", 0) or 0)
        source = _task_output(run.id, f"fetch_transcript.source_{source_index}") or {}
        if not source.get("available"):
            return {"skipped": True, "reason": "source_unavailable", "source_index": source_index}
        return _postprocess_product_information(run, source, ProductExtractionDraft.model_validate(result), config=config)
    if spec.key == "source_curator":
        return await _curated_source_queues(run, SourceCuration.model_validate(result), config)
    if spec.key == "audience_analyst":
        output = _postprocess_audience(attempt_id, run, AudienceAnalysisDraft.model_validate(result), config=config)
        if hasattr(result, "_binding_diagnostics"):
            output["binding_diagnostics"] = result._binding_diagnostics
        return output
    if spec.key == "knowledge_curator":
        return _postprocess_knowledge(attempt_id, run, GraphMutationPlan.model_validate(result), config=config)
    if spec.key == "consensus_analyst":
        reviews = [item["analysis"] for item in _outputs_with_prefix(run.id, "analyze_review.source_") if item.get("analysis")]
        audiences = [item["analysis"] for item in _outputs_with_prefix(run.id, "analyze_audience.source_") if item.get("analysis")]
        if isinstance(result, CompleteBuyingSynthesis):
            retained = {review["source_id"] for review in reviews}
            audiences = [audience for audience in audiences if audience["source_id"] in retained]
        synthesis_diagnostics: list[dict[str, Any]] = []
        try:
            if isinstance(result, NormalizedBuyingSynthesis):
                report_draft, synthesis_diagnostics = result.compile_report(payload["product_display_name"],
                    payload["product_canonical_name"], reviews, payload.get("claim_catalog", []))
            elif isinstance(result, AtomicBuyingSynthesis):
                report_draft = result.as_report(payload["product_display_name"], payload["product_canonical_name"], reviews)
            elif isinstance(result, BuyingSynthesis):
                report_draft = result.as_report(payload["product_display_name"], payload["product_canonical_name"])
            else:
                report_draft = FinalReportDraft.model_validate(result)
        except ValueError as exc:
            raise RuntimeTaskError("synthesis_reference_invalid", category="validation", retryable=True,
                                   invalid_output_hash=canonical_json_hash(result.model_dump(mode="json")),
                                   validator_results={"status": "failed", "issues": [
                                       exc.issue if isinstance(exc, SynthesisBindingError) else {"type": "reference_invalid"}
                                   ]}) from exc
        if isinstance(result, EvidenceBoundBuyingSynthesis) and task.input_payload.get("correction_stage"):
            unchanged = unchanged_rejected_findings(payload.get("report_under_repair") or {}, report_draft,
                                                   payload.get("correction_issues", []))
            if unchanged:
                raise RuntimeTaskError("correction_unchanged_rejected_finding", category="quality", retryable=False,
                    validator_results={"status": "failed", "issues": [
                        {"path": path, "type": "unchanged_rejected_finding"} for path in unchanged]})
        scoring = _score(reviews, audiences, int(run.requested_options.get("source_count", 5)), report_draft)
        return {
            "draft": report_draft.model_dump(mode="json"),
            "scoring": scoring,
            "source_analyses": reviews,
            "audience_analyses": audiences,
            "synthesis_diagnostics": synthesis_diagnostics,
            "synthesis_output_hash": canonical_json_hash(result.model_dump(mode="json")),
        }
    if spec.key == "quality_auditor":
        consensus_key = "correct_consensus" if task.input_payload.get("reaudit_stage") else "build_consensus"
        consensus = _task_output(run.id, consensus_key) or {}
        reviews = consensus.get("source_analyses", [])
        with session_scope() as db:
            auditor_version = db.get(AgentVersion, task.agent_version_id)
            strict_grounding = isinstance(result, (FindingAuditResult, ReferencedAuditResult, OwnedAuditResult)) or bool(
                auditor_version and "unsupported_narrative" in auditor_version.system_prompt)
        diagnostics: dict[str, Any] = {}
        if isinstance(result, (FindingAuditResult, ReferencedAuditResult, OwnedAuditResult)):
            try:
                if isinstance(result, (ReferencedAuditResult, OwnedAuditResult)):
                    model_audit, diagnostics = result.as_audit(PartAuditorInput.model_validate(part_audit_input(payload)))
                else:
                    model_audit, diagnostics = result.as_audit(DecisionAuditorInput.model_validate(decision_audit_input(payload)))
            except ValueError as exc:
                raise RuntimeTaskError("audit_decisions_invalid", category="validation", retryable=True,
                    validator_results={"status": "failed", "issues": [
                        {"path": "finding_checks", "type": str(exc)[:180]}],
                        **(exc.diagnostics if isinstance(exc, AuditDecisionError) else {})},
                    invalid_output_hash=canonical_json_hash(result.model_dump(mode="json"))) from exc
        else:
            model_audit = AuditResult.model_validate(result)
        deterministic_rejections: list[dict[str, Any]] = []
        safe_draft, audit, grounding_terminal = ground_report(
            FinalReportDraft.model_validate(consensus.get("draft", {})),
            reviews,
            model_audit,
            strict_grounding=strict_grounding,
            diagnostics=deterministic_rejections,
            owned_guidance=isinstance(result, OwnedAuditResult),
        )
        diagnostics["deterministic_rejections"] = deterministic_rejections
        diagnostics["synthesis"] = consensus.get("synthesis_diagnostics", [])
        if audit.verdict != "fail" and any(item.get("action") == "omitted" for item in diagnostics["synthesis"]):
            audit = audit.model_copy(update={"verdict": "pass_with_warnings", "issues": (*audit.issues,
                AuditIssue(code="synthesis_finding_omitted", field_path="report_draft"))})
        scoring = _score(reviews, consensus.get("audience_analyses", []),
                         int(run.requested_options.get("source_count", 5)), safe_draft)
        return {
            "audit": audit.model_dump(mode="json"),
            "consensus_task": consensus_key,
            "safe_draft": safe_draft.model_dump(mode="json"),
            "scoring": scoring,
            "grounding_terminal": grounding_terminal,
            "audit_diagnostics": diagnostics,
        }
    return result.model_dump(mode="json")


def _postprocess_product_information(run: AnalysisRun, source: dict,
        draft: ProductExtractionDraft | None, *, config: Settings, span_payload: dict | None = None, spans: dict | None = None) -> dict:
    if draft is None:
        return {"source_id": source["source_id"], "video_id": source["video_id"], "facts": [],
                "variants": [], "sample_used": {"units": []},
                "extraction_diagnostics": {"product_information_invalid_or_missing": True}}
    metadata, transcript_body = _product_source_material(run, source, config)
    rejected: list[dict[str, str]] = []
    source_args = dict(title=str(metadata.get("title") or ""), description=str(metadata.get("description") or ""),
        transcript_body=transcript_body, video_id=source["video_id"], canonical_product=run.canonical_product,
        diagnostics=rejected)
    facts, variants, sample = (validate_span_products(span_payload, spans or {}, **source_args) if span_payload is not None
                              else validate_extraction(draft, **source_args))
    return {"source_id": source["source_id"], "video_id": source["video_id"],
        "facts": [item.model_dump(mode="json") for item in facts],
        "variants": [item.model_dump(mode="json") for item in variants],
        "sample_used": sample.model_dump(mode="json"),
        "extraction_diagnostics": {
            "proposed_facts": len(span_payload.get("facts", [])) if span_payload is not None else len(draft.facts), "accepted_facts": len(facts),
            "proposed_variants": len(span_payload.get("variants", [])) if span_payload is not None else len(draft.variants), "accepted_variants": len(variants),
            "proposed_sample_details": sum(len(unit.get("details", [])) for unit in span_payload.get("sample_units", [])) if span_payload is not None else sum(len(unit.details) for unit in draft.sample_units),
            "accepted_sample_details": sum(len(unit.details) for unit in sample.units),
            "rejected_items": rejected,
            "rejection_counts": {code: sum(item["code"] == code for item in rejected)
                                 for code in sorted({item["code"] for item in rejected})}}}


def _verify_report_citations(db, workspace: Workspace, reviews: list[dict], *, config: Settings) -> None:
    expected: dict[uuid.UUID, tuple[str, str | None]] = {}
    for review in reviews:
        expected[uuid.UUID(review['source_id'])] = ('source', None)
        for claim in review['claims']:
            for ref in claim['evidence']:
                expected[uuid.UUID(ref['evidence_node_id'])] = ('evidence', ref['evidence_text'])
    rows = db.execute(select(ContextNode, ContextNodeVersion).join(ContextNodeVersion,
        ContextNodeVersion.id == ContextNode.current_version_id).where(ContextNode.id.in_(expected))).all()
    found = {node.id: (node, version) for node, version in rows}
    for node_id, (kind, quotation) in expected.items():
        pair = found.get(node_id)
        valid = bool(pair and pair[0].workspace_id == workspace.id and pair[0].status == 'active'
                     and pair[0].node_type == kind)
        if valid:
            try:
                _, body = read_version_body(workspace, pair[1], config=config)
                valid = quotation is None or body.strip() == quotation.strip()
            except (KnowledgeGraphError, MarkdownValidationError, OSError):
                valid = False
        if not valid:
            raise RuntimeTaskError('report_evidence_unavailable', category='quality', retryable=False,
                validator_results={'status': 'failed', 'issues': [{'type': 'citation_storage_invalid',
                    'node_id': str(node_id), 'node_type': kind}]})


def _publish_report(
    attempt_id: uuid.UUID,
    run: AnalysisRun,
    snapshot: ConfigurationSnapshot,
    *,
    config: Settings,
) -> dict[str, Any]:
    audit_payload = _task_output(run.id, "reaudit_report") or {}
    if not audit_payload.get("audit"):
        raise RuntimeTaskError("report_audit_missing", category="quality")
    audit = AuditResult.model_validate(audit_payload.get("audit", {}))
    if audit.verdict == "fail":
        raise RuntimeTaskError("report_audit_failed", category="quality")
    consensus_key = audit_payload.get("consensus_task", "correct_consensus")
    consensus = _task_output(run.id, consensus_key) or _task_output(run.id, "build_consensus") or {}
    draft = FinalReportDraft.model_validate(audit_payload.get("safe_draft") or consensus.get("draft", {}))
    scoring = audit_payload.get("scoring") or consensus.get("scoring", {})
    reviews = consensus.get("source_analyses", [])
    audiences = consensus.get("audience_analyses", [])
    if not reviews or not scoring.get("publishable"):
        raise RuntimeTaskError("report_publication_gate_failed", category="quality")
    warnings = list(scoring.get("warning_codes", []))
    if audit.verdict == "pass_with_warnings":
        warnings.append("quality_audit_warning")
    requested = int(run.requested_options.get("source_count", 5))
    product_outputs = _outputs_with_prefix(run.id, "extract_product_information.source_")
    product_info = merge_product_info(product_outputs)
    has_product_tasks = _has_task_prefix(run.id, "extract_product_information.source_")
    sample_by_source = {item.get("source_id"): item.get("sample_used") for item in product_outputs}

    def sample_for(source_id: str) -> SampleUsed:
        try:
            return SampleUsed.model_validate(sample_by_source.get(source_id) or {})
        except ValueError:
            return SampleUsed()

    sample_used_by_source = {
        item["source_id"]: sample_for(item["source_id"])
        for item in reviews
    } if has_product_tasks else {}
    status = "partial" if len(reviews) < requested or warnings else "complete"
    report_id = uuid.uuid5(run.id, "validated-internal-report")
    with session_scope() as db:
        locked_run = db.get(AnalysisRun, run.id)
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id).with_for_update())
        if not locked_run or not workspace:
            raise RuntimeTaskError("analysis_workspace_missing", category="storage")
        existing = db.scalar(select(Report).where(Report.run_id == run.id))
        if existing:
            return {"report_id": str(existing.id), "status": existing.payload["status"]}
        _verify_report_citations(db, workspace, reviews, config=config)
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
            product_info=product_info,
            sample_used_by_source=sample_used_by_source,
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
    if handler == "analysis.project_product_information":
        review = _task_output(run.id, f"analyze_review.source_{int(input_payload['source_index'])}") or {}
        return review.get("product_information") or {"skipped": True, "reason": "source_unavailable"}
    if handler == "analysis.project_knowledge":
        return _project_knowledge(attempt_id, run, config=config)
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
