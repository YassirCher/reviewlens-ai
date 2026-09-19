from __future__ import annotations

import asyncio
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.analysis.configuration import seed_analysis_configuration
from app.cache import get_redis
from app.config import settings
from app.db.models import ActiveConfiguration, AnonymousSession, BudgetPolicyVersion, ReportPublication, RunSubmission
from app.db.session import session_scope
from app.llmops.catalog import refresh_catalogs
from app.main import app
from app.api.v2.dependencies import get_v2_redis
from app.public.admission import _rate_keys, _reserve_rate
from app.runtime.outbox import relay_runtime_outbox, stream_key

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1"
    or settings.app_env != "test"
    or settings.openrouter_base_url != "http://openrouter-mock:8089/api/v1",
    reason="requires isolated Compose and mocked OpenRouter/YouTube services",
)


def _seed() -> None:
    asyncio.run(refresh_catalogs())
    with session_scope() as db:
        seeded = seed_analysis_configuration(db)
        active = db.get(ActiveConfiguration, 1)
        active.workflow_version_id = uuid.UUID(seeded["workflow_version_id"])
        active.budget_policy_version_id = uuid.UUID(seeded["budget_policy_version_id"])
        active.kill_switch = False


def test_phase7_public_lifecycle_and_revocation() -> None:
    _seed()
    with session_scope() as db:
        active = db.get(ActiveConfiguration, 1)
        assert active is not None
        policy = db.get(BudgetPolicyVersion, active.budget_policy_version_id)
        assert policy is not None and policy.public_queue_capacity == settings.public_queue_capacity
    origin = {"Origin": "http://localhost:3000"}
    product = {"product_name": "Phase 6 complete fixture", "video_count": 5, "analyze_comments": False}
    with TestClient(app) as owner:
        preflight = owner.post("/api/v2/analyses/preflight", json=product, headers=origin)
        assert preflight.status_code == 200, preflight.text
        assert preflight.json()["allowed"] is True
        assert settings.anonymous_session_cookie in owner.cookies
        key = "phase7-public-test-key-0001"
        created = owner.post("/api/v2/analyses", json=product, headers={**origin, "Idempotency-Key": key})
        assert created.status_code == 202, created.text
        run_id = uuid.UUID(created.json()["run_id"])
        replay = owner.post("/api/v2/analyses", json=product, headers={**origin, "Idempotency-Key": key})
        assert replay.status_code == 202 and replay.json()["run_id"] == str(run_id)
        conflict = owner.post("/api/v2/analyses", json={**product, "video_count": 3}, headers={**origin, "Idempotency-Key": key})
        assert conflict.status_code == 409
        with TestClient(app) as outsider:
            unauthorized = outsider.get(f"/api/v2/analyses/{run_id}")
            missing = outsider.get(f"/api/v2/analyses/{uuid.uuid4()}")
            assert unauthorized.status_code == missing.status_code == 404
            assert unauthorized.json()["error"]["message"] == missing.json()["error"]["message"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            relay_runtime_outbox(run_id=run_id)
            status = owner.get(f"/api/v2/analyses/{run_id}")
            assert status.status_code == 200, status.text
            if status.json()["status"] in {"complete", "partial", "failed", "cancelled"}:
                break
            time.sleep(1)
        else:
            pytest.fail("public workflow did not finish")
        state = status.json()
        assert state["status"] == "complete", state
        assert state["source_count_analyzed"] >= 1
        assert state["total_tokens"] > 0
        report_url = state["report_url"]
        assert report_url and report_url.startswith("/api/v2/reports/")
        report = owner.get(report_url)
        assert report.status_code == 200, report.text
        assert report.json()["total_tokens"] > 0
        assert report.json()["sources"]
        assert "cost_microusd" not in report.text
        assert "model_policy" not in report.text
        assert report.headers["x-robots-tag"].startswith("noindex")
        with TestClient(app) as shared:
            assert shared.get(report_url).status_code == 200
            assert shared.get("/api/v2/reports/" + "A" * 43).status_code == 404
        graph = owner.get(report_url + "/graph", params={"limit": 2})
        assert graph.status_code == 200, graph.text
        assert graph.json()["nodes"]
        assert len(graph.json()["nodes"]) <= 2
        assert "transcript_chunk" not in graph.text
        cursor = graph.json()["next_cursor"]
        if cursor:
            assert owner.get(report_url + "/graph", params={"cursor": cursor, "limit": 2}).status_code == 200
            assert owner.get(report_url + "/graph", params={"cursor": cursor + "X"}).status_code == 422
        get_redis().delete(stream_key(run_id))
        last = owner.get(f"/api/v2/analyses/{run_id}/events", headers={"Last-Event-ID": str(state["progress_sequence"] - 1)})
        assert last.status_code == 200
        assert f"id: {state['progress_sequence']}" in last.text
        assert owner.get(f"/api/v2/analyses/{run_id}/events", headers={"Last-Event-ID": "bad"}).status_code == 422

        with session_scope() as db:
            publication = db.scalar(select(ReportPublication).where(ReportPublication.run_id == run_id))
            assert publication is not None and publication.revoked_at is None
            assert publication.token_hash not in report_url
            publication_id = publication.id
            report_id = publication.report_id
        with pytest.raises(DBAPIError), session_scope() as db:
            publication = db.get(ReportPublication, publication_id)
            publication.payload = {"tampered": True}

        password = os.environ["PHASE1_TEST_ADMIN_PASSWORD"]
        login = owner.post("/api/v2/admin/session", json={"email": settings.admin_email, "password": password})
        assert login.status_code == 200, login.text
        csrf = owner.get("/api/v2/admin/csrf").json()["csrf_token"]
        revoked = owner.post(f"/api/v2/admin/reports/{report_id}/revoke", headers={"X-CSRF-Token": csrf})
        assert revoked.status_code == 204, revoked.text
        assert owner.get(report_url).status_code == 404
        assert owner.get(report_url + "/graph").status_code == 404
        assert owner.get(f"/api/v2/analyses/{run_id}").json()["report_url"] is None

        with session_scope() as db:
            session = db.scalar(select(AnonymousSession).where(AnonymousSession.id.is_not(None)).order_by(AnonymousSession.created_at))
            assert session is not None
            session.revoked_at = session.last_seen_at
        owner.cookies.delete(settings.admin_session_cookie)
        assert owner.get(f"/api/v2/analyses/{run_id}").status_code == 404


def test_phase7_redis_admission_is_atomic() -> None:
    _seed()
    redis = get_redis()
    with session_scope() as db:
        active = db.get(ActiveConfiguration, 1)
        assert active is not None
        policy = db.get(BudgetPolicyVersion, active.budget_policy_version_id)
        assert policy is not None
        session_id = uuid.uuid4()
        ip_hash = uuid.uuid4().hex * 2
        keys = _rate_keys(ip_hash, session_id)

        def reserve(index: int) -> bool:
            try:
                _reserve_rate(redis, policy, ip_hash, session_id, f"test-{index}")
                return True
            except Exception:
                return False

        try:
            with ThreadPoolExecutor(max_workers=6) as executor:
                results = list(executor.map(reserve, range(policy.public_runs_per_hour + 3)))
            assert results.count(True) == policy.public_runs_per_hour
        finally:
            redis.delete(*keys)


def test_phase7_concurrent_same_key_creates_one_run() -> None:
    _seed()
    origin = {"Origin": "http://localhost:3000"}
    product = {"product_name": "Phase 6 cancel fixture", "video_count": 3}
    key = "phase7-concurrent-key-0001"
    with TestClient(app) as bootstrap:
        initial = bootstrap.post("/api/v2/analyses/preflight", json=product, headers=origin)
        assert initial.status_code == 200
        cookie = bootstrap.cookies.get(settings.anonymous_session_cookie)
        assert cookie

    def submit(_: int) -> tuple[int, str]:
        with TestClient(app) as client:
            client.cookies.set(settings.anonymous_session_cookie, cookie)
            response = client.post("/api/v2/analyses", json=product, headers={**origin, "Idempotency-Key": key})
            return response.status_code, response.json().get("run_id", "")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))
    assert all(status == 202 for status, _ in results), results
    assert results[0][1] == results[1][1]
    with session_scope() as db:
        assert db.scalar(select(func.count(RunSubmission.id)).where(RunSubmission.idempotency_key == key)) == 1


