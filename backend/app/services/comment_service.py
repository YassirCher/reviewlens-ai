import html

import httpx

from app.config import settings
from app.models import CommentItem
from app.services.youtube_service import YOUTUBE_API


class CommentService:
    async def get_top_comments(self, video_id: str) -> list[CommentItem]:
        if not settings.youtube_api_key:
            return []

        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": settings.comments_per_video,
            "order": "relevance",
            "textFormat": "plainText",
            "key": settings.youtube_api_key,
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(f"{YOUTUBE_API}/commentThreads", params=params)

        # Comments can simply be disabled on a video. Do not fail the whole analysis.
        if response.status_code >= 400:
            return []

        comments: list[CommentItem] = []
        seen: set[str] = set()
        for item in response.json().get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            text = html.unescape(" ".join(snippet.get("textDisplay", "").split())).strip()
            if len(text) < 4 or text in seen:
                continue
            seen.add(text)
            comments.append(CommentItem(text=text, like_count=int(snippet.get("likeCount", 0) or 0)))
        return comments[: settings.comments_per_video]
