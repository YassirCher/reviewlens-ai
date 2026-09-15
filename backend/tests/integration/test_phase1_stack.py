from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError

from app.cache import get_redis
from app.config import settings
from app.db.models import (
    AdminSession,
    AgentDefinition,
    AgentVersion,
    AgentVersionTool,
    AuditEvent,
    ModelPolicy,
    ModelPolicyVersion,
    ToolDefinition,
    ToolVersion,
)
from app.db.session import get_engine, session_scope
from app.main import app
from app.platform.health import collect_health
from app.seed import seed_foundation
from app.security import keyed_hash

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1",
    reason="set REVIEWLENS_RUN_INTEGRATION=1 inside the isolated Compose test stack",
)


def test_empty_database_migration_is_current_and_complete() -> None:
    config = AlembicConfig("alembic.ini")
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())
    with get_engine().connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
        tables = set(inspect(connection).get_table_names())
    assert current_heads == expected_heads == {"20260915_0003"}
    assert {
        "admin_users",
        "admin_sessions",
        "anonymous_sessions",
        "audit_events",
        "agent_versions",
        "workflow_versions",
        "tool_versions",
        "model_policy_versions",
        "embedding_policy_versions",
        "budget_policy_versions",
        "active_configuration",
        "configuration_snapshots",
        "analysis_runs",
        "run_budget_states",
        "task_runs",
        "task_dependencies",
        "task_attempts",
        "progress_events",
        "runtime_outbox",
    } <= tables


def test_seed_is_idempotent() -> None:
    assert seed_foundation() == "already_seeded"
    assert seed_foundation() == "already_seeded"


def test_real_admin_session_csrf_logout_and_audit() -> None:
    password = os.environ["PHASE1_TEST_ADMIN_PASSWORD"]
    get_redis().flushdb()
    with TestClient(app) as client:
        login = client.post(
            "/api/v2/admin/session",
            json={"email": os.environ["ADMIN_EMAIL"], "password": password},
        )
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert password not in login.text

        current = client.get("/api/v2/admin/session")
        assert current.status_code == 200
        csrf = client.get("/api/v2/admin/csrf")
        assert csrf.status_code == 200

        rejected = client.delete(
            "/api/v2/admin/session",
            headers={"X-CSRF-Token": "invalid"},
        )
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "csrf_validation_failed"

        logout = client.delete(
            "/api/v2/admin/session",
            headers={"X-CSRF-Token": csrf.json()["csrf_token"]},
        )
        assert logout.status_code == 204
        assert client.get("/api/v2/admin/session").status_code == 401

    with session_scope() as db:
        actions = set(db.scalars(select(AuditEvent.action)))
    assert {"system.admin_seeded", "admin.login_succeeded", "admin.logout"} <= actions


def test_login_throttling_is_generic_and_redis_backed() -> None:
    get_redis().flushdb()
    with TestClient(app) as client:
        for _ in range(5):
            response = client.post(
                "/api/v2/admin/session",
                json={"email": "missing@example.test", "password": "incorrect"},
            )
            assert response.status_code == 401
            assert response.json()["error"]["message"] == "Sign-in failed."
        throttled = client.post(
            "/api/v2/admin/session",
            json={"email": "missing@example.test", "password": "incorrect"},
        )
    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"]
    assert "missing@example.test" not in throttled.text


def test_session_rotation_expiry_and_revocation_are_persistent_and_audited() -> None:
    get_redis().flushdb()
    password = os.environ["PHASE1_TEST_ADMIN_PASSWORD"]
    with TestClient(app) as client:
        assert client.post(
            "/api/v2/admin/session",
            json={"email": os.environ["ADMIN_EMAIL"], "password": password},
        ).status_code == 200
        old_token = client.cookies.get(settings.admin_session_cookie)

        assert client.post(
            "/api/v2/admin/session",
            json={"email": os.environ["ADMIN_EMAIL"], "password": password},
        ).status_code == 200
        current_token = client.cookies.get(settings.admin_session_cookie)
        assert old_token and current_token and old_token != current_token

        with TestClient(app) as old_client:
            old_client.cookies.set(settings.admin_session_cookie, old_token)
            assert old_client.get("/api/v2/admin/session").status_code == 401

        with session_scope() as db:
            current = db.scalar(
                select(AdminSession).where(
                    AdminSession.token_hash == keyed_hash(current_token, settings.session_secret)
                )
            )
            assert current is not None
            current.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        assert client.get("/api/v2/admin/session").status_code == 401

    with session_scope() as db:
        actions = set(db.scalars(select(AuditEvent.action)))
    assert {"admin.session_rotated", "admin.session_revoked"} <= actions


def test_published_configuration_and_audit_rows_are_database_immutable() -> None:
    with session_scope() as db:
        definition = ModelPolicy(
            key=f"test-policy-{uuid.uuid4()}",
            name="Test policy",
            description="Integration-only fixture",
        )
        db.add(definition)
        db.flush()
        version = ModelPolicyVersion(
            definition_id=definition.id,
            version_number=1,
            lifecycle="published",
            content_hash="a" * 64,
            change_note="test",
            policy={"models": []},
        )
        db.add(version)

    with pytest.raises(DBAPIError), session_scope() as db:
        version = db.scalar(
            select(ModelPolicyVersion).where(ModelPolicyVersion.content_hash == "a" * 64)
        )
        version.change_note = "forbidden mutation"

    with pytest.raises(DBAPIError), session_scope() as db:
        event = db.scalar(select(AuditEvent).order_by(AuditEvent.created_at).limit(1))
        event.action = "forbidden mutation"


def test_published_agent_tool_relations_are_database_immutable() -> None:
    with session_scope() as db:
        agent = AgentDefinition(key=f"agent-{uuid.uuid4()}", name="Test agent", description="test")
        tool = ToolDefinition(key=f"tool-{uuid.uuid4()}", name="Test tool", description="test")
        db.add_all((agent, tool))
        db.flush()
        agent_version = AgentVersion(
            definition_id=agent.id,
            version_number=1,
            lifecycle="draft",
            content_hash="b" * 64,
            change_note="test",
            system_prompt="test",
            output_schema={},
            retrieval_policy={},
        )
        tool_version = ToolVersion(
            definition_id=tool.id,
            version_number=1,
            lifecycle="published",
            content_hash="c" * 64,
            change_note="test",
            input_schema={},
            output_schema={},
            capability_metadata={},
            limits={},
            risk_class="read_only",
        )
        db.add_all((agent_version, tool_version))
        db.flush()
        db.add(AgentVersionTool(agent_version_id=agent_version.id, tool_version_id=tool_version.id))
        db.flush()
        agent_version.lifecycle = "published"
        agent_version.published_at = datetime.now(timezone.utc)
        agent_version_id = agent_version.id

    with session_scope() as db:
        assert db.get(AgentVersion, agent_version_id).lifecycle == "published"

    with pytest.raises(DBAPIError), session_scope() as db:
        relation = db.scalar(
            select(AgentVersionTool).where(AgentVersionTool.agent_version_id == agent_version_id)
        )
        db.delete(relation)


def test_complete_dependencies_report_ready() -> None:
    report = collect_health()
    assert report.status == "ready"
    assert all(item.available for item in report.dependencies.values())
