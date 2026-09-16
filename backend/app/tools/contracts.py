from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolExecutionContext(StrictModel):
    run_id: uuid.UUID
    task_run_id: uuid.UUID
    task_attempt_id: uuid.UUID
    tool_version_id: uuid.UUID
    agent_version_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    deadline_at: datetime
    comments_enabled: bool = False
    idempotency_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-f0-9]+$")


class YouTubeSearchInput(StrictModel):
    queries: tuple[str, ...] = Field(min_length=1, max_length=4)
    max_results: int = Field(default=20, ge=1, le=40)
    region_code: str = Field(default="US", min_length=2, max_length=2)
    relevance_language: str = Field(default="en", min_length=2, max_length=20)

    @field_validator("queries")
    @classmethod
    def normalize_queries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(" ".join(item.split()).strip() for item in value))
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("search queries must contain 1-200 visible characters")
        return normalized


class YouTubeSearchHit(StrictModel):
    video_id: str
    title: str = Field(max_length=500)
    channel_id: str | None = Field(default=None, max_length=120)
    channel_title: str = Field(default="", max_length=300)
    published_at: datetime | None = None


class YouTubeSearchOutput(StrictModel):
    hits: tuple[YouTubeSearchHit, ...]
    queries_executed: int = Field(ge=0, le=4)


class YouTubeVideoDetailsInput(StrictModel):
    video_ids: tuple[str, ...] = Field(min_length=1, max_length=50)

    @field_validator("video_ids")
    @classmethod
    def validate_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique = tuple(dict.fromkeys(value))
        if any(not VIDEO_ID_PATTERN.fullmatch(item) for item in unique):
            raise ValueError("invalid YouTube video ID")
        return unique


class YouTubeVideoMetadata(StrictModel):
    video_id: str
    title: str = Field(max_length=500)
    description: str = Field(default="", max_length=10000)
    channel_id: str | None = Field(default=None, max_length=120)
    channel_title: str = Field(default="", max_length=300)
    published_at: datetime | None = None
    view_count: int = Field(default=0, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    caption_available: bool = False
    live_broadcast_content: str = Field(default="none", max_length=40)
    upload_status: str = Field(default="processed", max_length=40)
    embeddable: bool | None = None
    privacy_status: str | None = Field(default=None, max_length=40)
    made_for_kids: bool | None = None
    thumbnail_url: str | None = Field(default=None, max_length=2000)


class YouTubeVideoDetailsOutput(StrictModel):
    videos: tuple[YouTubeVideoMetadata, ...]
    missing_video_ids: tuple[str, ...] = ()


class TranscriptSegment(StrictModel):
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=20000)
    start_seconds: float = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)


class YouTubeTranscriptInput(StrictModel):
    video_id: str
    requested_language: str = Field(default="en", min_length=2, max_length=20)
    fallback_languages: tuple[str, ...] = Field(default=("en", "en-US", "en-GB", "fr", "es", "ar"), max_length=12)

    @field_validator("video_id")
    @classmethod
    def validate_video_id(cls, value: str) -> str:
        if not VIDEO_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid YouTube video ID")
        return value


class YouTubeTranscriptOutput(StrictModel):
    video_id: str
    source_language: str
    delivered_language: str
    caption_kind: Literal["manual", "automatic"]
    translated: bool
    segments: tuple[TranscriptSegment, ...]


class YouTubeCommentsInput(StrictModel):
    video_id: str
    fetch_limit: int = Field(default=30, ge=1, le=30)
    retain_limit: int = Field(default=20, ge=1, le=20)

    @field_validator("video_id")
    @classmethod
    def validate_video_id(cls, value: str) -> str:
        if not VIDEO_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid YouTube video ID")
        return value

    @model_validator(mode="after")
    def validate_limits(self) -> "YouTubeCommentsInput":
        if self.retain_limit > self.fetch_limit:
            raise ValueError("retain_limit cannot exceed fetch_limit")
        return self


