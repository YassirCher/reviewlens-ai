from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.admin.evaluation import GOLDEN_CASES, case_fixture, golden_cases
from app.analysis.registry import AGENT_REGISTRY
from app.api.v2 import auth as auth_routes
from app.api.v2.admin_analytics import _safe_csv_cell
from app.api.v2.dependencies import get_v2_db, get_v2_redis
from app.config import Settings
from app.db.models import DailyBudgetState, OpenRouterAccountState
from app.knowledge.storage import MarkdownValidationError, resolve_body_path
from app.main import app
from app.observability import JsonFormatter, operation_context, redact_log_message
from app.platform import alerts as alerts_module
from app.platform.alerts import collect_operational_alerts
from app.platform.http_security import V2RequestGuardMiddleware
from app.services.audit_service import sanitize_audit_metadata
from app.services.admin_auth import SessionTokens
from tests.openrouter_mock import app as openrouter_mock_app
from tests.youtube_mock import app as youtube_mock_app


def test_v2_request_guard_rejects_media_type_declared_and_chunked_overflow() -> None:
    guarded = FastAPI()
    config = Settings(_env_file=None, v2_max_request_body_bytes=1024)
    guarded.add_middleware(V2RequestGuardMiddleware, config=config)

    @guarded.post("/api/v2/echo")
    async def echo(request: Request) -> dict:
        return {"size": len(await request.body())}

    @guarded.delete("/api/v2/echo")
    async def delete_echo(request: Request) -> dict:
        return {"size": len(await request.body())}

    with TestClient(guarded) as client:
        wrong_type = client.post("/api/v2/echo", content=b"{}", headers={"Content-Type": "text/plain"})
        declared = client.post(
            "/api/v2/echo", content=b"x" * 1025,
            headers={"Content-Type": "application/json", "Content-Length": "1025"},
        )
        chunked = client.post(
            "/api/v2/echo",
            content=(chunk for chunk in (b'"' + b"a" * 700, b"b" * 700 + b'"')),
            headers={"Content-Type": "application/json"},
        )
        delete_overflow = client.request(
            "DELETE", "/api/v2/echo", content=b"x" * 1025,
            headers={"Content-Type": "application/json"},
        )
        accepted = client.post("/api/v2/echo", json={"ok": True})

    assert wrong_type.status_code == 415
    assert wrong_type.json()["error"]["code"] == "unsupported_media_type"
    assert declared.status_code == 413
    assert chunked.status_code == 413
    assert delete_overflow.status_code == 413
    assert accepted.status_code == 200
    assert accepted.json()["size"] > 0


def test_api_headers_and_hostile_origin_are_strict() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        api_response = client.get("/api/v2/admin/session")
        hostile = client.options(
            "/api/v2/admin/session",
            headers={"Origin": "https://hostile.example", "Access-Control-Request-Method": "POST"},
        )
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in api_response.headers["Content-Security-Policy"]
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert hostile.status_code == 400
    assert "Access-Control-Allow-Origin" not in hostile.headers


def test_production_login_cookie_is_secure(monkeypatch) -> None:
    admin = SimpleNamespace(id=uuid.uuid4(), identifier="admin@example.test")
    instant = datetime(2026, 9, 19, 13, tzinfo=timezone.utc)
    session = SimpleNamespace(id=uuid.uuid4(), expires_at=instant, absolute_expires_at=instant)

    class FakeService:
        def __init__(self, *_args, **_kwargs):
            pass

        def login(self, **_kwargs):
            return SessionTokens(
                session_token="phase10-session", csrf_token="phase10-csrf",
                session=session, admin=admin,
            )

    def fake_db():
        yield object()

    production = Settings(_env_file=None, app_env="production")
    monkeypatch.setattr(auth_routes, "AdminAuthService", FakeService)
    monkeypatch.setattr(auth_routes, "settings", production)
    app.dependency_overrides[get_v2_db] = fake_db
    app.dependency_overrides[get_v2_redis] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/admin/session",
                json={"email": "admin@example.test", "password": "not-returned"},
            )
    finally:
        app.dependency_overrides.clear()
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie


def test_audit_and_log_redaction_remove_secrets_and_correlation_is_structured() -> None:
    metadata = sanitize_audit_metadata({
        "operation": "publish",
        "authorization": "Bearer do-not-record",
        "nested": {"prompt": "private body", "count": 2},
        "items": list(range(100)),
    })
    assert metadata["operation"] == "publish"
    assert "authorization" not in metadata
    assert metadata["nested"] == {"count": 2}
    assert len(metadata["items"]) == 25

    message = redact_log_message(
        'authorization=Bearer secret-token cookie=session=value password="private" prompt=source-body'
    )
    assert "secret-token" not in message
    assert "session=value" not in message
    assert "private" not in message
    assert "source-body" not in message

    record = logging.LogRecord("phase10", logging.INFO, __file__, 1, "safe event", (), None)
    with operation_context(run_id="run-1", task_id="task-1", attempt_id="attempt-1"):
        payload = json.loads(JsonFormatter().format(record))
    assert payload | {"run_id": "run-1", "task_id": "task-1", "attempt_id": "attempt-1"} == payload


