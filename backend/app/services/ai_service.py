import json
from statistics import mean

from pydantic import ValidationError

from app.config import settings
from app.models import (
    CommentItem,
    OverallAnalysis,
    VideoAnalysis,
    VideoAnalysisPayload,
    VideoCandidate,
    TranscriptSegment,
)
from app.providers.base import ProviderError
from app.providers.factory import get_provider
from app.services.prompts import OVERALL_SYSTEM_PROMPT, VIDEO_SYSTEM_PROMPT
from app.services.text_compaction import compact_comments, compact_transcript


class AIService:
    def resolve_provider(self, choice: str) -> tuple[str, str]:
        candidates: list[str] = []
        if choice != "auto":
            candidates.append(choice)
        candidates.append(settings.ai_primary_provider)
        candidates.extend(["openrouter", "xai", "openai"])

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if settings.provider_available(candidate) and settings.model_for(candidate):
                return candidate, settings.model_for(candidate)
        raise ProviderError("No AI provider is configured. Add an OpenRouter or xAI API key in .env.")

    async def analyze_video(
        self,
        *,
        product_name: str,
        video: VideoCandidate,
        transcript: list[TranscriptSegment],
        comments: list[CommentItem],
        provider_name: str,
        model: str,
    ) -> VideoAnalysis:
        transcript_text = compact_transcript(transcript)
        comments_text = compact_comments(comments)
        user_prompt = f"""PRODUCT\n{product_name}\n\nVIDEO METADATA\n{json.dumps(video.model_dump(), ensure_ascii=False)}\n\nTIMESTAMPED TRANSCRIPT\n{transcript_text}\n\n"""
        if comments_text:
            user_prompt += f"OPTIONAL TOP/RELEVANT COMMENTS\n{comments_text}\n\n"
        else:
            user_prompt += "OPTIONAL TOP/RELEVANT COMMENTS\nNot requested or unavailable. Set comments=null.\n\n"

        provider = get_provider(provider_name)
        raw = await provider.structured_completion(
            system_prompt=VIDEO_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=VideoAnalysisPayload.model_json_schema(),
            schema_name="video_review_analysis",
            model=model,
        )
        try:
            payload = VideoAnalysisPayload.model_validate(raw)
        except ValidationError as exc:
            raise ProviderError(f"AI output failed schema validation: {exc}") from exc

        # Keep the headline number reproducible instead of trusting an arbitrary
        # LLM-generated product_score. Confidence acts as evidence-quality signal.
        computed_score = round(
            0.45 * payload.purchase_recommendation_score
            + 0.35 * payload.reviewer_sentiment_score
            + 0.20 * payload.confidence_score
        )
        payload.product_score = max(0, min(100, computed_score))
        return VideoAnalysis(video=video, **payload.model_dump())

    async def aggregate(
        self,
        *,
        product_name: str,
        analyses: list[VideoAnalysis],
        selected_provider: str,
        selected_model: str,
    ) -> OverallAnalysis:
        if not analyses:
            raise ValueError("Cannot aggregate an empty analysis list.")

        agg_provider_name = settings.ai_aggregator_provider
        if agg_provider_name == "auto" or not settings.provider_available(agg_provider_name):
            agg_provider_name = selected_provider
        agg_model = settings.model_for(agg_provider_name) or selected_model

        compact_payload = []
        for analysis in analyses:
            compact_payload.append({
                "video_title": analysis.video.title,
                "channel": analysis.video.channel,
                "view_count": analysis.video.view_count,
                "review_type": analysis.review_type,
                "usage_period_raw": analysis.usage_period_raw,
                "product_score": analysis.product_score,
                "reviewer_sentiment_score": analysis.reviewer_sentiment_score,
                "purchase_recommendation_score": analysis.purchase_recommendation_score,
                "confidence_score": analysis.confidence_score,
                "purchase_verdict": analysis.purchase_verdict,
                "recommendation_summary": analysis.recommendation_summary,
                "pros": analysis.pros,
                "cons": analysis.cons,
                "major_issues": analysis.major_issues,
                "recommended_for": analysis.recommended_for,
                "not_recommended_for": analysis.not_recommended_for,
                "comments": analysis.comments.model_dump() if analysis.comments else None,
            })

        user_prompt = (
            f"PRODUCT\n{product_name}\n\nSTRUCTURED VIDEO ANALYSES\n"
            + json.dumps(compact_payload, ensure_ascii=False, indent=2)
        )

        try:
            provider = get_provider(agg_provider_name)
            raw = await provider.structured_completion(
                system_prompt=OVERALL_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=OverallAnalysis.model_json_schema(),
                schema_name="overall_product_consensus",
                model=agg_model,
            )
            result = OverallAnalysis.model_validate(raw)

            # Numeric consensus is deterministic; the AI supplies the semantic
            # explanation, consensus groups, disagreements and audience fit.
            base_score = round(mean(a.product_score for a in analyses))
            result.score = base_score
            if base_score >= 80:
                result.verdict = "buy"
            elif base_score >= 65:
                result.verdict = "buy_with_caveats"
            elif base_score >= 45:
                result.verdict = "mixed"
            else:
                result.verdict = "do_not_buy"
            return result
        except Exception:
            # Final report should remain usable if the 4th/free-tier call fails.
            return self.deterministic_fallback(analyses)

    @staticmethod
    def deterministic_fallback(analyses: list[VideoAnalysis]) -> OverallAnalysis:
        score = round(mean(a.product_score for a in analyses))
        confidence = round(mean(a.confidence_score for a in analyses))
        spread = max(a.product_score for a in analyses) - min(a.product_score for a in analyses)
        if spread >= 25:
            confidence = max(20, confidence - 15)

        if score >= 80:
            verdict = "buy"
        elif score >= 65:
            verdict = "buy_with_caveats"
        elif score >= 45:
            verdict = "mixed"
        else:
            verdict = "do_not_buy"

        pros: dict[str, int] = {}
        cons: dict[str, int] = {}
        for a in analyses:
            for item in a.pros:
                key = item.strip().lower()
                pros[key] = pros.get(key, 0) + 1
            for item in a.cons:
                key = item.strip().lower()
                cons[key] = cons.get(key, 0) + 1

        consensus_pros = [k.capitalize() for k, count in pros.items() if count >= 2][:5]
        consensus_cons = [k.capitalize() for k, count in cons.items() if count >= 2][:5]
        usage = [a.usage_period_raw for a in analyses if a.usage_period_raw]
        disagreements = []
        if spread >= 25:
            disagreements.append(f"Reviewer scores differ by {spread} points, so opinion is meaningfully mixed.")

        return OverallAnalysis(
            score=score,
            verdict=verdict,
            confidence=confidence,
            summary=f"Fallback consensus from {len(analyses)} review analysis result(s). The average product score is {score}/100.",
            consensus_pros=consensus_pros,
            consensus_cons=consensus_cons,
            disagreements=disagreements,
            longest_usage_period=usage[0] if usage else None,
            who_should_buy=list(dict.fromkeys(item for a in analyses for item in a.recommended_for))[:5],
            who_should_avoid=list(dict.fromkeys(item for a in analyses for item in a.not_recommended_for))[:5],
        )