class YouTubeComment(StrictModel):
    comment_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=10000)
    like_count: int = Field(default=0, ge=0)
    published_at: datetime | None = None


class YouTubeCommentsOutput(StrictModel):
    video_id: str
    comments_sampled: int = Field(ge=0)
    comments: tuple[YouTubeComment, ...]
    unavailable: bool = False


class GraphGetNodesInput(StrictModel):
    node_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=50)


class GraphNodeOutput(StrictModel):
    node_id: uuid.UUID
    node_version_id: uuid.UUID
    node_type: str
    title: str
    body_hash: str
    trust_level: str
    source_uri: str | None = None
    source_language: str | None = None
    content: str


class GraphGetNodesOutput(StrictModel):
    nodes: tuple[GraphNodeOutput, ...]


class GraphQueryRelationsInput(StrictModel):
    seed_node_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=50)
    relation_types: tuple[str, ...] = Field(default=(), max_length=12)
    direction: Literal["outgoing", "incoming", "both"] = "both"
    maximum_hops: int = Field(default=1, ge=0, le=2)
    limit: int = Field(default=100, ge=1, le=500)


class GraphRelationOutput(StrictModel):
    edge_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    relation_type: str
    confidence: int = Field(ge=0, le=100)


class GraphQueryRelationsOutput(StrictModel):
    relations: tuple[GraphRelationOutput, ...]


class GraphCreateNodeItem(StrictModel):
    node_type: str
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=2_000_000)
    trust_level: str
    source_uri: str | None = Field(default=None, max_length=2000)
    source_language: str | None = Field(default=None, max_length=40)
    confidence: int = Field(default=100, ge=0, le=100)
    tags: tuple[str, ...] = Field(default=(), max_length=50)
    provenance: dict[str, Any] = Field(default_factory=dict)
    public_visibility: Literal["public", "admin", "private"] = "admin"


class GraphCreateNodesInput(StrictModel):
    nodes: tuple[GraphCreateNodeItem, ...] = Field(min_length=1, max_length=100)


class GraphCreateNodesOutput(StrictModel):
    node_version_ids: tuple[uuid.UUID, ...]


class GraphCreateEdgeItem(StrictModel):
    source_version_id: uuid.UUID
    target_version_id: uuid.UUID
    relation_type: str
    confidence: int = Field(default=100, ge=0, le=100)
    properties: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-f0-9]+$")


class GraphCreateEdgesInput(StrictModel):
    edges: tuple[GraphCreateEdgeItem, ...] = Field(min_length=1, max_length=200)


class GraphCreateEdgesOutput(StrictModel):
    edge_ids: tuple[uuid.UUID, ...]


class VectorSearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=20000)
    allowed_node_types: tuple[str, ...] = Field(min_length=1, max_length=20)
    top_k: int = Field(default=12, ge=1, le=100)
    minimum_similarity: float = Field(default=0.25, ge=-1, le=1)


class VectorSearchHit(StrictModel):
    node_version_id: uuid.UUID
    score: float


class VectorSearchOutput(StrictModel):
    hits: tuple[VectorSearchHit, ...]
    degraded: bool = False


class VectorRequestUpsertInput(StrictModel):
    node_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=500)


class VectorRequestUpsertOutput(StrictModel):
    queued: int = Field(ge=0)


class EvidenceValidateInput(StrictModel):
    evidence_node_id: uuid.UUID
    source_node_id: uuid.UUID
    evidence_text: str = Field(min_length=1, max_length=500)
    timestamp_start_seconds: float | None = Field(default=None, ge=0)
    timestamp_end_seconds: float | None = Field(default=None, ge=0)
    central_claim: bool = False

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "EvidenceValidateInput":
        if self.timestamp_end_seconds is not None and self.timestamp_start_seconds is None:
            raise ValueError("timestamp_start_seconds is required when an end timestamp is present")
        if (
            self.timestamp_start_seconds is not None
            and self.timestamp_end_seconds is not None
            and self.timestamp_end_seconds < self.timestamp_start_seconds
        ):
            raise ValueError("timestamp_end_seconds cannot precede the start")
        return self


