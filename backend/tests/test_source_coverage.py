from __future__ import annotations

import json
import asyncio
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.analysis.contracts import SourceAnalysisDraft
from app.analysis.registry import AGENT_REGISTRY, LEGACY_REVIEW_SPEC, snapshot_output_model
from app.analysis.review import VideoExtraction, bind_review, parse_video_extraction
from app.config import Settings
from app.llmops.gateway import validated_chat_content
from app.tools.caption_cache import (CACHE_TTL_SECONDS, cache_key, caption_from_node,
    get_cached_caption, store_caption)
from app.tools.contracts import YouTubeTranscriptInput, YouTubeTranscriptOutput
from app.tools.evidence import transcript_excerpt_matches
from app.tools.youtube import source_slot_queues
from app.tools import runner
from app.tools import caption_cache
from app.tools.errors import ToolExecutionError
from app.tools.registry import TOOL_REGISTRY


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl


def caption():
    return YouTubeTranscriptOutput.model_validate({"video_id": "fixture01", "source_language": "es",
        "delivered_language": "en", "caption_kind": "automatic", "translated": True,
        "segments": [{"index": 0, "text": "Battery lasted thirty hours under light use",
                      "start_seconds": 15, "duration_seconds": 5}]})


def compact_payload():
    return {"review": {"review_type": "long_term", "ownership_context": "owned",
        "usage_period_mentioned": True, "usage_period_raw": "six months", "usage_period_days_estimate": 180,
        "reviewer_sentiment_score": 75, "purchase_recommendation_score": 75, "evidence_quality_score": 90,
        "purchase_verdict": "buy_with_caveats", "recommendation_summary": "Battery endurance under light use.",
        "claims": [{"claim": "Battery lasted thirty hours under light use", "central": True, "evidence": [{
            "evidence_text": "Battery lasted thirty hours under light use", "timestamp_start_seconds": 15,
            "timestamp_end_seconds": 20, "confidence": 90}]}]},
        "product_information": {"facts": [], "variants": [], "sample_units": []}}


def test_caption_cache_preserves_original_age_and_language():
    config, cache = Settings(_env_file=None), MemoryCache()
    request = YouTubeTranscriptInput(video_id="fixture01")
    now = datetime.now(timezone.utc)
    fetched = now - timedelta(days=2)
    stored = store_caption(request, caption(), fetched_at=fetched, config=config, client=cache, now=now)
    recovered = get_cached_caption(request, config=config, client=cache, now=now)
    assert recovered == stored and recovered.fetched_at == fetched
    assert recovered.transcript.translated and recovered.transcript.source_language == "es"
    assert cache.ttls[cache_key(request, config)] == 5 * 86400
    assert not get_cached_caption(request, config=config, client=cache, now=fetched + timedelta(seconds=CACHE_TTL_SECONDS))
    assert not get_cached_caption(request.model_copy(update={"requested_language": "fr"}), config=config, client=cache)
    assert not get_cached_caption(request.model_copy(update={"fallback_languages": ("ar",)}), config=config, client=cache)
    assert not get_cached_caption(request, config=Settings(_env_file=None, youtube_base_url="http://fixture.test"), client=cache)


