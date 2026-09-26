from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v2 import analyses as routes
from app.config import Settings
from app.errors import V2Error
from app.public.admission import _parse_cookie, resolve_session
from app.public.contracts import AnalysisRequest, PublicReportResponse
from app.public.reports import PublicProjectionError, _require_nodes, report_token, token_hash


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        session_secret="s" * 40,
        public_token_hash_secret="p" * 40,
        rate_limit_hash_secret="r" * 40,
    )


def test_analysis_contract_normalizes_product_and_rejects_extras() -> None:
    request = AnalysisRequest(product_name="  POCO   F7  ", video_count=5)
    assert request.product_name == "POCO F7"
    with pytest.raises(ValidationError):
        AnalysisRequest(product_name="POCO F7", provider="openai")
    with pytest.raises(ValidationError):
        AnalysisRequest(product_name="POCO F7", video_count=9)
    with pytest.raises(ValidationError):
        AnalysisRequest(product_name="POCO F7", locale="../../etc")


def test_progress_counts_only_real_reviews_and_marks_unavailable_work(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    tasks = [
        SimpleNamespace(id=uuid.uuid4(), workflow_task_key="fetch_transcript.source_1", status="succeeded", created_at=now, started_at=now, completed_at=now),
        SimpleNamespace(id=uuid.uuid4(), workflow_task_key="analyze_review.source_1", status="succeeded", created_at=now, started_at=now, completed_at=now),
        SimpleNamespace(id=uuid.uuid4(), workflow_task_key="analyze_review.source_2", status="succeeded", created_at=now, started_at=now, completed_at=now),
    ]
    db = MagicMock()
    db.scalars.return_value = tasks
    db.scalar.return_value = None
    rows = [
        (tasks[0].id, {"available": False}),
        (tasks[1].id, {"skipped": True, "reason": "source_unavailable"}),
        (tasks[2].id, {"analysis": {"source_id": "verified"}}),
    ]
    class SqlAlchemyLikeResult:
        def keys(self):
            return ("task_run_id", "output_payload")

        def __iter__(self):
            return iter(rows)

    db.execute.return_value = SqlAlchemyLikeResult()
    monkeypatch.setattr(routes, "usage_summary", lambda *_args: {"total_tokens": 0, "usage_pending": False})
    monkeypatch.setattr(routes, "product_info_from_tasks", lambda *_args: None)
    run = SimpleNamespace(
        id=uuid.uuid4(), status="running", product_input="Product", created_at=now,
        started_at=now, completed_at=None, requested_options={"source_count": 3},
        warning_summary={}, progress_sequence=1,
    )
    result = routes._status(db, run)
    assert result.source_count_analyzed == 1
    assert [task.status for task in result.tasks] == ["skipped", "skipped", "succeeded"]


def test_caption_rate_limit_has_a_distinct_public_failure() -> None:
    db = MagicMock()
    db.scalars.return_value = [SimpleNamespace(status="failed", error_code="transcript_access_blocked")]
    failure = routes._public_failure(db, SimpleNamespace(id=uuid.uuid4()), [])
    assert failure["code"] == "captions_rate_limited"
    assert "YouTube" in failure["message"]


def test_older_upstream_caption_failures_do_not_claim_captions_are_missing() -> None:
    db = MagicMock()
    db.scalars.return_value = []
    db.scalar.return_value = uuid.uuid4()
    failure = routes._public_failure(db, SimpleNamespace(id=uuid.uuid4()), [])
    assert failure["code"] == "caption_retrieval_failed"
    assert "could not be retrieved" in failure["message"]


def test_public_create_accepts_the_same_unwrapped_body_as_preflight() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    preflight = paths["/api/v2/analyses/preflight"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    create = paths["/api/v2/analyses"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert create == preflight == {"$ref": "#/components/schemas/AnalysisRequest"}


def test_signed_anonymous_cookie_has_tamper_detection_and_no_plain_identifier_in_db() -> None:
    config = _settings()
    db = MagicMock()
    db.scalar.return_value = None
    session, cookie = resolve_session(db, None, create=True, config=config)
    assert session is not None and cookie is not None
    identifier = _parse_cookie(cookie, config)
    assert identifier and identifier not in session.identifier_hash
    assert _parse_cookie(cookie[:-1] + ("A" if cookie[-1] != "A" else "B"), config) is None


def test_report_token_is_256_bit_derived_and_hash_only() -> None:
    config = _settings()
    report_id = uuid.uuid4()
    token = report_token(report_id, config)
    assert len(token) == 43
    assert str(report_id) not in token
    assert token == report_token(report_id, config)
    assert token != report_token(uuid.uuid4(), config)
    digest = token_hash(token, config)
    assert len(digest) == 64 and token not in digest


def test_public_graph_cursor_is_bound_to_report_and_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "settings", _settings())
    report_id = uuid.uuid4()
    cursor = routes._cursor(report_id, 25, "finding")
    assert routes._parse_cursor(cursor, report_id, "finding") == 25
    with pytest.raises(V2Error):
        routes._parse_cursor(cursor, uuid.uuid4(), "finding")
    with pytest.raises(V2Error):
        routes._parse_cursor(cursor, report_id, "source")
    with pytest.raises(V2Error):
        routes._parse_cursor(cursor + "A", report_id, "finding")


def test_public_projection_contract_forbids_cost_and_internal_fields() -> None:
    safe = {
        "schema_version": 1,
        "report_id": str(uuid.uuid4()),
        "product_name": "POCO F7",
        "status": "partial",
        "source_count_requested": 5,
        "source_count_analyzed": 1,
        "overall_score": 70,
        "verdict": "buy_with_caveats",
        "confidence": 45,
        "confidence_band": "medium",
        "summary": "Evidence is limited.",
        "consensus_pros": [],
        "consensus_cons": [],
        "disagreements": [],
        "longest_usage_period": None,
        "longest_usage_source_id": None,
        "who_should_buy": [],
        "who_should_avoid": [],
        "limitations": [],
        "warnings": ["partial_coverage"],
        "sources": [],
        "generated_at": "2026-09-16T00:00:00Z",
        "total_tokens": 120,
        "model_call_count": 2,
        "usage_pending": False,
    }
    assert PublicReportResponse.model_validate(safe).total_tokens == 120
    with pytest.raises(ValidationError):
        PublicReportResponse.model_validate({**safe, "cost_microusd": 500})
    with pytest.raises(ValidationError):
        PublicReportResponse.model_validate({**safe, "model_provider": "private"})


def test_anonymous_cookie_mutations_require_allowed_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _settings()
    config.backend_cors_origins = "http://localhost:3000"
    monkeypatch.setattr(routes, "settings", config)
    scope = {"type": "http", "method": "POST", "path": "/api/v2/analyses", "headers": [(b"cookie", b"reviewlens_anonymous_session=opaque")]}
    with pytest.raises(V2Error):
        routes._check_origin(Request(scope))
    scope["headers"].append((b"origin", b"http://localhost:3000"))
    routes._check_origin(Request(scope))


def test_public_projection_rejects_cross_workspace_nodes() -> None:
    workspace_id = uuid.uuid4()
    node_id = uuid.uuid4()
    db = MagicMock()
    db.scalars.return_value = [
        type("Node", (), {"id": node_id, "workspace_id": uuid.uuid4(), "status": "active", "current_version_id": uuid.uuid4(), "node_type": "source"})()
    ]
    report = type("Report", (), {"workspace_id": workspace_id})()
    with pytest.raises(PublicProjectionError):
        _require_nodes(db, report, {node_id: "source"})