class EvidenceValidateOutput(StrictModel):
    valid: bool
    error_codes: tuple[str, ...] = ()
    lineage_node_ids: tuple[uuid.UUID, ...] = ()


class ScoreSourceInput(StrictModel):
    source_id: uuid.UUID
    channel_id: str = Field(min_length=1, max_length=120)
    reviewer_sentiment_score: int = Field(ge=0, le=100)
    purchase_recommendation_score: int = Field(ge=0, le=100)
    evidence_quality_score: int = Field(ge=0, le=100)
    review_type: Literal["first_impressions", "short_term", "long_term", "comparison", "retrospective", "unknown"]
    translated: bool = False


class ScoringPreviewInput(StrictModel):
    sources: tuple[ScoreSourceInput, ...] = Field(min_length=1, max_length=8)
    requested_source_count: int = Field(ge=3, le=8)
    audience_sentiment_delta: int = Field(default=0, ge=-100, le=100)
    audience_confidence: int = Field(default=0, ge=0, le=100)
    independent_recurrence: float = Field(default=0, ge=0, le=1)
    agreement_ratio: float = Field(default=0, ge=0, le=1)
    central_conflict_ratio: float = Field(default=0, ge=0, le=1)
    has_long_term_evidence: bool = False
    central_claims_valid: bool = True


class SourceScoreOutput(StrictModel):
    source_id: uuid.UUID
    score: int = Field(ge=0, le=100)
    reliability_weight: float = Field(ge=0.5, le=1)


class ScoringPreviewOutput(StrictModel):
    sources: tuple[SourceScoreOutput, ...]
    base_score: int = Field(ge=0, le=100)
    audience_adjustment: int = Field(ge=-5, le=5)
    overall_score: int = Field(ge=0, le=100)
    verdict: Literal["buy", "buy_with_caveats", "mixed", "do_not_buy", "unclear"]
    confidence: int = Field(ge=0, le=100)
    confidence_band: Literal["low", "medium", "high", "very_high"]
    publishable: bool
    warning_codes: tuple[str, ...] = ()


class ResearchQueryPlan(StrictModel):
    canonical_product: str = Field(min_length=2, max_length=200)
    queries: tuple[str, ...] = Field(min_length=1, max_length=4)
    exclusion_hints: tuple[str, ...] = Field(default=(), max_length=20)
    requested_source_count: int = Field(default=5, ge=3, le=8)
    requested_language: str = Field(default="en", min_length=2, max_length=20)
    analyze_comments: bool = False


class CandidateScore(StrictModel):
    video_id: str
    total: float = Field(ge=0, le=1)
    product_relevance: float = Field(ge=0, le=1)
    review_intent: float = Field(ge=0, le=1)
    caption_availability: float = Field(ge=0, le=1)
    independence: float = Field(ge=0, le=1)
    long_term_comparison: float = Field(ge=0, le=1)
    view_signal: float = Field(ge=0, le=1)
    excluded_reason: str | None = None


class ResearchSource(StrictModel):
    source_node_id: uuid.UUID
    source_version_id: uuid.UUID
    transcript_node_id: uuid.UUID
    transcript_version_id: uuid.UUID
    transcript_chunk_ids: tuple[uuid.UUID, ...]
    comment_set_node_id: uuid.UUID | None = None
    video_id: str
    rank: int = Field(ge=1)


class ResearchResult(StrictModel):
    workspace_id: uuid.UUID
    requested_source_count: int
    selected_sources: tuple[ResearchSource, ...]
    candidate_scores: tuple[CandidateScore, ...]
    warning_codes: tuple[str, ...] = ()


class ToolRisk(StrEnum):
    PURE = "pure"
    READ_ONLY = "read_only"
    NETWORK_READ = "network_read"
    GRAPH_WRITE = "graph_write"
