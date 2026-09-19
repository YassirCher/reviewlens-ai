from __future__ import annotations


import httpx

from app.config import Settings, settings
from app.tools.contracts import YouTubeTranscriptInput
from app.tools.errors import ToolExecutionError
from app.tools.youtube import TranscriptProvider, fetch_transcript


async def run_youtube_live_smoke(video_id: str, config: Settings = settings) -> dict:
    if not config.youtube_live_smoke_enabled:
        raise RuntimeError("YOUTUBE_LIVE_SMOKE_ENABLED must be true")
    if not config.youtube_api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured")
    if config.youtube_base_url.rstrip("/") != "https://www.googleapis.com/youtube/v3":
        raise RuntimeError("live smoke requires the official YouTube Data API origin")
    async with httpx.AsyncClient(
        timeout=config.youtube_request_timeout_seconds,
        follow_redirects=False,
    ) as client:
        response = await client.get(
            f"{config.youtube_base_url.rstrip('/')}/videos",
            params={
                "part": "snippet,contentDetails,status",
                "id": video_id,
                "fields": "items(id,contentDetails(caption),status(uploadStatus))",
            },
            headers={"X-Goog-Api-Key": config.youtube_api_key},
        )
    if response.is_redirect:
        raise ToolExecutionError("youtube_redirect_rejected", category="security")
    if response.status_code >= 400:
        raise ToolExecutionError("youtube_live_metadata_failed", category="upstream")
    items = response.json().get("items", [])
    if len(items) != 1 or items[0].get("id") != video_id:
        raise ToolExecutionError("youtube_live_video_missing", category="not_found")
    transcript = await fetch_transcript(
        YouTubeTranscriptInput(video_id=video_id),
        provider=TranscriptProvider(),
        config=config,
    )
    return {
        "status": "succeeded",
        "video_id": video_id,
        "metadata_count": 1,
        "caption_available": str(items[0].get("contentDetails", {}).get("caption", "false")).lower()
        == "true",
        "source_language": transcript.source_language,
        "delivered_language": transcript.delivered_language,
        "caption_kind": transcript.caption_kind,
        "translated": transcript.translated,
        "segment_count": len(transcript.segments),
        "data_api_requests": 1,
    }
