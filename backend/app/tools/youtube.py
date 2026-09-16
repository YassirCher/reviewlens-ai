from __future__ import annotations

import asyncio
import html
import math
import re
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from youtube_transcript_api import YouTubeTranscriptApi

from app.config import Settings, settings
from app.db.models import YouTubeQuotaReservation, YouTubeQuotaState
from app.db.session import session_scope
from app.tools.contracts import (
    CandidateScore,
    TranscriptSegment,
    YouTubeComment,
    YouTubeCommentsInput,
    YouTubeCommentsOutput,
    YouTubeSearchHit,
    YouTubeSearchInput,
    YouTubeSearchOutput,
    YouTubeTranscriptInput,
    YouTubeTranscriptOutput,
    YouTubeVideoDetailsInput,
    YouTubeVideoDetailsOutput,
    YouTubeVideoMetadata,
)
from app.tools.errors import ToolExecutionError

PACIFIC = ZoneInfo("America/Los_Angeles")
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
REVIEW_TERMS = ("review", "test", "tested", "verdict", "experience", "after")
LONG_TERM_TERMS = ("long term", "long-term", "month", "months", "year", "years", "comparison", " vs ")
PROMOTIONAL_TERMS = ("official launch", "trailer", "commercial", "advertisement", "promo", "teaser")
STOP_WORDS = {"the", "a", "an", "and", "or", "with", "for", "of", "in", "to"}


def youtube_quota_date(moment: datetime | None = None):
    value = moment or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(PACIFIC).date()


def _bucket_limit(bucket: str, config: Settings) -> int:
    return (
        config.youtube_search_daily_call_limit
        if bucket == "search"
        else config.youtube_data_daily_unit_limit
    )


def reserve_quota(
    invocation_id: uuid.UUID,
    *,
    bucket: str,
    units: int,
    network_attempt: int,
    config: Settings = settings,
) -> uuid.UUID:
    quota_date = youtube_quota_date()
    limit = _bucket_limit(bucket, config)
    with session_scope() as db:
        db.execute(
            insert(YouTubeQuotaState)
            .values(bucket=bucket, quota_date=quota_date, limit_units=limit)
            .on_conflict_do_nothing(index_elements=["bucket", "quota_date"])
        )
        state = db.scalar(
            select(YouTubeQuotaState)
            .where(YouTubeQuotaState.bucket == bucket, YouTubeQuotaState.quota_date == quota_date)
            .with_for_update()
        )
        assert state is not None
        if state.limit_units != limit:
            if state.reserved_units or state.consumed_units:
                raise ToolExecutionError("youtube_quota_limit_changed", category="configuration")
            state.limit_units = limit
        if state.reserved_units + state.consumed_units + units > state.limit_units:
            raise ToolExecutionError(
                "youtube_quota_exhausted",
                category="quota",
                safe_metadata={"bucket": bucket, "quota_date": quota_date.isoformat()},
            )
        reservation = YouTubeQuotaReservation(
            id=uuid.uuid4(),
            tool_invocation_id=invocation_id,
            network_attempt=network_attempt,
            bucket=bucket,
            quota_date=quota_date,
            units=units,
            status="reserved",
        )
        state.reserved_units += units
        db.add(reservation)
        db.flush()
        return reservation.id


def finalize_quota(reservation_id: uuid.UUID, *, consumed: bool) -> None:
    with session_scope() as db:
        reservation = db.scalar(
            select(YouTubeQuotaReservation)
            .where(YouTubeQuotaReservation.id == reservation_id)
            .with_for_update()
        )
        if reservation is None or reservation.status != "reserved":
            return
        state = db.scalar(
            select(YouTubeQuotaState)
            .where(
                YouTubeQuotaState.bucket == reservation.bucket,
                YouTubeQuotaState.quota_date == reservation.quota_date,
            )
            .with_for_update()
        )
        assert state is not None
        state.reserved_units -= reservation.units
        if consumed:
            state.consumed_units += reservation.units
            reservation.status = "consumed"
        else:
            reservation.status = "released"
        reservation.finalized_at = datetime.now(timezone.utc)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


def _error_reason(response: httpx.Response) -> str | None:
    try:
        errors = response.json().get("error", {}).get("errors", [])
        if errors and isinstance(errors[0], dict):
            reason = errors[0].get("reason")
            return str(reason)[:120] if reason else None
    except Exception:
        return None
    return None


