from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.admin.cutover import _percentile, evaluate_metrics
from app.api import routes
from app.api.v2.dependencies import get_v2_db, get_v2_redis
from app.compatibility.v1 import deprecation_headers, legacy_progress
from app.config import Settings
from app.main import app
from app.models import AnalyzeRequest
from app.platform.http_security import V2RequestGuardMiddleware
from tests.openrouter_mock import app as openrouter_mock_app


def test_legacy_request_guard_and_deprecation_contract() -> None:
    guarded = FastAPI()
    guarded.add_middleware(
        V2RequestGuardMiddleware,
        config=Settings(_env_file=None, v2_max_request_body_bytes=1024),
    )

    @guarded.post("/api/analyze")
    async def echo(request: Request) -> dict:
        return {"size": len(await request.body())}

    @guarded.post("/api/analyze/stream")
    async def stream() -> StreamingResponse:
        async def events():
            yield "event: result\ndata: {}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    with TestClient(guarded) as client:
        media = client.post("/api/analyze", content="{}", headers={"Content-Type": "text/plain"})
        overflow = client.post(
            "/api/analyze",
            content=(chunk for chunk in (b'"' + b"a" * 700, b"b" * 700 + b'"')),
            headers={"Content-Type": "application/json"},
        )
        streamed = client.post("/api/analyze/stream", json={"product_name": "Camera Pro"})
    for response, expected in ((media, 415), (overflow, 413)):
        assert response.status_code == expected
        assert response.headers["Deprecation"] == "true"
        assert response.headers["Link"] == '</api/v2/analyses>; rel="successor-version"'

    assert "Sunset" not in deprecation_headers(Settings(_env_file=None))
    assert streamed.status_code == 200
    assert streamed.text == "event: result\ndata: {}\n\n"
    sunset = deprecation_headers(
        Settings(_env_file=None, legacy_api_sunset_at="2027-01-01T00:00:00Z")
    )
    assert sunset["Sunset"] == "Fri, 01 Jan 2027 00:00:00 GMT"


def test_legacy_provider_is_wire_only_and_progress_is_safe() -> None:
    auto = routes._v2_payload(AnalyzeRequest(product_name="Camera Pro", provider="auto"))
    openai = routes._v2_payload(AnalyzeRequest(product_name="Camera Pro", provider="openai"))
    assert auto == openai
    assert auto.model_dump() == {
        "product_name": "Camera Pro",
        "video_count": None,
        "analyze_comments": False,
        "locale": None,
    }
    assert legacy_progress(
        {"task_key": "analyze_review.source_1", "percent": 40},
        "task.progress",
    ) == {
        "stage": "analyze_review_source_1",
        "label": "Research in progress",
        "percent": 40,
        "detail": "ReviewLens is processing the durable V2 run.",
    }


def test_disabled_adapter_returns_safe_410_with_successor(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "settings",
        Settings(_env_file=None, legacy_analysis_adapter_enabled=False),
    )
    monkeypatch.setattr(routes, "record_rejection", lambda **_kwargs: None)

    def fake_db():
        yield object()

    app.dependency_overrides[get_v2_db] = fake_db
    app.dependency_overrides[get_v2_redis] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/analyze",
                json={"product_name": "Camera Pro", "provider": "xai"},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "legacy_adapter_disabled"
    assert response.headers["Deprecation"] == "true"


class _FakeCutoverSession:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = iter(rows)

    def scalars(self, _statement):
        return iter(next(self.rows))


def test_stable_window_gate_passes_only_at_every_boundary() -> None:
    now = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    run_ids = [uuid.uuid4() for _ in range(20)]
    runs = [
        SimpleNamespace(
            id=run_id,
            status="complete" if index < 18 else "failed",
            created_at=now - timedelta(hours=25),
            started_at=now - timedelta(seconds=900),
            completed_at=now,
        )
        for index, run_id in enumerate(run_ids)
    ]
    successful = runs[:18]
    usage = [
        SimpleNamespace(
            run_id=run.id,
            total_tokens=100 + index,
            total_cost_microusd=1000 + index,
            usage_status="complete",
        )
        for index, run in enumerate(runs)
    ]
    budgets = [
        SimpleNamespace(
            max_tokens=1000,
            consumed_tokens=200,
            max_cost_microusd=5000,
            consumed_cost_microusd=1000,
        )
        for _ in runs
    ]
    daily = [SimpleNamespace(max_cost_microusd=100_000, consumed_cost_microusd=20_000, reserved_cost_microusd=0)]
    publications = [
        SimpleNamespace(
            payload={"sources": [{"claims": [{"central": True, "evidence": [{"id": "e1", "text": "linked"}]}]}]}
        )
        for _ in successful
    ]
    compatibility = [
        SimpleNamespace(mapped_response=True, status="complete")
        for _ in range(5)
    ]
    observation = SimpleNamespace(
        started_at=now - timedelta(hours=24),
        thresholds={
            "stable_window_hours": 24,
            "min_terminal_runs": 20,
            "min_compatibility_requests": 5,
            "max_failure_rate": 0.10,
            "max_p95_run_latency_seconds": 900,
            "min_compatibility_success_rate": 0.95,
        },
    )
    result = evaluate_metrics(
        _FakeCutoverSession([runs, usage, budgets, daily, publications, compatibility]),
        observation,
        now=now,
    )
    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["samples"]["terminal_runs"] == 20
    assert result["distributions"]["p95_tokens_per_run"] == _percentile(
        [event.total_tokens for event in usage], 0.95
    )

    compatibility[-1].mapped_response = False
    failed = evaluate_metrics(
        _FakeCutoverSession([runs, usage, budgets, daily, publications, compatibility]),
        observation,
        now=now,
    )
    assert failed["ready"] is False
    assert failed["blockers"] == ["compatibility_mapping"]


def test_phase11_mock_rejects_every_non_deepseek_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("PHASE11_ALLOWED_INFERENCE_MODEL", "deepseek/deepseek-v4-flash")
    body = {
        "provider": {"require_parameters": True},
        "response_format": {"type": "json_schema", "json_schema": {"name": "phase3_fixture"}},
        "messages": [{"role": "user", "content": "fixture"}],
        "metadata": {},
    }
    headers = {
        "Authorization": "Bearer fixture",
        "X-Title": "ReviewLens tests",
        "HTTP-Referer": "https://reviewlens.test",
        "X-Request-ID": "phase11-model-fixture",
    }
    with TestClient(openrouter_mock_app) as client:
        client.post("/history/reset")
        denied = client.post(
            "/api/v1/chat/completions",
            headers=headers,
            json=body | {"models": ["fixture/chat-model"]},
        )
        allowed = client.post(
            "/api/v1/chat/completions",
            headers=headers,
            json=body | {"models": ["deepseek/deepseek-v4-flash"]},
        )
        history = client.get("/history").json()["inference"]
    assert denied.status_code == 400
    assert allowed.status_code == 200
    assert history == [
        {"operation": "chat", "model": "deepseek/deepseek-v4-flash", "calls": 1}
    ]
