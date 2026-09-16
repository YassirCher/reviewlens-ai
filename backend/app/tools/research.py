from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from app.config import Settings, settings
from app.db.models import ContextNodeVersion, TaskAttempt, Workspace
from app.db.session import session_scope
from app.runtime.contracts import canonical_json_hash
from app.tools.contracts import (
    GraphCreateEdgeItem,
    GraphCreateEdgesInput,
    GraphCreateNodeItem,
    GraphCreateNodesInput,
    ResearchQueryPlan,
    ResearchResult,
    ResearchSource,
    YouTubeCommentsOutput,
    YouTubeSearchOutput,
    YouTubeTranscriptOutput,
    YouTubeVideoDetailsOutput,
)
from app.tools.errors import ToolExecutionError
from app.tools.runner import invoke_tool
from app.tools.youtube import chunk_transcript, rank_candidates


def _json_body(label: str, payload: dict) -> str:
    return f"# {label}\n\n```json\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n```\n"


def _transcript_body(transcript: YouTubeTranscriptOutput) -> str:
    lines = ["# Timestamped transcript", "", '<untrusted-data source="youtube-transcript">']
    for segment in transcript.segments:
        end = segment.start_seconds + (segment.duration_seconds or 0)
        lines.append(f"[{segment.start_seconds:.3f}-{end:.3f}] {segment.text}")
    lines.extend(["</untrusted-data>", ""])
    return "\n".join(lines)


def _chunk_body(segments) -> str:
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
        lines.append(f"- [{comment.comment_id}] likes={comment.like_count} published={published}: {comment.text}")
    lines.extend(["</untrusted-data>", ""])
    return "\n".join(lines)


