"""Compact, source-bound extraction contract for the combined per-video call."""
from __future__ import annotations

import uuid
from functools import cache
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from app.analysis.contracts import OwnershipContext, ReviewType, SourceAnalysisDraft, StrictModel, Verdict
from app.analysis.product_info import ProductExtractionDraft


class ReviewQuote(StrictModel):
    evidence_text: str = Field(min_length=3, max_length=300)
    timestamp_start_seconds: float = Field(ge=0)
    timestamp_end_seconds: float | None = Field(default=None, ge=0)
    confidence: int = Field(ge=0, le=100)
    support_type: Literal["supports", "contradicts"] = "supports"

    @model_validator(mode="after")
    def ordered_times(self) -> "ReviewQuote":
        if self.timestamp_end_seconds is not None and self.timestamp_end_seconds < self.timestamp_start_seconds:
            raise ValueError("evidence timestamps are out of order")
        return self


class ReviewClaim(StrictModel):
    claim: str = Field(min_length=1, max_length=240)
    central: bool = False
    evidence: tuple[ReviewQuote, ...] = Field(min_length=1, max_length=2)


BriefText = Annotated[str, Field(min_length=1, max_length=160)]


class CompactReview(StrictModel):
    review_type: ReviewType
    ownership_context: OwnershipContext
    usage_period_mentioned: bool = False
    usage_period_raw: str | None = Field(default=None, max_length=120)
    usage_period_days_estimate: int | None = Field(default=None, ge=0, le=36500)
    reviewer_sentiment_score: int = Field(ge=0, le=100)
    purchase_recommendation_score: int = Field(ge=0, le=100)
    evidence_quality_score: int = Field(ge=0, le=100)
    purchase_verdict: Verdict
    recommendation_summary: str = Field(min_length=1, max_length=300)
    pros: tuple[BriefText, ...] = Field(default=(), max_length=3)
    cons: tuple[BriefText, ...] = Field(default=(), max_length=3)
    major_issues: tuple[BriefText, ...] = Field(default=(), max_length=3)
    recommended_for: tuple[BriefText, ...] = Field(default=(), max_length=3)
    not_recommended_for: tuple[BriefText, ...] = Field(default=(), max_length=3)
    claims: tuple[ReviewClaim, ...] = Field(min_length=1, max_length=6)
    limitations: tuple[BriefText, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def distinct_claims(self) -> "CompactReview":
        seen = set()
        claims = []
        for claim in self.claims:
            key = " ".join(claim.claim.casefold().split())
            if key not in seen:
                seen.add(key)
                claims.append(claim)
        return self.model_copy(update={"claims": tuple(claims)})


class VideoExtraction(StrictModel):
    review: CompactReview
    product_information: ProductExtractionDraft | None = None


@cache
def video_extraction_schema() -> dict:
    """Reuse the immutable compiled contract during concurrent per-video calls."""
    return VideoExtraction.model_json_schema()


def parse_video_extraction(payload: dict) -> VideoExtraction:
    """Product details are optional even when a provider violates that subsection's schema."""
    if not isinstance(payload, dict) or set(payload) - {"review", "product_information"}:
        return VideoExtraction.model_validate(payload)
    review = CompactReview.model_validate(payload.get("review"))
    try:
        details = ProductExtractionDraft.model_validate(payload.get("product_information"))
    except ValidationError:
        details = None
    return VideoExtraction(review=review, product_information=details)


def bind_review(review: CompactReview, source_id: uuid.UUID) -> SourceAnalysisDraft:
    payload = review.model_dump(mode="json")
    payload["source_id"] = str(source_id)
    for claim in payload["claims"]:
        for quote in claim["evidence"]:
            quote["source_node_id"] = str(source_id)
    return SourceAnalysisDraft.model_validate(payload)
