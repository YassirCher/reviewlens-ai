import asyncio

from youtube_transcript_api import YouTubeTranscriptApi

from app.models import TranscriptSegment

PREFERRED_LANGUAGES = ["en", "en-US", "en-GB", "fr", "es", "ar"]


class TranscriptService:
    def __init__(self) -> None:
        self.client = YouTubeTranscriptApi()

    async def get(self, video_id: str) -> list[TranscriptSegment]:
        return await asyncio.to_thread(self._get_sync, video_id)

    def _get_sync(self, video_id: str) -> list[TranscriptSegment]:
        try:
            transcript = self.client.fetch(video_id, languages=PREFERRED_LANGUAGES)
        except Exception:
            # If preferred languages are unavailable, try the first available transcript.
            try:
                transcript_list = self.client.list(video_id)
                transcript_obj = next(iter(transcript_list), None)
                if transcript_obj is None:
                    return []
                # Translate to English when supported; otherwise analyze original text.
                if transcript_obj.language_code not in PREFERRED_LANGUAGES and transcript_obj.is_translatable:
                    transcript_obj = transcript_obj.translate("en")
                transcript = transcript_obj.fetch()
            except Exception:
                return []

        segments: list[TranscriptSegment] = []
        for snippet in transcript:
            text = " ".join(str(snippet.text).split()).strip()
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    text=text,
                    start_seconds=float(snippet.start),
                    duration_seconds=float(snippet.duration) if snippet.duration is not None else None,
                )
            )
        return segments
