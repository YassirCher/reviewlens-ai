from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Verdict(StrEnum):
    BUY = "buy"
    BUY_WITH_CAVEATS = "buy_with_caveats"
    MIXED = "mixed"
    DO_NOT_BUY = "do_not_buy"
    UNCLEAR = "unclear"


class ReviewType(StrEnum):
    FIRST_IMPRESSIONS = "first_impressions"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    COMPARISON = "comparison"
    RETROSPECTIVE = "retrospective"
    UNKNOWN = "unknown"


class OwnershipContext(StrEnum):
    PURCHASED = "purchased"
    OWNED = "owned"
    LOANED = "loaned"
    REVIEW_UNIT = "review_unit"
    SPONSORED_UNKNOWN = "sponsored_unknown"
    UNKNOWN = "unknown"


class QueryPlan(StrictModel):
    canonical_label: str = Field(min_length=2, max_length=200)
    aliases: tuple[str, ...] = Field(default=(), max_length=8)
    queries: tuple[str, ...] = Field(min_length=1, max_length=4)
    exclusion_hints: tuple[str, ...] = Field(default=(), max_length=20)
    requested_source_count: int = Field(ge=3, le=8)
    requested_language: str = Field(default="en", min_length=2, max_length=20)
    analyze_comments: bool = False

    @field_validator("queries", "aliases", "exclusion_hints")
    @classmethod
    def normalize_text_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(" ".join(item.split()).strip() for item in value))
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("query-plan strings must contain 1-200 visible characters")
        return normalized


class ResearchCoordinatorInput(StrictModel):
    product_name: str = Field(min_length=1, max_length=500)
    requested_source_count: int = Field(ge=3, le=8)
    requested_language: str = Field(default="en", min_length=2, max_length=20)
    analyze_comments: bool = False


class CandidateContext(StrictModel):
    video_id: str = Field(min_length=6, max_length=20)
    title: str = Field(min_length=1, max_length=500)
    channel_id: str = Field(min_length=1, max_length=120)
    channel_title: str = Field(default="", max_length=300)
    duration_seconds: int = Field(ge=0)
    view_count: int = Field(ge=0)
    caption_available: bool
    deterministic_score: float = Field(ge=0, le=1)
    deterministic_exclusion: str | None = None


class SourceCuratorInput(StrictModel):
    canonical_product: str = Field(min_length=1, max_length=200)
    candidates: tuple[CandidateContext, ...] = Field(min_length=1, max_length=40)


class ReviewAnalystInput(StrictModel):
    source_id: uuid.UUID
    transcript_node_id: uuid.UUID
    source_title: str = Field(min_length=1, max_length=500)
    channel_id: str = Field(min_length=1, max_length=120)
    transcript_language: str = Field(min_length=2, max_length=40)
    translated: bool
    caption_kind: Literal["manual", "automatic"]


class AudienceAnalystInput(StrictModel):
    source_id: uuid.UUID
    comment_set_node_id: uuid.UUID
    comments_sampled: int = Field(ge=0, le=30)
    comments_retained: int = Field(ge=0, le=20)


class KnowledgeCuratorInput(StrictModel):
    source_analyses: tuple[SourceAnalysis, ...] = Field(min_length=1, max_length=8)
    audience_analyses: tuple[AudienceAnalysis, ...] = Field(default=(), max_length=8)


class ConsensusAnalystInput(StrictModel):
    product_display_name: str = Field(min_length=1, max_length=200)
    product_canonical_name: str = Field(min_length=1, max_length=200)
    requested_source_count: int = Field(ge=3, le=8)
    source_analyses: tuple[SourceAnalysis, ...] = Field(min_length=1, max_length=8)
    audience_analyses: tuple[AudienceAnalysis, ...] = Field(default=(), max_length=8)
    correction_issues: tuple["AuditIssue", ...] = Field(default=(), max_length=100)


class QualityAuditorInput(StrictModel):
    report_draft: "FinalReportDraft"
    source_analyses: tuple[SourceAnalysis, ...] = Field(min_length=1, max_length=8)


class CandidateDecision(StrictModel):
    video_id: str = Field(min_length=6, max_length=20)
    classification: Literal[
        "review", "comparison", "long_term", "first_impressions", "launch", "advertisement", "irrelevant"
    ]
    eligible: bool
    product_relevance: float = Field(ge=0, le=1)
    review_intent: float = Field(ge=0, le=1)
    independence: float = Field(ge=0, le=1)
    evidence_potential: float = Field(ge=0, le=1)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=12)


