from __future__ import annotations

from app.tools.contracts import (
    ScoringPreviewInput,
    ScoringPreviewOutput,
    SourceScoreOutput,
)


def _verdict(score: int) -> str:
    if score >= 80:
        return "buy"
    if score >= 65:
        return "buy_with_caveats"
    if score >= 45:
        return "mixed"
    return "do_not_buy"


def _band(confidence: int) -> str:
    if confidence >= 85:
        return "very_high"
    if confidence >= 70:
        return "high"
    if confidence >= 45:
        return "medium"
    return "low"


def preview_scoring(request: ScoringPreviewInput) -> ScoringPreviewOutput:
    scored: list[SourceScoreOutput] = []
    weighted_score = 0.0
    weighted_quality = 0.0
    total_weight = 0.0
    for source in request.sources:
        source_score = round(
            0.60 * source.purchase_recommendation_score
            + 0.40 * source.reviewer_sentiment_score
        )
        weight = 0.50 + 0.50 * source.evidence_quality_score / 100
        scored.append(
            SourceScoreOutput(
                source_id=source.source_id,
                score=source_score,
                reliability_weight=round(weight, 4),
            )
        )
        weighted_score += source_score * weight
        weighted_quality += source.evidence_quality_score * weight
        total_weight += weight

    base_score = round(weighted_score / total_weight)
    audience_adjustment = round(
        5
        * (request.audience_sentiment_delta / 100)
        * (request.audience_confidence / 100)
        * request.independent_recurrence
    )
    audience_adjustment = max(-5, min(5, audience_adjustment))
    overall_score = max(0, min(100, base_score + audience_adjustment))

    confidence = weighted_quality / total_weight
    confidence += 10 * request.agreement_ratio
    missing_ratio = max(0, request.requested_source_count - len(request.sources)) / request.requested_source_count
    confidence -= 15 * missing_ratio
    confidence -= 20 * request.central_conflict_ratio
    if request.has_long_term_evidence:
        confidence += 5
    translated_ratio = sum(source.translated for source in request.sources) / len(request.sources)
    confidence -= 10 * translated_ratio
    unique_channels = len({source.channel_id for source in request.sources})
    duplicate_ratio = 1 - (unique_channels / len(request.sources))
    confidence -= 10 * duplicate_ratio
    confidence_value = max(0, min(100, round(confidence)))

    warnings: list[str] = []
    if len(request.sources) < request.requested_source_count:
        warnings.append("partial_source_coverage")
    if len(request.sources) == 1:
        confidence_value = min(confidence_value, 45)
        warnings.append("single_source_no_consensus")
    elif len(request.sources) == 2:
        confidence_value = min(confidence_value, 70)
    if all(source.review_type == "first_impressions" for source in request.sources):
        confidence_value = min(confidence_value, 70)
        warnings.append("first_impressions_only")
    if not request.central_claims_valid:
        warnings.append("central_evidence_invalid")

    publishable = request.central_claims_valid
    verdict = _verdict(overall_score) if publishable else "unclear"
    return ScoringPreviewOutput(
        sources=tuple(scored),
        base_score=base_score,
        audience_adjustment=audience_adjustment,
        overall_score=overall_score,
        verdict=verdict,
        confidence=confidence_value,
        confidence_band=_band(confidence_value),
        publishable=publishable,
        warning_codes=tuple(warnings),
    )