def test_csv_and_markdown_paths_reject_formula_and_link_attacks(tmp_path: Path) -> None:
    assert _safe_csv_cell("=HYPERLINK(\"https://evil.example\")").startswith("'")
    assert _safe_csv_cell("  @SUM(1,1)").startswith("'")
    assert _safe_csv_cell("ordinary") == "ordinary"

    root = tmp_path / "workspace"
    root.mkdir()
    assert resolve_body_path(root, "nodes/good.md") == root / "nodes" / "good.md"
    with pytest.raises(MarkdownValidationError):
        resolve_body_path(root, "nodes/bad.txt")
    with pytest.raises(MarkdownValidationError):
        resolve_body_path(root, "../escape.md")

    original = root / "original.md"
    original.write_text("safe", encoding="utf-8")
    hardlink = root / "hardlink.md"
    os.link(original, hardlink)
    with pytest.raises(MarkdownValidationError):
        resolve_body_path(root, "hardlink.md")

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = root / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(MarkdownValidationError):
        resolve_body_path(root, "linked/escape.md")

    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(root, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(MarkdownValidationError):
        resolve_body_path(linked_root, "nodes/good.md")


def test_operational_alert_thresholds_are_stable_and_typed(monkeypatch) -> None:
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)

    class FakeSession:
        def __init__(self) -> None:
            self.values = iter((
                6, 2, now - timedelta(minutes=16), 5, 1, 2,
                now - timedelta(hours=2), 1,
            ))

        def get(self, model, _key):
            if model is OpenRouterAccountState:
                return SimpleNamespace(status="payment_blocked")
            if model is DailyBudgetState:
                return SimpleNamespace(
                    reserved_cost_microusd=200, consumed_cost_microusd=800, max_cost_microusd=1000,
                )
            return None

        def scalar(self, _statement):
            return next(self.values)

    monkeypatch.setattr(alerts_module, "worker_is_reachable", lambda: False)
    monkeypatch.setattr(alerts_module, "scheduler_heartbeat_is_fresh", lambda: False)
    config = Settings(
        _env_file=None, operations_error_rate_min_runs=5, projection_backlog_alert_threshold=5,
    )
    result = collect_operational_alerts(FakeSession(), config=config, now=now)
    codes = {item.code for item in result.alerts}
    assert codes == {
        "openrouter_payment_blocked", "public_daily_budget_exhausted", "run_failure_rate_high",
        "usage_reconciliation_stale", "projection_backlog_high", "queued_work_stale",
        "catalog_refresh_stale", "markdown_reconciliation_failed", "worker_unavailable",
        "scheduler_heartbeat_stale",
    }
    assert [item.severity for item in result.alerts] == sorted(
        (item.severity for item in result.alerts), key={"critical": 0, "warning": 1}.get,
    )
    assert all(item.recovery_link.startswith("/admin/") for item in result.alerts)


def test_phase10_golden_suite_covers_contract_and_validates_every_role_fixture() -> None:
    expected = {
        "recommendation_rejection_ambiguity", "long_term_use", "reviewer_disagreement",
        "sponsorship", "prompt_injection", "comment_spam", "isolated_complaint",
        "translated_captions", "unrelated_products", "missing_evidence_timestamps",
        "unsupported_visual_claims", "conflicting_node_versions",
    }
    assert {case.key for case in GOLDEN_CASES} == expected
    for role, spec in AGENT_REGISTRY.items():
        cases = golden_cases(role)
        assert cases
        for case in cases:
            spec.input_model.model_validate(case_fixture(role, case))


def test_local_upstream_mocks_expose_the_phase10_failure_matrix() -> None:
    headers = {
        "Authorization": "Bearer fixture-key",
        "X-Title": "ReviewLens tests",
        "HTTP-Referer": "https://reviewlens.test",
        "X-Request-ID": "phase10-fixture",
    }
    body = {
        "models": ["deepseek/deepseek-v4-flash"],
        "provider": {"require_parameters": True},
        "response_format": {"type": "json_schema", "json_schema": {"name": "phase3_fixture"}},
        "messages": [{"role": "user", "content": "fixture"}],
        "metadata": {},
    }
    expected = {
        "authentication": 401, "payment": 402, "rate_limit": 429, "timeout": 408,
        "upstream_5xx": 500, "provider_unavailable": 503, "model_unavailable": 404,
    }
    with TestClient(openrouter_mock_app) as client:
        for scenario, status in expected.items():
            response = client.post(
                "/api/v1/chat/completions",
                headers=headers,
                json=body | {"metadata": {"phase10_failure": scenario}},
            )
            assert response.status_code == status
        schema = client.post(
            "/api/v1/chat/completions",
            headers=headers,
            json=body | {"metadata": {"phase10_failure": "schema_rejection"}},
        )
        assert schema.status_code == 200
        assert json.loads(schema.json()["choices"][0]["message"]["content"]) == {
            "phase10_invalid": True,
        }

    youtube_headers = {"X-Goog-Api-Key": "fixture-key"}
    with TestClient(youtube_mock_app) as client:
        assert client.get(
            "/youtube/v3/search", params={"q": "fixture"},
            headers=youtube_headers | {"X-Mock-Failure": "search_failure"},
        ).status_code == 503
        assert client.get(
            "/youtube/v3/search", params={"q": "fixture"},
            headers=youtube_headers | {"X-Mock-Failure": "timeout"},
        ).status_code == 408
        assert client.get(
            "/youtube/v3/transcripts", params={"videoId": "fixture01"},
            headers={"X-Mock-Failure": "missing_transcript"},
        ).status_code == 404
        assert client.get(
            "/youtube/v3/commentThreads", params={"videoId": "fixture01"},
            headers=youtube_headers | {"X-Mock-Failure": "comments_unavailable"},
        ).status_code == 403