class SourceCuration(StrictModel):
    decisions: tuple[CandidateDecision, ...] = Field(min_length=1, max_length=40)
    ordered_video_ids: tuple[str, ...] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_order(self) -> "SourceCuration":
        known = {item.video_id for item in self.decisions if item.eligible}
        if len(self.ordered_video_ids) != len(set(self.ordered_video_ids)):
            raise ValueError("ordered video IDs must be unique")
        if not set(self.ordered_video_ids) <= known:
            raise ValueError("ordered video IDs must reference eligible decisions")
        return self


class EvidenceDraft(StrictModel):
    source_node_id: uuid.UUID
    evidence_text: str = Field(min_length=1, max_length=500)
    timestamp_start_seconds: float | None = Field(default=None, ge=0)
    timestamp_end_seconds: float | None = Field(default=None, ge=0)
    confidence: int = Field(ge=0, le=100)
    support_type: Literal["supports", "contradicts"] = "supports"

    @model_validator(mode="after")
    def validate_timestamps(self) -> "EvidenceDraft":
        if self.timestamp_end_seconds is not None and self.timestamp_start_seconds is None:
            raise ValueError("an evidence end timestamp requires a start timestamp")
        if (
            self.timestamp_start_seconds is not None
            and self.timestamp_end_seconds is not None
            and self.timestamp_end_seconds < self.timestamp_start_seconds
        ):
            raise ValueError("evidence timestamps are out of order")
        return self


class EvidenceRef(EvidenceDraft):
    evidence_node_id: uuid.UUID


class ClaimDraft(StrictModel):
    claim: str = Field(min_length=1, max_length=500)
    central: bool = False
    evidence: tuple[EvidenceDraft, ...] = Field(min_length=1, max_length=8)


class Claim(StrictModel):
    claim: str = Field(min_length=1, max_length=500)
    central: bool = False
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=8)


class SourceAnalysisDraft(StrictModel):
    source_id: uuid.UUID
    review_type: ReviewType
    ownership_context: OwnershipContext
    usage_period_mentioned: bool = False
    usage_period_raw: str | None = Field(default=None, max_length=120)
    usage_period_days_estimate: int | None = Field(default=None, ge=0, le=36500)
    reviewer_sentiment_score: int = Field(ge=0, le=100)
    purchase_recommendation_score: int = Field(ge=0, le=100)
    evidence_quality_score: int = Field(ge=0, le=100)
    purchase_verdict: Verdict
    recommendation_summary: str = Field(min_length=1, max_length=1000)
    pros: tuple[str, ...] = Field(default=(), max_length=20)
    cons: tuple[str, ...] = Field(default=(), max_length=20)
    major_issues: tuple[str, ...] = Field(default=(), max_length=20)
    recommended_for: tuple[str, ...] = Field(default=(), max_length=20)
    not_recommended_for: tuple[str, ...] = Field(default=(), max_length=20)
    claims: tuple[ClaimDraft, ...] = Field(min_length=1, max_length=30)
    limitations: tuple[str, ...] = Field(default=(), max_length=20)


class SourceAnalysis(StrictModel):
    source_id: uuid.UUID
    source_analysis_node_id: uuid.UUID
    channel_id: str = Field(min_length=1, max_length=120)
    review_type: ReviewType
    ownership_context: OwnershipContext
    usage_period_mentioned: bool
    usage_period_raw: str | None = None
    usage_period_days_estimate: int | None = None
    reviewer_sentiment_score: int = Field(ge=0, le=100)
    purchase_recommendation_score: int = Field(ge=0, le=100)
    evidence_quality_score: int = Field(ge=0, le=100)
    source_score: int = Field(ge=0, le=100)
    purchase_verdict: Verdict
    recommendation_summary: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    major_issues: tuple[str, ...]
    recommended_for: tuple[str, ...]
    not_recommended_for: tuple[str, ...]
    claims: tuple[Claim, ...]
    limitations: tuple[str, ...]
    transcript_language: str
    translated: bool
    caption_kind: Literal["manual", "automatic"]