class YouTubeDataClient:
    def __init__(
        self,
        invocation_id: uuid.UUID,
        *,
        config: Settings = settings,
        client: httpx.AsyncClient | None = None,
        deadline_at: datetime | None = None,
        max_attempts: int | None = None,
    ) -> None:
        if not config.youtube_api_key:
            raise ToolExecutionError("youtube_not_configured", category="configuration")
        if (
            config.app_env.lower() != "test"
            and config.youtube_base_url.rstrip("/") != "https://www.googleapis.com/youtube/v3"
        ):
            raise ToolExecutionError("youtube_origin_not_allowed", category="configuration")
        self.invocation_id = invocation_id
        self.config = config
        self.deadline_at = deadline_at
        self.max_attempts = min(
            config.youtube_network_max_attempts,
            max_attempts or config.youtube_network_max_attempts,
        )
        self._network_attempt = 0
        timeout = httpx.Timeout(config.youtube_request_timeout_seconds)
        self.client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self.retry_count = 0

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _request(self, path: str, params: dict, *, bucket: str) -> httpx.Response:
        last_error: ToolExecutionError | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._network_attempt += 1
            reservation_id = reserve_quota(
                self.invocation_id,
                bucket=bucket,
                units=1,
                network_attempt=self._network_attempt,
                config=self.config,
            )
            try:
                response = await self.client.get(
                    f"{self.config.youtube_base_url.rstrip('/')}/{path}",
                    params=params,
                    headers={"X-Goog-Api-Key": self.config.youtube_api_key},
                )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                finalize_quota(reservation_id, consumed=False)
                last_error = ToolExecutionError(
                    "youtube_network_failure", category="timeout", retryable=True
                )
                retryable = True
                delay = min(
                    self.config.youtube_retry_max_seconds,
                    self.config.youtube_retry_base_seconds * (2 ** (attempt - 1)),
                )
            except (httpx.TimeoutException, httpx.NetworkError):
                finalize_quota(reservation_id, consumed=True)
                last_error = ToolExecutionError(
                    "youtube_network_failure", category="timeout", retryable=True
                )
                retryable = True
                delay = min(
                    self.config.youtube_retry_max_seconds,
                    self.config.youtube_retry_base_seconds * (2 ** (attempt - 1)),
                )
            else:
                finalize_quota(reservation_id, consumed=True)
                if response.is_redirect:
                    raise ToolExecutionError("youtube_redirect_rejected", category="security")
                if response.status_code < 400:
                    self.retry_count = attempt - 1
                    return response
                reason = _error_reason(response)
                if response.status_code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded"}:
                    raise ToolExecutionError("youtube_upstream_quota_exhausted", category="quota")
                if response.status_code in {401, 403}:
                    raise ToolExecutionError("youtube_authentication_failed", category="authentication")
                if response.status_code == 404:
                    raise ToolExecutionError("youtube_resource_not_found", category="not_found")
                retryable = response.status_code == 429 or response.status_code >= 500
                code = "youtube_rate_limited" if response.status_code == 429 else "youtube_upstream_failure"
                last_error = ToolExecutionError(code, category="transient", retryable=retryable)
                delay = _retry_after_seconds(response)
                if delay is None:
                    delay = min(
                        self.config.youtube_retry_max_seconds,
                        self.config.youtube_retry_base_seconds * (2 ** (attempt - 1)),
                    )
            if not retryable or attempt >= self.max_attempts:
                assert last_error is not None
                self.retry_count = attempt - 1
                raise last_error
            if self.deadline_at:
                now = datetime.now(timezone.utc)
                deadline = self.deadline_at
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                if now.timestamp() + delay >= deadline.timestamp():
                    raise ToolExecutionError("youtube_retry_deadline_exceeded", category="timeout")
            await asyncio.sleep(delay)
        raise ToolExecutionError("youtube_upstream_failure", category="transient")

    async def search(self, request: YouTubeSearchInput) -> YouTubeSearchOutput:
        hits: dict[str, YouTubeSearchHit] = {}
        queries_executed = 0
        for query in request.queries:
            queries_executed += 1
            response = await self._request(
                "search",
                {
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "order": "relevance",
                    "maxResults": request.max_results,
                    "regionCode": request.region_code,
                    "relevanceLanguage": request.relevance_language,
                    "safeSearch": "moderate",
                    "fields": "items(id/videoId,snippet(title,channelId,channelTitle,publishedAt))",
                },
                bucket="search",
            )
            for item in response.json().get("items", []):
                video_id = str(item.get("id", {}).get("videoId", ""))
                if not video_id or video_id in hits:
                    continue
                snippet = item.get("snippet", {})
                hits[video_id] = YouTubeSearchHit(
                    video_id=video_id,
                    title=html.unescape(str(snippet.get("title", "")))[:500],
                    channel_id=snippet.get("channelId"),
                    channel_title=html.unescape(str(snippet.get("channelTitle", "")))[:300],
                    published_at=snippet.get("publishedAt"),
                )
                if len(hits) >= request.max_results:
                    break
            if len(hits) >= request.max_results:
                break
        return YouTubeSearchOutput(hits=tuple(hits.values()), queries_executed=queries_executed)

    async def video_details(self, request: YouTubeVideoDetailsInput) -> YouTubeVideoDetailsOutput:
        response = await self._request(
            "videos",
            {
                "part": "snippet,statistics,contentDetails,status",
                "id": ",".join(request.video_ids),
                "fields": "items(id,snippet(title,description,channelId,channelTitle,publishedAt,liveBroadcastContent,thumbnails),statistics(viewCount),contentDetails(duration,caption),status(uploadStatus,embeddable,privacyStatus,madeForKids))",
            },
            bucket="data_api",
        )
        videos: list[YouTubeVideoMetadata] = []
        found: set[str] = set()
        for item in response.json().get("items", []):
            video_id = str(item.get("id", ""))
            if not video_id:
                continue
            found.add(video_id)
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            details = item.get("contentDetails", {})
            status = item.get("status", {})
            thumbs = snippet.get("thumbnails", {})
            thumbnail = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
            videos.append(
                YouTubeVideoMetadata(
                    video_id=video_id,
                    title=html.unescape(str(snippet.get("title", "")))[:500],
                    description=html.unescape(str(snippet.get("description", "")))[:10000],
                    channel_id=snippet.get("channelId"),
                    channel_title=html.unescape(str(snippet.get("channelTitle", "")))[:300],
                    published_at=snippet.get("publishedAt"),
                    view_count=int(statistics.get("viewCount", 0) or 0),
                    duration_seconds=parse_iso8601_duration(str(details.get("duration", ""))),
                    caption_available=str(details.get("caption", "false")).lower() == "true",
                    live_broadcast_content=str(snippet.get("liveBroadcastContent", "none")),
                    upload_status=str(status.get("uploadStatus", "processed")),
                    embeddable=status.get("embeddable"),
                    privacy_status=status.get("privacyStatus"),
                    made_for_kids=status.get("madeForKids"),
                    thumbnail_url=thumbnail,
                )
            )
        return YouTubeVideoDetailsOutput(
            videos=tuple(videos),
            missing_video_ids=tuple(item for item in request.video_ids if item not in found),
        )

    async def comments(self, request: YouTubeCommentsInput) -> YouTubeCommentsOutput:
        try:
            response = await self._request(
                "commentThreads",
                {
                    "part": "snippet",
                    "videoId": request.video_id,
                    "maxResults": request.fetch_limit,
                    "order": "relevance",
                    "textFormat": "plainText",
                    "fields": "items(snippet/topLevelComment(id,snippet(textDisplay,likeCount,publishedAt)))",
                },
                bucket="data_api",
            )
        except ToolExecutionError as exc:
            if exc.code == "youtube_resource_not_found":
                return YouTubeCommentsOutput(
                    video_id=request.video_id, comments_sampled=0, comments=(), unavailable=True
                )
            raise
        raw = response.json().get("items", [])
        retained: list[YouTubeComment] = []
        seen: set[str] = set()
        for item in raw:
            comment = item.get("snippet", {}).get("topLevelComment", {})
            snippet = comment.get("snippet", {})
            text = normalize_source_text(html.unescape(str(snippet.get("textDisplay", ""))))
            fingerprint = re.sub(r"\W+", "", text.casefold())
            if not _comment_is_usable(text) or not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            retained.append(
                YouTubeComment(
                    comment_id=str(comment.get("id", "missing")),
                    text=text[:10000],
                    like_count=int(snippet.get("likeCount", 0) or 0),
                    published_at=snippet.get("publishedAt"),
                )
            )
            if len(retained) >= request.retain_limit:
                break
        return YouTubeCommentsOutput(
            video_id=request.video_id,
            comments_sampled=len(raw),
            comments=tuple(retained),
        )


