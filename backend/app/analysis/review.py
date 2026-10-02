"""Compact, source-bound extraction contract for the combined per-video call."""
from __future__ import annotations

import uuid
import re
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


class ClassifiedClaim(ReviewClaim):
    kind: Literal["strength", "caveat", "context"]
    topic: str = Field(min_length=1, max_length=40)


class ClassifiedReview(CompactReview):
    claims: tuple[ClassifiedClaim, ...] = Field(min_length=1, max_length=6)


class ClassifiedVideoExtraction(VideoExtraction):
    review: ClassifiedReview


_SCHEMA_CACHE: dict[type[VideoExtraction], dict] = {}


def video_extraction_schema(model: type[VideoExtraction] = VideoExtraction) -> dict:
    """Reuse the immutable compiled contract during concurrent per-video calls."""
    if model not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[model] = model.model_json_schema()
    return _SCHEMA_CACHE[model]


def parse_video_extraction(payload: dict, model: type[VideoExtraction] = VideoExtraction) -> VideoExtraction:
    """Product details are optional even when a provider violates that subsection's schema."""
    if not isinstance(payload, dict) or set(payload) - {"review", "product_information"}:
        return model.model_validate(payload)
    review_model = model.model_fields["review"].annotation
    assert review_model is not None
    review = review_model.model_validate(payload.get("review"))
    try:
        details = ProductExtractionDraft.model_validate(payload.get("product_information"))
    except ValidationError:
        details = None
    return model(review=review, product_information=details)


def bind_review(review: CompactReview, source_id: uuid.UUID) -> SourceAnalysisDraft:
    payload = review.model_dump(mode="json")
    payload["source_id"] = str(source_id)
    for claim in payload["claims"]:
        claim.pop("kind", None)
        claim.pop("topic", None)
        for quote in claim["evidence"]:
            quote["source_node_id"] = str(source_id)
    return SourceAnalysisDraft.model_validate(payload)


def normalize_usage(review: SourceAnalysisDraft, transcript: str) -> tuple[SourceAnalysisDraft, dict]:
    """An explicit ownership/use phrase must support extended-use classification."""
    raw = review.usage_period_raw or ""
    spoken = re.sub(r"^\[[^\]]+\]\s*", "", transcript, flags=re.M)
    words = lambda value: " ".join(re.findall(r"\w+", value.casefold()))
    text, phrase = words(spoken), words(raw)
    start = text.find(phrase) if phrase else -1
    nearby = text[max(0, start - 100):start + len(phrase)] if start >= 0 else ""
    supported = bool(review.usage_period_mentioned and start >= 0 and re.search(
        r"\b(?:using|used|owned|ownership|testing|tested|reviewing|living|spent|use)\b", nearby))
    if re.search(r"\b(?:son|daughter|child|brother|sister)\b.{0,25}\b(?:old|age)\b|\b(?:battery|runtime|playback|charging)\b.{0,30}\b(?:lasts?|hours?)\b", phrase):
        supported = False
    # Estimate alone is never proof of an ownership duration.
    duration = re.search(r"\b(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|full|past)\s+(days?|weeks?|months?|years?)\b", phrase)
    days = None
    if supported and duration:
        named = dict(zip(("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"), range(1, 13)))
        amount = int(duration[1]) if duration[1].isdigit() else named.get(duration[1], 1)
        days = amount * {"d": 1, "w": 7, "m": 30, "y": 365}[duration[2][0]]
    updates = {"usage_period_mentioned": supported, "usage_period_raw": raw if supported else None,
               "usage_period_days_estimate": days}
    if review.review_type in {ReviewType.LONG_TERM, ReviewType.RETROSPECTIVE} and (days is None or days < 30):
        updates["review_type"] = ReviewType.SHORT_TERM if days is not None else ReviewType.UNKNOWN
    return review.model_copy(update=updates), {"usage_supported": supported, "extended_use_supported": days is not None and days >= 30}
