from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisRequest(StrictModel):
    product_name: str = Field(min_length=1, max_length=500)
    video_count: int | None = Field(default=None, ge=3, le=8)
    analyze_comments: bool = False
    locale: str | None = Field(default=None, min_length=2, max_length=20)

    @field_validator("product_name")
    @classmethod
    def product_is_visible(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("a visible product name is required")
        return normalized

    @field_validator("locale")
    @classmethod
    def locale_is_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", value):
            raise ValueError("locale must be a language or language-region tag")
        return value


class NormalizedOptions(StrictModel):
    product_name: str
    video_count: int
    analyze_comments: bool
    locale: str


class QuotaRemaining(StrictModel):
    hourly_remaining: int = Field(ge=0)
    daily_ip_remaining: int = Field(ge=0)
    daily_session_remaining: int = Field(ge=0)
    concurrent_remaining: int = Field(ge=0)


class QueueCondition(StrictModel):
    condition: Literal["available", "full"]
    queued_runs: int = Field(ge=0)


class TokenBand(StrictModel):
    min: int = Field(ge=0)
    max: int = Field(ge=0)


class PreflightEstimate(StrictModel):
    token_band: TokenBand
    cost_band: Literal["low", "medium", "high"]
    non_binding: Literal[True]


class PreflightResponse(StrictModel):
    normalized_options: NormalizedOptions
    allowed: bool
    denial_code: str | None
    queue: QueueCondition
    remaining_public_quota: QuotaRemaining
    estimate: PreflightEstimate


class CreateResponse(StrictModel):
    run_id: uuid.UUID
    status: str
    status_url: str
    events_url: str


class PublicTask(StrictModel):
    task_key: str
    status: str
    label: str
    started_at: datetime | None
    completed_at: datetime | None


class StatusResponse(StrictModel):
    run_id: uuid.UUID
    status: str
    product_name: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    source_count_requested: int
    source_count_analyzed: int
    completed_tasks: int
    total_tasks: int
    warnings: tuple[str, ...]
    failure: dict[str, str] | None
    total_tokens: int = Field(ge=0)
    usage_pending: bool
    tasks: tuple[PublicTask, ...]
    report_url: str | None
    progress_sequence: int = Field(ge=0)


class PublicEvidence(StrictModel):
    id: str
    text: str = Field(max_length=500)
    timestamp_start_seconds: float | None
    timestamp_end_seconds: float | None
    support_type: Literal["supports", "contradicts"]
    confidence: int = Field(ge=0, le=100)


class PublicClaim(StrictModel):
    claim: str
    central: bool
    evidence: tuple[PublicEvidence, ...]


class PublicSource(StrictModel):
    id: str
    video_id: str
    url: str
    title: str
    channel: str
    views: int | None
    duration_seconds: int | None
    published_at: datetime | None
    review_type: str
    ownership_context: str
    usage_period: str | None
    source_score: int = Field(ge=0, le=100)
    evidence_quality_score: int = Field(ge=0, le=100)
    recommendation_summary: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    limitations: tuple[str, ...]
    transcript_language: str
    translated: bool
    caption_kind: str
    claims: tuple[PublicClaim, ...]


class PublicFinding(StrictModel):
    id: str
    statement: str
    source_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


class PublicDisagreement(StrictModel):
    topic: str
    side_a: str
    side_a_source_ids: tuple[str, ...]
    side_b: str
    side_b_source_ids: tuple[str, ...]


class PublicReportResponse(StrictModel):
    schema_version: Literal[1]
    report_id: uuid.UUID
    product_name: str
    status: Literal["complete", "partial"]
    source_count_requested: int
    source_count_analyzed: int
    overall_score: int = Field(ge=0, le=100)
    verdict: str
    confidence: int = Field(ge=0, le=100)
    confidence_band: str
    summary: str
    consensus_pros: tuple[PublicFinding, ...]
    consensus_cons: tuple[PublicFinding, ...]
    disagreements: tuple[PublicDisagreement, ...]
    longest_usage_period: str | None
    longest_usage_source_id: str | None
    who_should_buy: tuple[str, ...]
    who_should_avoid: tuple[str, ...]
    limitations: tuple[str, ...]
    warnings: tuple[str, ...]
    sources: tuple[PublicSource, ...]
    generated_at: datetime
    total_tokens: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    usage_pending: bool


class GraphNode(StrictModel):
    id: str
    type: Literal["product", "source", "finding", "evidence"]
    label: str
    video_id: str | None = None
    polarity: str | None = None


class GraphEdge(StrictModel):
    source: str
    target: str
    type: Literal["ABOUT", "SUPPORTS", "CONTRADICTS"]


class GraphResponse(StrictModel):
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    next_cursor: str | None
