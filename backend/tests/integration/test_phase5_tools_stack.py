from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.db.models import (
    AnalysisRun,
    ContextNode,
    TaskRun,
    ToolDefinition,
    ToolInvocation,
    ToolVersion,
    YouTubeQuotaReservation,
    YouTubeQuotaState,
)
from app.db.session import session_scope
from app.tools.contracts import ResearchQueryPlan
from app.tools.errors import ToolAuthorizationError, ToolExecutionError
from app.tools.fixtures import create_research_fixture_attempt, finish_research_fixture
from app.tools.registry import TOOL_REGISTRY, seed_tool_registry
from app.tools.research import execute_research
from app.tools.runner import _authorize, _begin_invocation, _finish_invocation, invoke_tool
from app.tools.youtube import finalize_quota, reserve_quota, youtube_quota_date

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1",
    reason="set REVIEWLENS_RUN_INTEGRATION=1 inside the isolated Compose test stack",
)


def _create(scenario: str):
    with session_scope() as db:
        run, task, attempt = create_research_fixture_attempt(db, scenario)  # type: ignore[arg-type]
        return run.id, task.id, attempt.id, run.product_input, bool(
            run.requested_options.get("analyze_comments")
        )


def _execute(scenario: str):
    run_id, task_id, attempt_id, product, analyze_comments = _create(scenario)
    result = asyncio.run(
        execute_research(
            attempt_id,
            ResearchQueryPlan(
                canonical_product=product,
                queries=(f"{product} review", f"{product} long term review"),
                requested_source_count=5,
                analyze_comments=analyze_comments,
            ),
        )
    )
    with session_scope() as db:
        finish_research_fixture(
            db,
            run_id,
            task_id,
            attempt_id,
            result.model_dump(mode="json"),
        )
    return run_id, attempt_id, result


def test_tool_seed_is_idempotent_and_matches_static_registry() -> None:
    with session_scope() as db:
        assert seed_tool_registry(db) == {"definitions_created": 0, "versions_created": 0}
        definitions = set(db.scalars(select(ToolDefinition.key)))
        versions = list(
            db.scalars(
                select(ToolVersion).where(
                    ToolVersion.semantic_version == "1.0.0",
                    ToolVersion.lifecycle == "published",
                )
            )
        )
    assert set(TOOL_REGISTRY) <= definitions
    assert len(versions) >= 12


def test_complete_research_persists_five_sources_comments_graph_and_safe_audits() -> None:
    run_id, _, result = _execute("complete")
    assert len(result.selected_sources) == 5
    assert not result.warning_codes
    with session_scope() as db:
        node_types = dict(
            db.execute(
                select(ContextNode.node_type, func.count())
                .where(ContextNode.workspace_id == result.workspace_id)
                .group_by(ContextNode.node_type)
            ).all()
        )
        invocations = list(
            db.scalars(select(ToolInvocation).where(ToolInvocation.run_id == run_id))
        )
    assert node_types["source"] >= 5
    assert node_types["transcript"] == 5
    assert node_types["transcript_chunk"] >= 5
    assert node_types["comment_set"] == 5
    assert invocations and all(item.status == "succeeded" for item in invocations)
    serialized_metadata = str([item.safe_metadata for item in invocations])
    assert "Ignore previous instructions" not in serialized_metadata
    assert "Audience observation" not in serialized_metadata


def test_missing_transcript_advances_and_partial_fixture_is_useful() -> None:
    missing_run_id, _, missing = _execute("missing_transcript")
    assert len(missing.selected_sources) == 5
    with session_scope() as db:
        failed_transcript = db.scalar(
            select(func.count())
            .select_from(ToolInvocation)
            .where(
                ToolInvocation.run_id == missing_run_id,
                ToolInvocation.tool_key == "youtube.transcript",
                ToolInvocation.status == "failed",
                ToolInvocation.error_code == "transcript_unavailable",
            )
        )
    assert failed_transcript == 1

    _, _, partial = _execute("partial")
    assert len(partial.selected_sources) == 2
    assert partial.warning_codes == ("partial_source_coverage",)


def test_comments_off_performs_no_comment_invocation_or_mock_request() -> None:
    httpx.post("http://youtube-mock:8090/reset", timeout=5).raise_for_status()
    run_id, _, result = _execute("comments_off")
    assert len(result.selected_sources) == 5
    with session_scope() as db:
        comment_invocations = db.scalar(
            select(func.count())
            .select_from(ToolInvocation)
            .where(
                ToolInvocation.run_id == run_id,
                ToolInvocation.tool_key == "youtube.comments",
            )
        )
    metrics = httpx.get("http://youtube-mock:8090/metrics", timeout=5).json()
    assert comment_invocations == 0
    assert metrics.get("comments", 0) == 0