def parse_iso8601_duration(value: str) -> int | None:
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value or "",
    )
    if not match:
        return None
    return (
        int(match.group("days") or 0) * 86400
        + int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )


def normalize_source_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        item for item in TOKEN_PATTERN.findall(value.casefold()) if item not in STOP_WORDS and len(item) > 1
    )


def product_relevance(product: str, title: str, description: str = "") -> float:
    product_tokens = set(_tokens(product))
    if not product_tokens:
        return 0
    title_tokens = set(_tokens(title))
    description_tokens = set(_tokens(description[:1000]))
    title_match = len(product_tokens & title_tokens) / len(product_tokens)
    description_match = len(product_tokens & description_tokens) / len(product_tokens)
    return min(1.0, 0.85 * title_match + 0.15 * description_match)


def title_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(_tokens(left)), set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def score_candidate(product: str, video: YouTubeVideoMetadata, config: Settings = settings) -> CandidateScore:
    combined = f" {video.title.casefold()} {video.description[:500].casefold()} "
    relevance = product_relevance(product, video.title, video.description)
    intent = 1.0 if any(term in combined for term in REVIEW_TERMS) else 0.0
    long_term = 1.0 if any(term in combined for term in LONG_TERM_TERMS) else 0.0
    promotional = any(term in combined for term in PROMOTIONAL_TERMS)
    independence = 0.0 if promotional or "official" in video.channel_title.casefold() else 1.0
    view_signal = min(1.0, math.log10(video.view_count + 1) / 7)
    excluded: str | None = None
    if relevance < config.youtube_min_relevance_score:
        excluded = "irrelevant_product"
    elif video.duration_seconds is not None and video.duration_seconds < config.youtube_min_review_duration_seconds:
        excluded = "below_review_duration"
    elif video.live_broadcast_content in {"live", "upcoming"}:
        excluded = "live_or_upcoming"
    elif video.upload_status != "processed" or video.privacy_status in {"private", "unlisted"}:
        excluded = "unavailable"
    total = (
        0.40 * relevance
        + 0.20 * intent
        + 0.15 * float(video.caption_available)
        + 0.10 * independence
        + 0.10 * long_term
        + 0.05 * view_signal
    )
    return CandidateScore(
        video_id=video.video_id,
        total=round(total, 6),
        product_relevance=round(relevance, 6),
        review_intent=intent,
        caption_availability=float(video.caption_available),
        independence=independence,
        long_term_comparison=long_term,
        view_signal=round(view_signal, 6),
        excluded_reason=excluded,
    )