def _node_identity(version_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as db:
        version = db.get(ContextNodeVersion, version_id)
        if version is None:
            raise RuntimeError("created context node version is missing")
        return version.node_id, version.id


async def execute_research(
    attempt_id: uuid.UUID,
    plan: ResearchQueryPlan,
    *,
    config: Settings = settings,
) -> ResearchResult:
    with session_scope() as db:
        attempt = db.get(TaskAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError("research attempt does not exist")
        task_run_id = attempt.task_run_id
        from app.db.models import TaskRun

        task = db.get(TaskRun, task_run_id)
        workspace = db.scalar(select(Workspace).where(Workspace.run_id == task.run_id)) if task else None
        if workspace is None:
            raise RuntimeError("research workspace does not exist")
        workspace_id = workspace.id

    candidate_pool = min(config.youtube_candidate_cap, max(20, plan.requested_source_count * 4))
    search_payload = await invoke_tool(
        attempt_id,
        "youtube.search",
        {
            "queries": list(plan.queries),
            "max_results": candidate_pool,
            "region_code": config.youtube_region_code,
            "relevance_language": plan.requested_language,
        },
        call_key="research.search",
        config=config,
    )
    search = YouTubeSearchOutput.model_validate(search_payload)
    if not search.hits:
        raise ToolExecutionError("youtube_no_candidates", category="not_found")
    details_payload = await invoke_tool(
        attempt_id,
        "youtube.video_details",
        {"video_ids": [item.video_id for item in search.hits]},
        call_key="research.video_details",
        config=config,
    )
    details = YouTubeVideoDetailsOutput.model_validate(details_payload)

    product_and_sources = [
        GraphCreateNodeItem(
            node_type="product",
            title=plan.canonical_product,
            body=_json_body("Product scope", {"canonical_product": plan.canonical_product}),
            trust_level="operational",
            tags=("product",),
            provenance={"phase": 5, "untrusted": False},
            public_visibility="admin",
        )
    ]
    for video in details.videos:
        product_and_sources.append(
            GraphCreateNodeItem(
                node_type="source",
                title=video.title,
                body=_json_body("YouTube source metadata", video.model_dump(mode="json")),
                trust_level="primary_source",
                source_uri=f"https://www.youtube.com/watch?v={video.video_id}",
                source_language=plan.requested_language,
                tags=("youtube", "candidate"),
                provenance={"video_id": video.video_id, "raw_metadata_before_ranking": True, "untrusted": True},
                public_visibility="admin",
            )
        )
    created_payload = await invoke_tool(
        attempt_id,
        "graph.create_nodes",
        GraphCreateNodesInput(nodes=tuple(product_and_sources)).model_dump(mode="json"),
        call_key="research.raw_sources",
        config=config,
    )
    created_version_ids = tuple(uuid.UUID(item) for item in created_payload["node_version_ids"])
    product_node_id, product_version_id = _node_identity(created_version_ids[0])
    source_versions: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    for video, version_id in zip(details.videos, created_version_ids[1:], strict=True):
        source_versions[video.video_id] = _node_identity(version_id)

    about_edges = GraphCreateEdgesInput(
        edges=tuple(
            GraphCreateEdgeItem(
                source_version_id=source_version_id,
                target_version_id=product_version_id,
                relation_type="ABOUT",
                idempotency_key=canonical_json_hash(
                    ["ABOUT", str(source_version_id), str(product_version_id)]
                ),
            )
            for _, source_version_id in source_versions.values()
        )
    )
    if about_edges.edges:
        await invoke_tool(
            attempt_id,
            "graph.create_edges",
            about_edges.model_dump(mode="json"),
            call_key="research.source_about_product",
            config=config,
        )

    ranked = rank_candidates(plan.canonical_product, details.videos, config=config)
    selected: list[ResearchSource] = []
    warning_codes: list[str] = []
    for video, candidate_score in ranked:
        if candidate_score.excluded_reason or len(selected) >= plan.requested_source_count:
            continue
        try:
            transcript_payload = await invoke_tool(
                attempt_id,
                "youtube.transcript",
                {
                    "video_id": video.video_id,
                    "requested_language": plan.requested_language,
                },
                call_key=f"research.transcript.{video.video_id}",
                config=config,
            )
        except ToolExecutionError as exc:
            if exc.code.startswith("transcript_"):
                continue
            raise
        transcript = YouTubeTranscriptOutput.model_validate(transcript_payload)
        chunks = chunk_transcript(transcript, config=config)
        duration = max(
            segment.start_seconds + (segment.duration_seconds or 0)
            for segment in transcript.segments
        )
        transcript_nodes = [
            GraphCreateNodeItem(
                node_type="transcript",
                title=f"Transcript — {video.title}",
                body=_transcript_body(transcript),
                trust_level="primary_source",
                source_uri=f"https://www.youtube.com/watch?v={video.video_id}",
                source_language=transcript.source_language,
                tags=("youtube", "transcript"),
                provenance={
                    "video_id": video.video_id,
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
                    title=f"Transcript chunk {index + 1} — {video.title}",
                    body=_chunk_body(chunk),
                    trust_level="primary_source",
                    source_uri=f"https://www.youtube.com/watch?v={video.video_id}",
                    source_language=transcript.source_language,
                    tags=("youtube", "transcript", "chunk"),
                    provenance={
                        "video_id": video.video_id,
                        "segment_start": chunk[0].index,
                        "segment_end": chunk[-1].index,
                        "duration_seconds": duration,
                        "untrusted": True,
                    },
                    public_visibility="admin",
                )
            )
        transcript_created = await invoke_tool(
            attempt_id,
            "graph.create_nodes",
            GraphCreateNodesInput(nodes=tuple(transcript_nodes)).model_dump(mode="json"),
            call_key=f"research.transcript_nodes.{video.video_id}",
            config=config,
        )
        transcript_version_ids = tuple(
            uuid.UUID(item) for item in transcript_created["node_version_ids"]
        )
        transcript_node_id, transcript_version_id = _node_identity(transcript_version_ids[0])
        chunk_node_ids = tuple(_node_identity(item)[0] for item in transcript_version_ids[1:])
        source_node_id, source_version_id = source_versions[video.video_id]
        lineage_edges = [
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
            lineage_edges.extend(
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
            GraphCreateEdgesInput(edges=tuple(lineage_edges)).model_dump(mode="json"),
            call_key=f"research.transcript_edges.{video.video_id}",
            config=config,
        )

        comment_set_node_id = None
        if plan.analyze_comments:
            comments_payload = await invoke_tool(
                attempt_id,
                "youtube.comments",
                {
                    "video_id": video.video_id,
                    "fetch_limit": config.comments_fetch_limit,
                    "retain_limit": config.comments_retain_limit,
                },
                call_key=f"research.comments.{video.video_id}",
                config=config,
            )
            comments = YouTubeCommentsOutput.model_validate(comments_payload)
            if comments.comments:
                comment_node_payload = await invoke_tool(
                    attempt_id,
                    "graph.create_nodes",
                    GraphCreateNodesInput(
                        nodes=(
                            GraphCreateNodeItem(
                                node_type="comment_set",
                                title=f"Audience comments — {video.title}",
                                body=_comment_body(comments),
                                trust_level="secondary_source",
                                source_uri=f"https://www.youtube.com/watch?v={video.video_id}",
                                source_language=plan.requested_language,
                                tags=("youtube", "comments", "audience"),
                                provenance={
                                    "video_id": video.video_id,
                                    "comments_sampled": comments.comments_sampled,
                                    "comments_retained": len(comments.comments),
                                    "top_level_only": True,
                                    "untrusted": True,
                                },
                                public_visibility="admin",
                            ),
                        )
                    ).model_dump(mode="json"),
                    call_key=f"research.comment_node.{video.video_id}",
                    config=config,
                )
                comment_version_id = uuid.UUID(comment_node_payload["node_version_ids"][0])
                comment_set_node_id, _ = _node_identity(comment_version_id)
                await invoke_tool(
                    attempt_id,
                    "graph.create_edges",
                    GraphCreateEdgesInput(
                        edges=(
                            GraphCreateEdgeItem(
                                source_version_id=comment_version_id,
                                target_version_id=source_version_id,
                                relation_type="DERIVED_FROM",
                                idempotency_key=canonical_json_hash(
                                    ["DERIVED_FROM", str(comment_version_id), str(source_version_id)]
                                ),
                            ),
                        )
                    ).model_dump(mode="json"),
                    call_key=f"research.comment_edge.{video.video_id}",
                    config=config,
                )
        selected.append(
            ResearchSource(
                source_node_id=source_node_id,
                source_version_id=source_version_id,
                transcript_node_id=transcript_node_id,
                transcript_version_id=transcript_version_id,
                transcript_chunk_ids=chunk_node_ids,
                comment_set_node_id=comment_set_node_id,
                video_id=video.video_id,
                rank=len(selected) + 1,
            )
        )
    if len(selected) < plan.requested_source_count:
        warning_codes.append("partial_source_coverage")
    if not selected:
        raise ToolExecutionError("no_usable_transcripts", category="not_found")
    return ResearchResult(
        workspace_id=workspace_id,
        requested_source_count=plan.requested_source_count,
        selected_sources=tuple(selected),
        candidate_scores=tuple(score for _, score in ranked),
        warning_codes=tuple(warning_codes),
    )