def test_duplicate_logical_call_is_rejected_and_terminal_invocation_is_immutable() -> None:
    run_id, _, attempt_id, _, _ = _create("comments_off")
    source_id = str(uuid.uuid4())
    payload = {
        "sources": [
            {
                "source_id": source_id,
                "channel_id": "fixture-channel",
                "reviewer_sentiment_score": 70,
                "purchase_recommendation_score": 80,
                "evidence_quality_score": 90,
                "review_type": "long_term",
            }
        ],
        "requested_source_count": 3,
    }
    first = asyncio.run(
        invoke_tool(attempt_id, "scoring.preview", payload, call_key="duplicate.fixture")
    )
    assert first["publishable"] is True
    with pytest.raises(ToolExecutionError, match="duplicate_tool_invocation"):
        asyncio.run(
            invoke_tool(attempt_id, "scoring.preview", payload, call_key="duplicate.fixture")
        )
    with session_scope() as db:
        invocation_id = db.scalar(
            select(ToolInvocation.id).where(
                ToolInvocation.run_id == run_id,
                ToolInvocation.tool_key == "scoring.preview",
            )
        )
    with pytest.raises(DBAPIError):
        with session_scope() as db:
            invocation = db.get(ToolInvocation, invocation_id)
            assert invocation
            invocation.safe_metadata = {"forbidden": True}


def test_tool_start_honors_cancellation_and_transactional_concurrency_limit() -> None:
    run_id, task_id, attempt_id, _, _ = _create("comments_off")
    payload = {
        "nodes": [
            {
                "node_type": "finding",
                "title": "Fixture finding",
                "body": "Safe fixture body",
                "trust_level": "derived",
            }
        ]
    }
    spec, first_context, input_hash = _authorize(
        attempt_id, "graph.create_nodes", payload, "concurrency.first"
    )
    first = _begin_invocation(spec, first_context, input_hash)
    try:
        _, second_context, second_hash = _authorize(
            attempt_id, "graph.create_nodes", payload, "concurrency.second"
        )
        with pytest.raises(ToolExecutionError, match="tool_concurrency_exhausted"):
            _begin_invocation(spec, second_context, second_hash)
    finally:
        _finish_invocation(
            first.id,
            status="failed",
            error=ToolExecutionError("fixture_finished", category="test"),
        )

    with session_scope() as db:
        task = db.get(TaskRun, task_id)
        run = db.get(AnalysisRun, run_id)
        assert task and run
        task.status = "cancelling"
        run.status = "cancelling"
    with pytest.raises(ToolAuthorizationError, match="tool_attempt_not_running"):
        asyncio.run(
            invoke_tool(
                attempt_id,
                "scoring.preview",
                {
                    "sources": [
                        {
                            "source_id": str(uuid.uuid4()),
                            "channel_id": "fixture-channel",
                            "reviewer_sentiment_score": 70,
                            "purchase_recommendation_score": 80,
                            "evidence_quality_score": 90,
                            "review_type": "long_term",
                        }
                    ],
                    "requested_source_count": 3,
                },
                call_key="cancelled.fixture",
            )
        )


def test_quota_reservations_block_concurrent_overage_and_release_safely() -> None:
    run_id, _, attempt_id, _, _ = _create("comments_off")
    payload = {
        "sources": [
            {
                "source_id": str(uuid.uuid4()),
                "channel_id": "fixture-channel",
                "reviewer_sentiment_score": 70,
                "purchase_recommendation_score": 80,
                "evidence_quality_score": 90,
                "review_type": "long_term",
            }
        ],
        "requested_source_count": 3,
    }
    asyncio.run(invoke_tool(attempt_id, "scoring.preview", payload, call_key="quota.parent"))
    with session_scope() as db:
        invocation_id = db.scalar(
            select(ToolInvocation.id).where(
                ToolInvocation.run_id == run_id,
                ToolInvocation.tool_key == "scoring.preview",
            )
        )
    state_key = {"bucket": "search", "quota_date": youtube_quota_date()}
    with session_scope() as db:
        state = db.get(YouTubeQuotaState, state_key)
        if state is None:
            state = YouTubeQuotaState(
                **state_key,
                limit_units=settings.youtube_search_daily_call_limit,
                reserved_units=0,
                consumed_units=0,
            )
            db.add(state)
            db.flush()
        original_reserved = state.reserved_units
        original_consumed = state.consumed_units
        state.reserved_units = state.limit_units - state.consumed_units - 1

    try:
        reservation_id = reserve_quota(
            invocation_id,
            bucket="search",
            units=1,
            network_attempt=1,
        )
        with pytest.raises(ToolExecutionError, match="youtube_quota_exhausted"):
            reserve_quota(
                invocation_id,
                bucket="search",
                units=1,
                network_attempt=2,
            )
        finalize_quota(reservation_id, consumed=False)
        with session_scope() as db:
            state = db.get(YouTubeQuotaState, state_key)
            reservation = db.get(YouTubeQuotaReservation, reservation_id)
            assert state and state.reserved_units == state.limit_units - state.consumed_units - 1
            assert reservation and reservation.status == "released"
    finally:
        with session_scope() as db:
            state = db.get(YouTubeQuotaState, state_key)
            assert state is not None
            state.reserved_units = original_reserved
            state.consumed_units = original_consumed