def rank_candidates(
    product: str,
    videos: Iterable[YouTubeVideoMetadata],
    *,
    config: Settings = settings,
) -> tuple[tuple[YouTubeVideoMetadata, CandidateScore], ...]:
    deduped: list[tuple[YouTubeVideoMetadata, CandidateScore]] = []
    seen_ids: set[str] = set()
    for video in videos:
        if video.video_id in seen_ids:
            continue
        seen_ids.add(video.video_id)
        score = score_candidate(product, video, config)
        if score.excluded_reason:
            deduped.append((video, score))
            continue
        duplicate = any(
            item.channel_id == video.channel_id and title_similarity(item.title, video.title) >= 0.90
            for item, prior_score in deduped
            if not prior_score.excluded_reason
        )
        if duplicate:
            score = score.model_copy(update={"excluded_reason": "near_duplicate"})
        deduped.append((video, score))

    eligible = [(video, score) for video, score in deduped if not score.excluded_reason]
    excluded = [(video, score) for video, score in deduped if score.excluded_reason]
    ranked: list[tuple[YouTubeVideoMetadata, CandidateScore]] = []
    channel_counts: dict[str, int] = {}
    while eligible:
        eligible.sort(
            key=lambda item: (
                item[1].total - 0.25 * channel_counts.get(item[0].channel_id or item[0].channel_title, 0),
                item[0].published_at or datetime.min.replace(tzinfo=timezone.utc),
                item[0].video_id,
            ),
            reverse=True,
        )
        selected = eligible.pop(0)
        ranked.append(selected)
        channel = selected[0].channel_id or selected[0].channel_title
        channel_counts[channel] = channel_counts.get(channel, 0) + 1
    excluded.sort(key=lambda item: (item[1].excluded_reason or "", item[0].video_id))
    return tuple(ranked + excluded)


def _comment_is_usable(text: str) -> bool:
    if len(text) < 4 or len(text) > 10000:
        return False
    if len(re.findall(r"https?://", text.casefold())) > 2:
        return False
    if re.search(r"(.)\1{14,}", text.casefold()):
        return False
    visible = sum(character.isalnum() for character in text)
    return visible >= max(3, int(len(text) * 0.15))


