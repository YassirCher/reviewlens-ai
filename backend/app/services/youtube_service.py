import html
import re
from collections import OrderedDict

import httpx

from app.config import settings
from app.models import VideoCandidate

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
REVIEW_WORDS = {
    "review", "reviews", "test", "tested", "testing", "verdict",
    "experience", "month", "months", "week", "weeks", "day", "days",
    "after", "honest", "long term", "long-term", "comparison", "vs",
}
NEGATIVE_TITLE_WORDS = {
    "trailer", "launch event", "unboxing only", "teaser", "commercial",
    "advertisement", "ad ", "shorts", "giveaway", "give away",
}
STOP_WORDS = {"the", "a", "an", "and", "or", "with", "for", "of", "in", "to", "pro", "max"}


def parse_iso8601_duration(value: str) -> int | None:
    match = re.fullmatch(r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?", value or "")
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def normalized_tokens(text: str) -> list[str]:
    return [
        token for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 1 and token not in STOP_WORDS
    ]


def relevance_score(product_name: str, title: str, description: str = "") -> float:
    title_lower = title.lower()
    product_tokens = normalized_tokens(product_name)
    if not product_tokens:
        return 0.0

    matched = sum(1 for token in product_tokens if token in normalized_tokens(title))
    product_match = matched / len(product_tokens)
    review_bonus = 0.0
    combined = f"{title_lower} {description.lower()[:500]}"
    if any(word in combined for word in REVIEW_WORDS):
        review_bonus = 0.28
    negative = 0.38 if any(word in title_lower for word in NEGATIVE_TITLE_WORDS) else 0.0
    return max(0.0, min(1.0, 0.72 * product_match + review_bonus - negative))


class YouTubeService:
    async def find_review_candidates(self, product_name: str) -> list[VideoCandidate]:
        if not settings.youtube_api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not configured.")

        queries = [f"{product_name} review", f"{product_name} long term review"]
        found: OrderedDict[str, dict] = OrderedDict()

        async with httpx.AsyncClient(timeout=25.0) as client:
            for query_index, query in enumerate(queries):
                params = {
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "order": "viewCount",
                    "maxResults": settings.youtube_candidate_count,
                    "videoCaption": "closedCaption",
                    "regionCode": settings.youtube_region_code,
                    "relevanceLanguage": settings.youtube_relevance_language,
                    "safeSearch": "moderate",
                    "key": settings.youtube_api_key,
                }
                response = await client.get(f"{YOUTUBE_API}/search", params=params)
                self._raise_youtube(response, "search")
                for item in response.json().get("items", []):
                    video_id = item.get("id", {}).get("videoId")
                    if video_id and video_id not in found:
                        found[video_id] = item
                # A second search costs quota. Only use it if the first search is unlikely
                # to yield enough usable reviews after filtering/transcript checks.
                if query_index == 0 and len(found) >= 8:
                    break

            if not found:
                return []

            ids = list(found.keys())[:50]
            details_response = await client.get(
                f"{YOUTUBE_API}/videos",
                params={
                    "part": "snippet,statistics,contentDetails,status",
                    "id": ",".join(ids),
                    "key": settings.youtube_api_key,
                },
            )
            self._raise_youtube(details_response, "video metadata")

        candidates: list[VideoCandidate] = []
        for item in details_response.json().get("items", []):
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            content = item.get("contentDetails", {})
            status = item.get("status", {})
            title = html.unescape(snippet.get("title", ""))
            description = html.unescape(snippet.get("description", ""))
            duration = parse_iso8601_duration(content.get("duration", ""))
            score = relevance_score(product_name, title, description)

            # Reject obvious false positives and ultra-short videos.
            if score < 0.52:
                continue
            if duration is not None and duration < 180:
                continue
            if snippet.get("liveBroadcastContent") in {"live", "upcoming"}:
                continue
            if status.get("uploadStatus") and status.get("uploadStatus") != "processed":
                continue

            thumbs = snippet.get("thumbnails", {})
            thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
            video_id = item.get("id", "")
            candidates.append(
                VideoCandidate(
                    video_id=video_id,
                    title=title,
                    channel=html.unescape(snippet.get("channelTitle", "Unknown channel")),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    thumbnail_url=thumb,
                    view_count=int(statistics.get("viewCount", 0) or 0),
                    duration_seconds=duration,
                    published_at=snippet.get("publishedAt"),
                    relevance_score=round(score, 3),
                )
            )

        # Once relevance is good enough, honor the POC's core rule: highest views first.
        # Prefer a stronger relevance pool when at least three such videos exist.
        strong = [item for item in candidates if item.relevance_score >= 0.65]
        pool = strong if len(strong) >= settings.youtube_top_video_count else candidates
        pool.sort(key=lambda v: (v.view_count, v.relevance_score), reverse=True)
        return pool[: settings.youtube_candidate_count]

    @staticmethod
    def _raise_youtube(response: httpx.Response, stage: str) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            detail = response.text
        raise RuntimeError(f"YouTube {stage} failed ({response.status_code}): {detail[:500]}")