def test_phase7_kill_switch_admin_bypass_and_redis_fail_closed() -> None:
    _seed()
    origin = {"Origin": "http://localhost:3000"}
    product = {"product_name": "Phase 6 cancel fixture", "video_count": 3}
    with session_scope() as db:
        active = db.get(ActiveConfiguration, 1)
        assert active is not None
        active.public_analysis_enabled = False
    try:
        with TestClient(app) as client:
            denied = client.post("/api/v2/analyses/preflight", json=product, headers=origin)
            assert denied.status_code == 200 and denied.json()["allowed"] is False
            blocked = client.post("/api/v2/analyses", json=product, headers={**origin, "Idempotency-Key": "phase7-disabled-public-001"})
            assert blocked.status_code == 503

            login = client.post("/api/v2/admin/session", json={"email": settings.admin_email, "password": os.environ["PHASE1_TEST_ADMIN_PASSWORD"]})
            assert login.status_code == 200
            csrf = client.get("/api/v2/admin/csrf").json()["csrf_token"]
            admin = client.post("/api/v2/admin/analyses", json=product, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "phase7-admin-run-key-001"})
            assert admin.status_code == 202, admin.text
            run_id = admin.json()["run_id"]
            assert client.post(f"/api/v2/analyses/{run_id}/cancel", headers={"X-CSRF-Token": csrf}).status_code in {200, 202}
            with session_scope() as db:
                active = db.get(ActiveConfiguration, 1)
                active.kill_switch = True
            blocked_admin = client.post("/api/v2/admin/analyses", json=product, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "phase7-admin-run-key-002"})
            assert blocked_admin.status_code == 503
    finally:
        with session_scope() as db:
            active = db.get(ActiveConfiguration, 1)
            active.public_analysis_enabled = True
            active.kill_switch = False

    class BrokenRedis:
        def ping(self) -> None:
            raise RedisError("fixture outage")

    app.dependency_overrides[get_v2_redis] = lambda: BrokenRedis()
    try:
        with TestClient(app) as client:
            unavailable = client.post("/api/v2/analyses/preflight", json=product, headers=origin)
            assert unavailable.status_code == 503
            assert unavailable.json()["error"]["code"] == "admission_unavailable"
    finally:
        app.dependency_overrides.pop(get_v2_redis, None)