class TranscriptProvider:
    def __init__(self, client: YouTubeTranscriptApi | None = None) -> None:
        self.client = client or YouTubeTranscriptApi()

    def fetch(self, request: YouTubeTranscriptInput) -> YouTubeTranscriptOutput:
        try:
            tracks = list(self.client.list(request.video_id))
        except Exception as exc:
            raise ToolExecutionError("transcript_unavailable", category="upstream") from exc
        if not tracks:
            raise ToolExecutionError("transcript_unavailable", category="not_found")
        exact_manual = [t for t in tracks if t.language_code == request.requested_language and not t.is_generated]
        exact_auto = [t for t in tracks if t.language_code == request.requested_language and t.is_generated]
        language_order = {code: index for index, code in enumerate(request.fallback_languages)}
        other_manual = sorted(
            [t for t in tracks if t.language_code != request.requested_language and not t.is_generated],
            key=lambda t: (language_order.get(t.language_code, 999), t.language_code),
        )
        other_auto = sorted(
            [t for t in tracks if t.language_code != request.requested_language and t.is_generated],
            key=lambda t: (language_order.get(t.language_code, 999), t.language_code),
        )
        track = (exact_manual + exact_auto + other_manual + other_auto)[0]
        source_language = track.language_code
        caption_kind = "automatic" if track.is_generated else "manual"
        delivered = track
        translated = False
        if source_language != request.requested_language and track.is_translatable:
            try:
                delivered = track.translate(request.requested_language)
                translated = True
            except Exception:
                delivered = track
        try:
            fetched = delivered.fetch()
        except Exception as exc:
            raise ToolExecutionError("transcript_fetch_failed", category="upstream") from exc
        segments = tuple(
            TranscriptSegment(
                index=index,
                text=text,
                start_seconds=float(item.start),
                duration_seconds=float(item.duration) if item.duration is not None else None,
            )
            for index, item in enumerate(fetched)
            if (text := normalize_source_text(str(item.text)))
        )
        if not segments:
            raise ToolExecutionError("transcript_empty", category="not_found")
        return YouTubeTranscriptOutput(
            video_id=request.video_id,
            source_language=source_language,
            delivered_language=getattr(delivered, "language_code", source_language),
            caption_kind=caption_kind,
            translated=translated,
            segments=segments,
        )


async def fetch_transcript(
    request: YouTubeTranscriptInput,
    *,
    provider: TranscriptProvider | None = None,
    config: Settings = settings,
) -> YouTubeTranscriptOutput:
    if (
        provider is None
        and config.app_env.lower() == "test"
        and config.youtube_base_url.rstrip("/") != "https://www.googleapis.com/youtube/v3"
    ):
        try:
            async with httpx.AsyncClient(
                timeout=config.youtube_transcript_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{config.youtube_base_url.rstrip('/')}/transcripts",
                    params={
                        "videoId": request.video_id,
                        "language": request.requested_language,
                    },
                )
            if response.status_code == 404:
                raise ToolExecutionError("transcript_unavailable", category="not_found")
            if response.status_code >= 400:
                raise ToolExecutionError("transcript_fetch_failed", category="upstream")
            return YouTubeTranscriptOutput.model_validate(response.json())
        except ToolExecutionError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolExecutionError(
                "transcript_fetch_failed", category="upstream", retryable=True
            ) from exc
    try:
        return await asyncio.wait_for(
            asyncio.to_thread((provider or TranscriptProvider()).fetch, request),
            timeout=config.youtube_transcript_timeout_seconds,
        )
    except TimeoutError as exc:
        raise ToolExecutionError("transcript_timeout", category="timeout", retryable=True) from exc


def chunk_transcript(
    transcript: YouTubeTranscriptOutput,
    *,
    config: Settings = settings,
) -> tuple[tuple[TranscriptSegment, ...], ...]:
    chunks: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    current_size = 0
    target = config.youtube_transcript_chunk_target_characters
    maximum = config.youtube_transcript_chunk_max_characters
    for segment in transcript.segments:
        segment_size = len(segment.text) + 32
        if current and (current_size >= target or current_size + segment_size > maximum):
            chunks.append(current)
            current = []
            current_size = 0
        current.append(segment)
        current_size += segment_size
    if current:
        chunks.append(current)
    return tuple(tuple(chunk) for chunk in chunks)
