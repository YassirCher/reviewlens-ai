from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

from app.config import settings
from app.models import AnalyzeResponse, VideoAnalysis
from app.services.ai_service import AIService
from app.providers.base import ProviderError
from app.services.comment_service import CommentService
from app.services.transcript_service import TranscriptService
from app.services.youtube_service import YouTubeService


class AnalysisOrchestrator:
    def __init__(self) -> None:
        self.youtube = YouTubeService()
        self.transcripts = TranscriptService()
        self.comments = CommentService()
        self.ai = AIService()

    async def run(self, product_name: str, analyze_comments: bool, provider_choice: str) -> AnalyzeResponse:
        final: AnalyzeResponse | None = None
        async for event in self.run_stream(product_name, analyze_comments, provider_choice):
            if event["type"] == "result":
                final = AnalyzeResponse.model_validate(event["data"])
        if final is None:
            raise RuntimeError("Analysis ended without a result.")
        return final

    async def run_stream(
        self,
        product_name: str,
        analyze_comments: bool,
        provider_choice: str,
    ) -> AsyncGenerator[dict, None]:
        analysis_id = str(uuid4())
        warnings: list[str] = []
        provider_name, model = self.ai.resolve_provider(provider_choice)

        yield self._progress("searching", "Searching YouTube", 8, "Finding high-view review candidates")
        candidates = await self.youtube.find_review_candidates(product_name)
        if not candidates:
            raise RuntimeError("No relevant captioned YouTube review candidates were found.")

        yield self._progress("transcripts", "Checking transcripts", 20, f"Testing {len(candidates)} ranked candidates")
        selected: list[tuple] = []
        for candidate in candidates:
            transcript = await self.transcripts.get(candidate.video_id)
            if not transcript:
                warnings.append(f"No usable transcript: {candidate.title}")
                continue
            selected.append((candidate, transcript))
            if len(selected) >= settings.youtube_top_video_count:
                break

        if not selected:
            raise RuntimeError("Review candidates were found, but none had a usable transcript.")
        if len(selected) < settings.youtube_top_video_count:
            warnings.append(f"Only {len(selected)} analyzable review video(s) were available.")

        results: list[VideoAnalysis] = []
        total = len(selected)
        for index, (candidate, transcript) in enumerate(selected, start=1):
            base_pct = 28 + round(((index - 1) / max(1, total)) * 54)
            yield self._progress(
                f"video_{index}",
                f"Analyzing review {index}/{total}",
                base_pct,
                candidate.title,
            )

            comments = []
            if analyze_comments:
                comments = await self.comments.get_top_comments(candidate.video_id)
                if not comments:
                    warnings.append(f"Comments unavailable: {candidate.title}")

            try:
                result = await self.ai.analyze_video(
                    product_name=product_name,
                    video=candidate,
                    transcript=transcript,
                    comments=comments,
                    provider_name=provider_name,
                    model=model,
                )
                results.append(result)
                yield {
                    "type": "video_result",
                    "data": {"index": index, "analysis": result.model_dump(mode="json")},
                }
            except ProviderError as exc:
                warnings.append(f"Analysis failed for '{candidate.title}': {str(exc)[:220]}")
                # Authentication/quota errors are unlikely to recover on the next
                # video. Preserve completed work instead of burning more requests.
                if exc.status_code in {401, 403, 429}:
                    break
            except Exception as exc:
                warnings.append(f"Analysis failed for '{candidate.title}': {str(exc)[:220]}")

        if not results:
            raise RuntimeError("The AI provider could not analyze any selected review.")

        yield self._progress("consensus", "Building consensus", 88, "Comparing reviewer agreement, caveats and usage evidence")
        overall = await self.ai.aggregate(
            product_name=product_name,
            analyses=results,
            selected_provider=provider_name,
            selected_model=model,
        )

        response = AnalyzeResponse(
            analysis_id=analysis_id,
            product_name=product_name,
            analyze_comments=analyze_comments,
            provider_used=provider_name,
            model_used=model,
            videos=results,
            overall=overall,
            warnings=warnings,
        )
        yield self._progress("complete", "Analysis complete", 100, "Evidence-backed verdict ready")
        yield {"type": "result", "data": response.model_dump(mode="json")}

    @staticmethod
    def _progress(stage: str, label: str, percent: int, detail: str) -> dict:
        return {
            "type": "progress",
            "data": {
                "stage": stage,
                "label": label,
                "percent": percent,
                "detail": detail,
            },
        }
