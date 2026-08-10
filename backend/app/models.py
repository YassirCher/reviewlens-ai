from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["buy", "buy_with_caveats", "mixed", "do_not_buy", "unclear"]
ProviderChoice = Literal["auto", "openrouter", "xai", "openai"]


class AnalyzeRequest(BaseModel):
    product_name: str = Field(min_length=2, max_length=200)
    analyze_comments: bool = False
    provider: ProviderChoice = "auto"


class ProviderInfo(BaseModel):
    id: str
    label: str
    available: bool
    model: str
    free_friendly: bool = False


class ConfigResponse(BaseModel):
    providers: list[ProviderInfo]
    default_provider: str
    comments_default: bool = False
    max_videos: int = 3


class TranscriptSegment(BaseModel):
    text: str
    start_seconds: float
    duration_seconds: float | None = None


class CommentItem(BaseModel):
    text: str
    like_count: int = 0


class VideoCandidate(BaseModel):
    video_id: str
    title: str
    channel: str
    url: str
    thumbnail_url: str | None = None
    view_count: int = 0
    duration_seconds: int | None = None
    published_at: str | None = None
    relevance_score: float = 0.0


class EvidenceItem(BaseModel):
    claim: str
    evidence_text: str
    timestamp_seconds: float | None = None
    confidence: int = Field(ge=0, le=100)


class CommentAnalysis(BaseModel):
    comments_analyzed: int = 0
    positive_pct: int = Field(default=0, ge=0, le=100)
    neutral_pct: int = Field(default=0, ge=0, le=100)
    negative_pct: int = Field(default=0, ge=0, le=100)
    recurring_pros: list[str] = Field(default_factory=list)
    recurring_cons: list[str] = Field(default_factory=list)
    repeated_issues: list[str] = Field(default_factory=list)
    audience_agrees_with_reviewer: bool | None = None
    confidence_score: int = Field(default=0, ge=0, le=100)


class VideoAnalysisPayload(BaseModel):
    review_type: Literal[
        "first_impressions",
        "short_term",
        "long_term",
        "comparison",
        "unknown",
    ] = "unknown"
    usage_period_mentioned: bool = False
    usage_period_raw: str | None = None
    usage_period_days_estimate: int | None = None
    ownership_context: Literal["owned", "loaned", "review_unit", "unknown"] = "unknown"

    product_score: int = Field(ge=0, le=100)
    reviewer_sentiment_score: int = Field(ge=0, le=100)
    purchase_recommendation_score: int = Field(ge=0, le=100)
    confidence_score: int = Field(ge=0, le=100)

    purchase_verdict: Verdict
    recommendation_summary: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    major_issues: list[str] = Field(default_factory=list)
    recommended_for: list[str] = Field(default_factory=list)
    not_recommended_for: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    comments: CommentAnalysis | None = None


class VideoAnalysis(VideoAnalysisPayload):
    video: VideoCandidate


class OverallAnalysis(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    confidence: int = Field(ge=0, le=100)
    summary: str
    consensus_pros: list[str] = Field(default_factory=list)
    consensus_cons: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    longest_usage_period: str | None = None
    who_should_buy: list[str] = Field(default_factory=list)
    who_should_avoid: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    analysis_id: str
    product_name: str
    analyze_comments: bool
    provider_used: str
    model_used: str
    videos: list[VideoAnalysis]
    overall: OverallAnalysis | None = None
    warnings: list[str] = Field(default_factory=list)
