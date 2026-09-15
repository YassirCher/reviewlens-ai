from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.v2 import auth as auth_routes
from app.api.v2.dependencies import get_v2_db, get_v2_redis
from app.config import Settings
from app.db.base import Base
from app.main import app
from app.platform import health as health_module
from app.platform.health import collect_health
from app.security import generate_opaque_token, hash_password, keyed_hash, verify_password
from app.services.admin_auth import SessionTokens


def _valid_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspaces"
    quarantine = tmp_path / "quarantine"
    workspace.mkdir()
    quarantine.mkdir()
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:pass@localhost/db",
        redis_url="redis://localhost/0",
        session_secret="s" * 32,
        public_token_hash_secret="p" * 32,
        rate_limit_hash_secret="r" * 32,
        node_storage_root=str(workspace),
        node_quarantine_root=str(quarantine),
        neo4j_uri="bolt://neo4j:7687",
        neo4j_username="neo4j",
        neo4j_password="test-only",
        qdrant_url="http://qdrant:6333",
        celery_broker_url="",
        youtube_api_key="",
        openrouter_api_key="",
    )


def test_security_primitives_hash_passwords_and_tokens() -> None:
    password_hash = hash_password("a-long-test-password")
    assert password_hash.startswith("$argon2id$")
    assert verify_password("a-long-test-password", password_hash)
    assert not verify_password("wrong-password", password_hash)

    token = generate_opaque_token()
    assert len(token) >= 43
    assert token not in keyed_hash(token, "s" * 32)


def test_v2_configuration_validation_is_role_specific(tmp_path: Path) -> None:
    config = _valid_settings(tmp_path)
    assert config.v2_configuration_errors("api") == []
    worker_errors = config.v2_configuration_errors("worker")
    assert "CELERY_BROKER_URL is required" in worker_errors
    assert "YOUTUBE_API_KEY is required" in worker_errors
    assert "OPENROUTER_API_KEY is required" in worker_errors

    weak = config.model_copy(update={"session_secret": "short"})
    assert "SESSION_SECRET must contain at least 32 bytes" in weak.v2_configuration_errors("api")

    plaintext = config.model_copy(update={"admin_password": "must-never-be-accepted"})
    assert "ADMIN_PASSWORD is forbidden; use ADMIN_PASSWORD_HASH" in plaintext.v2_configuration_errors("api")

    wildcard_cors = config.model_copy(update={"backend_cors_origins": "*"})
    assert any("cannot contain '*'" in error for error in wildcard_cors.v2_configuration_errors("api"))


def test_phase1_schema_remains_present_beside_phase2_runtime_tables() -> None:
    phase1_tables = {
        "admin_users",
        "admin_sessions",
        "anonymous_sessions",
        "audit_events",
        "agent_definitions",
        "agent_versions",
        "agent_version_tools",
        "workflow_definitions",
        "workflow_versions",
        "tool_definitions",
        "tool_versions",
        "model_policies",
        "model_policy_versions",
        "embedding_policies",
        "embedding_policy_versions",
        "budget_policies",
        "budget_policy_versions",
        "active_configuration",
    }
    assert phase1_tables <= set(Base.metadata.tables)
    assert {
        "configuration_snapshots",
        "analysis_runs",
        "run_budget_states",
        "task_runs",
        "task_dependencies",
        "task_attempts",
        "progress_events",
        "runtime_outbox",
    } <= set(Base.metadata.tables)
    assert "projection_outbox" not in Base.metadata.tables
    for table_name in ("admin_sessions", "agent_versions", "workflow_versions", "tool_versions"):
        assert {"created_at", "updated_at", "version"} <= set(Base.metadata.tables[table_name].c.keys())


def test_health_distinguishes_ready_degraded_and_not_ready(monkeypatch, tmp_path: Path) -> None:
    config = _valid_settings(tmp_path)
    monkeypatch.setattr(health_module, "_postgres_probe", lambda: "migrations_current")
    monkeypatch.setattr(health_module, "_redis_probe", lambda: None)
    monkeypatch.setattr(health_module, "_neo4j_probe", lambda _: None)
    monkeypatch.setattr(health_module, "_qdrant_probe", lambda _: None)
    assert collect_health(config).status == "ready"

    def unavailable(_: Settings) -> None:
        raise ConnectionError("test-only failure")

    monkeypatch.setattr(health_module, "_qdrant_probe", unavailable)
    degraded = collect_health(config)
    assert degraded.status == "degraded"
    assert degraded.ready

    invalid = config.model_copy(update={"redis_url": ""})
    assert collect_health(invalid).status == "not_ready"


def test_legacy_health_contract_and_security_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "reviewlens-api"}
    assert uuid.UUID(response.headers["X-Request-ID"])
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_v2_validation_uses_safe_error_envelope() -> None:
    def fake_db():
        yield object()

    app.dependency_overrides[get_v2_db] = fake_db
    app.dependency_overrides[get_v2_redis] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.post("/api/v2/admin/session", json={"email": "invalid"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    payload = response.json()["error"]
    assert payload["code"] == "validation_error"
    assert payload["retryable"] is False
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert "password" not in response.text.lower() or "field required" in response.text.lower()


def test_all_v2_http_and_unexpected_errors_use_the_safe_envelope() -> None:
    with TestClient(app) as client:
        missing = client.get("/api/v2/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert missing.json()["error"]["request_id"] == missing.headers["X-Request-ID"]

    def broken_db():
        raise RuntimeError("secret-looking diagnostic must not escape")
        yield

    app.dependency_overrides[get_v2_db] = broken_db
    app.dependency_overrides[get_v2_redis] = lambda: object()
    try:
        with TestClient(app) as client:
            failed = client.post(
                "/api/v2/admin/session",
                json={"email": "admin@example.test", "password": "not-returned"},
            )
    finally:
        app.dependency_overrides.clear()
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    assert "secret-looking" not in failed.text


def test_login_response_sets_only_an_httponly_cookie(monkeypatch) -> None:
    admin = SimpleNamespace(id=uuid.uuid4(), identifier="admin@example.test")
    session = SimpleNamespace(
        id=uuid.uuid4(),
        expires_at="2026-09-15T13:00:00+00:00",
        absolute_expires_at="2026-09-16T00:00:00+00:00",
    )

    class FakeService:
        def __init__(self, *_args, **_kwargs):
            pass

        def login(self, **_kwargs):
            return SessionTokens(
                session_token="test-session-token",
                csrf_token="test-csrf-token",
                session=session,
                admin=admin,
            )

    def fake_db():
        yield object()

    monkeypatch.setattr(auth_routes, "AdminAuthService", FakeService)
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

    assert response.status_code == 200
    assert "test-session-token" not in response.text
    assert "test-csrf-token" not in response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie
