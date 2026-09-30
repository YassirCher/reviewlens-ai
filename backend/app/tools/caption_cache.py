"""Private server cache of public YouTube captions, never of another run's analyses."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select

from app.cache import RedisConfigurationError, get_redis
from app.config import Settings, settings
from app.db.models import AnalysisRun, ContextNode, ContextNodeVersion, TaskAttempt, TaskRun, ToolInvocation, Workspace
from app.db.session import session_scope
from app.knowledge.service import KnowledgeGraphError, read_version_body
from app.knowledge.storage import canonical_json_hash
from app.tools.contracts import TranscriptSegment, YouTubeTranscriptInput, YouTubeTranscriptOutput

CAPTION_SELECTION_VERSION = "tracks-v1"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_SEGMENT = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]\s*(.+)$")


class CachedCaption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    transcript: YouTubeTranscriptOutput
    fetched_at: datetime
    content_hash: str


def caption_origin(config: Settings) -> str:
    return canonical_json_hash(config.youtube_base_url.rstrip("/"))


def cache_key(request: YouTubeTranscriptInput, config: Settings) -> str:
    return "reviewlens:captions:" + canonical_json_hash([
        CAPTION_SELECTION_VERSION, caption_origin(config), request.model_dump(mode="json"),
    ])


def _remaining(entry: CachedCaption, now: datetime) -> int:
    if entry.fetched_at.tzinfo is None or entry.fetched_at > now + timedelta(seconds=5):
        return 0
    return max(0, int((entry.fetched_at + timedelta(seconds=CACHE_TTL_SECONDS) - now).total_seconds()))


def get_cached_caption(request: YouTubeTranscriptInput, *, config: Settings = settings, client=None,
                       now: datetime | None = None) -> CachedCaption | None:
    try:
        raw = (client or get_redis()).get(cache_key(request, config))
        if not raw or len(raw) > config.youtube_tool_max_output_bytes * 2:
            return None
        entry = CachedCaption.model_validate_json(raw)
        if (not _remaining(entry, now or datetime.now(timezone.utc))
                or entry.transcript.video_id != request.video_id or not entry.transcript.segments
                or entry.content_hash != canonical_json_hash(entry.transcript.model_dump(mode="json"))):
            return None
        return entry
    except (RedisError, RedisConfigurationError, ValidationError, ValueError, TypeError):
        return None


def store_caption(request: YouTubeTranscriptInput, transcript: YouTubeTranscriptOutput, *,
                  fetched_at: datetime | None = None, config: Settings = settings, client=None,
                  now: datetime | None = None) -> CachedCaption:
    current = now or datetime.now(timezone.utc)
    entry = CachedCaption(transcript=transcript, fetched_at=fetched_at or current,
                          content_hash=canonical_json_hash(transcript.model_dump(mode="json")))
    ttl = _remaining(entry, current)
    if transcript.video_id == request.video_id and transcript.segments and ttl:
        try:
            (client or get_redis()).setex(cache_key(request, config), ttl, entry.model_dump_json())
        except (RedisError, RedisConfigurationError):
            pass
    return entry


def caption_from_node(body: str, provenance: dict, video_id: str) -> YouTubeTranscriptOutput:
    segments: list[TranscriptSegment] = []
    for line in body.splitlines():
        if match := _SEGMENT.match(line):
            start, end = float(match[1]), float(match[2])
            if end < start:
                raise ValueError("invalid caption times")
            segments.append(TranscriptSegment(index=len(segments), text=match[3],
                                               start_seconds=start, duration_seconds=end - start))
    if not segments or provenance.get("video_id") != video_id:
        raise ValueError("caption identity or content missing")
    return YouTubeTranscriptOutput(video_id=video_id, source_language=provenance["source_language"],
        delivered_language=provenance["delivered_language"], caption_kind=provenance["caption_kind"],
        translated=provenance["translated"], segments=tuple(segments))


def bootstrap_caption(request: YouTubeTranscriptInput, *, config: Settings = settings) -> CachedCaption | None:
    """Only reuse hash-verified captions acquired by a successful public-source tool call.

    No old graph IDs, private notes, reviewer output, or owner data enter the new run.
    A missing origin is accepted only for legacy records from the real YouTube endpoint.
    """
    current = datetime.now(timezone.utc)
    with session_scope() as db:
        rows = db.execute(select(ContextNodeVersion, Workspace, AnalysisRun.requested_options)
            .join(ContextNode, ContextNode.current_version_id == ContextNodeVersion.id)
            .join(Workspace, Workspace.id == ContextNode.workspace_id)
            .join(TaskAttempt, TaskAttempt.id == ContextNodeVersion.created_by_attempt_id)
            .join(TaskRun, TaskRun.id == TaskAttempt.task_run_id)
            .join(AnalysisRun, AnalysisRun.id == TaskRun.run_id)
            .where(ContextNode.node_type == "transcript", ContextNode.status == "active",
                Workspace.status.in_(("active", "degraded")),
                ContextNodeVersion.source_uri == f"https://www.youtube.com/watch?v={request.video_id}",
                ContextNodeVersion.created_at >= current - timedelta(seconds=CACHE_TTL_SECONDS))
            .order_by(ContextNodeVersion.created_at.desc()).limit(8)).all()
        for version, workspace, options in rows:
            provenance = version.provenance
            origin = provenance.get("caption_origin")
            if (origin != caption_origin(config) and not (origin is None and
                    config.youtube_base_url.rstrip("/") == "https://www.googleapis.com/youtube/v3")):
                continue
            if options.get("language", "en") != request.requested_language:
                continue
            if request.fallback_languages != YouTubeTranscriptInput.model_fields["fallback_languages"].default:
                continue  # Legacy nodes did not persist customized fallback policies.
            expected_inputs = [canonical_json_hash({"video_id": request.video_id,
                                                   "requested_language": request.requested_language}),
                               canonical_json_hash(request.model_dump(mode="json"))]
            acquired = db.scalar(select(ToolInvocation.id).where(
                ToolInvocation.task_attempt_id == version.created_by_attempt_id,
                ToolInvocation.tool_key == "youtube.transcript", ToolInvocation.status == "succeeded",
                ToolInvocation.input_hash.in_(expected_inputs)).limit(1))
            if not acquired:
                continue
            try:
                fetched_at = datetime.fromisoformat(provenance["caption_fetched_at"]) if provenance.get(
                    "caption_fetched_at") else version.created_at
                transcript = caption_from_node(read_version_body(workspace, version, config=config)[1],
                                               provenance, request.video_id)
                entry = store_caption(request, transcript, fetched_at=fetched_at, config=config)
                if _remaining(entry, current):
                    return entry
            except (KnowledgeGraphError, ValidationError, ValueError, KeyError, TypeError, OSError):
                continue
    return None


def available_caption(request: YouTubeTranscriptInput, *, config: Settings = settings) -> CachedCaption | None:
    return get_cached_caption(request, config=config) or bootstrap_caption(request, config=config)