@pytest.mark.parametrize("corruption", ["json", "body", "video", "future"])
def test_corrupt_captions_are_never_used(corruption):
    config, cache = Settings(_env_file=None), MemoryCache()
    request = YouTubeTranscriptInput(video_id="fixture01")
    store_caption(request, caption(), config=config, client=cache)
    key = cache_key(request, config)
    value = json.loads(cache.values[key])
    if corruption == "body":
        value["transcript"]["segments"][0]["text"] = "unverified replacement"
    elif corruption == "video":
        value["transcript"]["video_id"] = "other001"
    elif corruption == "future":
        value["fetched_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    cache.values[key] = "bad json" if corruption == "json" else json.dumps(value)
    assert get_cached_caption(request, config=config, client=cache) is None


def test_caption_bootstrap_parser_requires_identity_and_times():
    provenance = {"video_id": "fixture01", "source_language": "es", "delivered_language": "en",
                  "caption_kind": "automatic", "translated": True}
    rebuilt = caption_from_node("[15.000-20.000] Battery lasted thirty hours under light use", provenance, "fixture01")
    assert rebuilt == caption()
    with pytest.raises(ValueError):
        caption_from_node("[15-10] words in wrong order", provenance, "fixture01")
    with pytest.raises(ValueError):
        caption_from_node("[15-20] words from another video", provenance, "other001")


def test_source_slots_never_resurrect_exclusions_or_duplicate_videos():
    ids = [f"video00{i}" for i in range(1, 9)]
    candidates = {v: {"channel_id": f"channel-{i}", "deterministic_score": .8,
                     "deterministic_exclusion": "below_review_duration" if i == 0 else None}
                  for i, v in enumerate(ids)}
    decisions = [{"video_id": v, "eligible": True, "classification": "review"} for v in ids]
    decisions[-1]["eligible"] = False
    cached = frozenset(ids[2:7])
    queues = source_slot_queues(ids + ids[:1], candidates, decisions, 5, cached)
    allocated = [v for queue in queues for v in queue]
    assert len(allocated) == len(set(allocated))
    assert ids[0] not in allocated and ids[-1] not in allocated
    assert {queue[0] for queue in queues} == cached
    assert len({candidates[queue[0]]["channel_id"] for queue in queues}) == 5


def test_combined_output_binds_ids_and_retains_evidence_validation():
    result = parse_video_extraction(compact_payload())
    source_id = uuid.uuid4()
    draft = bind_review(result.review, source_id)
    assert draft.source_id == source_id
    assert draft.claims[0].evidence[0].source_node_id == source_id
    quote = draft.claims[0].evidence[0]
    assert transcript_excerpt_matches("[15-20] " + quote.evidence_text, quote.evidence_text, 15, 20)
    assert not transcript_excerpt_matches("[15-20] A different video has no battery test", quote.evidence_text, 15, 20)
    assert "source_node_id" not in json.dumps(VideoExtraction.model_json_schema())
    assert "source_id" not in VideoExtraction.model_json_schema()["$defs"]["CompactReview"]["properties"]


def test_invalid_product_section_degrades_without_rejecting_review():
    payload = compact_payload()
    payload["product_information"] = {"facts": "invalid"}
    invocation = SimpleNamespace(response_schema=VideoExtraction.model_json_schema(),
                                 optional_output_fields=("product_information",))
    cleaned, errors = validated_chat_content(invocation, payload)
    assert errors == [] and cleaned["product_information"] is None
    assert parse_video_extraction(cleaned).review.claims
    payload["review"]["claims"] = []
    assert validated_chat_content(invocation, payload)[1]


def test_compact_output_rejects_more_than_six_claims_and_identity_keys():
    payload = compact_payload()
    payload["review"]["claims"] *= 7
    with pytest.raises(ValidationError):
        parse_video_extraction(payload)
    payload = compact_payload()
    payload["review"]["claims"][0]["evidence"][0]["source_node_id"] = str(uuid.uuid4())
    with pytest.raises(ValidationError):
        parse_video_extraction(payload)


def test_snapshot_contracts_accept_legacy_and_combined_review():
    assert snapshot_output_model("review_analyst", LEGACY_REVIEW_SPEC.output_model.model_json_schema()) is SourceAnalysisDraft
    assert snapshot_output_model("review_analyst", AGENT_REGISTRY["review_analyst"].output_model.model_json_schema()) is VideoExtraction
    with pytest.raises(ValueError):
        snapshot_output_model("review_analyst", {"type": "object"})


def test_review_context_is_explicit_and_cannot_expand_into_other_sources():
    policy = AGENT_REGISTRY["review_analyst"].retrieval_policy
    assert policy.maximum_graph_hops == policy.vector_top_k == policy.lexical_candidate_limit == 0
    assert {item.value for item in policy.allowed_node_types} == {"source", "transcript_chunk"}
    assert policy.input_token_budget <= LEGACY_REVIEW_SPEC.retrieval_policy.input_token_budget
    assert AGENT_REGISTRY["review_analyst"].max_output_tokens <= LEGACY_REVIEW_SPEC.max_output_tokens


def test_cached_acquisition_never_calls_blocked_upstream(monkeypatch):
    config = Settings(_env_file=None)
    entry = SimpleNamespace(transcript=caption())
    monkeypatch.setattr(runner, "available_caption", lambda *_args, **_kwargs: entry)

    async def blocked(*_args, **_kwargs):
        raise AssertionError("cache hit must avoid upstream acquisition")

    monkeypatch.setattr(runner, "fetch_transcript", blocked)
    result, retries = asyncio.run(runner._dispatch(TOOL_REGISTRY["youtube.transcript"],
        SimpleNamespace(deadline_at=datetime.now(timezone.utc) + timedelta(seconds=45)), uuid.uuid4(),
        YouTubeTranscriptInput(video_id="fixture01"), config=config))
    assert result == caption() and retries == 0


def test_uncached_blocked_acquisition_stops_after_one_request(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "available_caption", lambda *_args, **_kwargs: None)

    async def blocked(*_args, **_kwargs):
        calls.append(1)
        raise ToolExecutionError("transcript_access_blocked", category="upstream", retryable=False)

    monkeypatch.setattr(runner, "fetch_transcript", blocked)
    with pytest.raises(ToolExecutionError, match="transcript_access_blocked"):
        asyncio.run(runner._dispatch(TOOL_REGISTRY["youtube.transcript"],
            SimpleNamespace(deadline_at=datetime.now(timezone.utc) + timedelta(seconds=45)), uuid.uuid4(),
            YouTubeTranscriptInput(video_id="fixture01"), config=Settings(_env_file=None)))
    assert len(calls) == 1


def test_duplicate_compact_claims_are_collapsed():
    payload = compact_payload()
    payload["review"]["claims"] *= 2
    assert len(parse_video_extraction(payload).review.claims) == 1


@pytest.mark.parametrize("rejection", [None, "origin", "language", "expired", "proof", "corrupt"])
def test_bootstrap_requires_verified_acquisition_and_preserves_original_age(monkeypatch, rejection):
    config, cache = Settings(_env_file=None), MemoryCache()
    now = datetime.now(timezone.utc)
    fetched = now - timedelta(days=8 if rejection == "expired" else 2)
    provenance = {"video_id": "fixture01", "source_language": "es", "delivered_language": "en",
        "caption_kind": "automatic", "translated": True, "caption_origin": caption_cache.caption_origin(config),
        "caption_fetched_at": fetched.isoformat()}
    if rejection == "origin":
        provenance["caption_origin"] = "foreign-fixture-origin"
    version = SimpleNamespace(provenance=provenance, created_at=now, created_by_attempt_id=uuid.uuid4())
    workspace = SimpleNamespace(id=uuid.uuid4())

    class Database:
        def execute(self, _query):
            return SimpleNamespace(all=lambda: [(version, workspace, {"language": "fr" if rejection == "language" else "en"})])

        def scalar(self, _query):
            return None if rejection == "proof" else uuid.uuid4()

    @contextmanager
    def session():
        yield Database()

    def read(*_args, **_kwargs):
        if rejection == "corrupt":
            raise caption_cache.KnowledgeGraphError("body_hash_mismatch")
        return {}, "[15-20] Battery lasted thirty hours under light use"

    monkeypatch.setattr(caption_cache, "session_scope", session)
    monkeypatch.setattr(caption_cache, "read_version_body", read)
    monkeypatch.setattr(caption_cache, "get_redis", lambda: cache)
    entry = caption_cache.bootstrap_caption(YouTubeTranscriptInput(video_id="fixture01"), config=config)
    if rejection is None:
        assert entry.fetched_at == fetched and entry.transcript == caption()
        assert 5 * 86400 - 5 <= next(iter(cache.ttls.values())) <= 5 * 86400
    else:
        assert entry is None and not cache.values