class AudienceAnalysisDraft(StrictModel):
    source_id: uuid.UUID
    comments_sampled: int = Field(ge=0, le=30)
    comments_retained: int = Field(ge=0, le=20)
    sampling_limitations: tuple[str, ...] = Field(min_length=1, max_length=10)
    positive_pct: int = Field(ge=0, le=100)
    neutral_pct: int = Field(ge=0, le=100)
    negative_pct: int = Field(ge=0, le=100)
    recurring_pros: tuple[str, ...] = Field(default=(), max_length=15)
    recurring_cons: tuple[str, ...] = Field(default=(), max_length=15)
    repeated_issues: tuple[str, ...] = Field(default=(), max_length=15)
    audience_agrees_with_reviewer: bool | None = None
    confidence_score: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_percentages(self) -> "AudienceAnalysisDraft":
        if self.positive_pct + self.neutral_pct + self.negative_pct != 100:
            raise ValueError("audience percentages must sum to 100")
        return self


class AudienceAnalysis(AudienceAnalysisDraft):
    audience_signal_node_id: uuid.UUID


class FindingDraft(StrictModel):
    statement: str = Field(min_length=1, max_length=500)
    source_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=8)
    evidence_node_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=30)
    confidence: int = Field(ge=0, le=100)
    relation: Literal["consensus", "disagreement", "scope"]


class GraphMutationPlan(StrictModel):
    findings: tuple[FindingDraft, ...] = Field(min_length=1, max_length=50)


class ConsensusItem(StrictModel):
    statement: str = Field(min_length=1, max_length=500)
    source_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=8)
    evidence_node_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=30)


class Disagreement(StrictModel):
    topic: str = Field(min_length=1, max_length=300)
    side_a: str = Field(min_length=1, max_length=500)
    side_a_source_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=8)
    side_b: str = Field(min_length=1, max_length=500)
    side_b_source_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=8)


class FinalReportDraft(StrictModel):
    product_display_name: str = Field(min_length=1, max_length=200)
    product_canonical_name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=3000)
    consensus_pros: tuple[ConsensusItem, ...] = Field(default=(), max_length=20)
    consensus_cons: tuple[ConsensusItem, ...] = Field(default=(), max_length=20)
    disagreements: tuple[Disagreement, ...] = Field(default=(), max_length=20)
    longest_usage_period: str | None = Field(default=None, max_length=200)
    longest_usage_source_id: uuid.UUID | None = None
    who_should_buy: tuple[str, ...] = Field(default=(), max_length=20)
    who_should_avoid: tuple[str, ...] = Field(default=(), max_length=20)
    limitations: tuple[str, ...] = Field(default=(), max_length=30)


class AuditIssue(StrictModel):
    code: str = Field(min_length=1, max_length=120)
    field_path: str = Field(min_length=1, max_length=300)
    evidence_node_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=30)
    retryable: bool = False


class AuditResult(StrictModel):
    verdict: Literal["pass", "pass_with_warnings", "fail"]
    issues: tuple[AuditIssue, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_issues(self) -> "AuditResult":
        if self.verdict == "pass" and self.issues:
            raise ValueError("a passing audit cannot contain issues")
        if self.verdict == "fail" and not self.issues:
            raise ValueError("a failed audit requires at least one issue")
        return self


class FinalReport(StrictModel):
    schema_version: Literal[1] = 1
    report_id: uuid.UUID
    run_id: uuid.UUID
    workspace_id: uuid.UUID
    product_display_name: str
    product_canonical_name: str
    status: Literal["complete", "partial"]
    source_count_requested: int = Field(ge=3, le=8)
    source_count_analyzed: int = Field(ge=1, le=8)
    base_score: int = Field(ge=0, le=100)
    audience_adjustment: int = Field(ge=-5, le=5)
    overall_score: int = Field(ge=0, le=100)
    verdict: Verdict
    confidence: int = Field(ge=0, le=100)
    confidence_band: Literal["low", "medium", "high", "very_high"]
    summary: str
    consensus_pros: tuple[ConsensusItem, ...]
    consensus_cons: tuple[ConsensusItem, ...]
    disagreements: tuple[Disagreement, ...]
    longest_usage_period: str | None = None
    longest_usage_source_id: uuid.UUID | None = None
    who_should_buy: tuple[str, ...]
    who_should_avoid: tuple[str, ...]
    limitations: tuple[str, ...]
    warnings: tuple[str, ...]
    source_analyses: tuple[SourceAnalysis, ...]
    audience_analyses: tuple[AudienceAnalysis, ...] = ()
    total_tokens: int = Field(ge=0)
    configuration_snapshot_id: uuid.UUID
    generated_at: datetime


ConsensusAnalystInput.model_rebuild()
QualityAuditorInput.model_rebuild()
