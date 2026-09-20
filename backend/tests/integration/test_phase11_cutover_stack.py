from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analysis.configuration import seed_analysis_configuration
from app.config import settings
from app.db.models import (
    ActiveConfiguration,
    AnalysisRun,
    CompatibilityRequest,
    UsageEvent,
)
from app.db.session import session_scope
from app.llmops.catalog import refresh_catalogs
from app.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1"
    or settings.app_env != "test"
    or settings.openrouter_base_url != "http://openrouter-mock:8089/api/v1",
    reason="requires the isolated Phase 11 stack and local mocks",
)

DEEPSEEK = "deepseek/deepseek-v4-flash"


def _seed() -> None:
    asyncio.run(refresh_catalogs())
    with session_scope() as db:
        seeded = seed_analysis_configuration(db)
        active = db.get(ActiveConfiguration, 1)
        assert active is not None
        active.workflow_version_id = uuid.UUID(seeded["workflow_version_id"])
        active.budget_policy_version_id = uuid.UUID(seeded["budget_policy_version_id"])
        active.kill_switch = False
        active.public_analysis_enabled = True


def _admin() -> tuple[TestClient, str]:
    client = TestClient(app)
    login = client.post(
        "/api/v2/admin/session",
        json={
            "email": settings.admin_email,
            "password": os.environ["PHASE1_TEST_ADMIN_PASSWORD"],
        },
    )
    assert login.status_code == 200, login.text
    csrf = client.get("/api/v2/admin/csrf")
    assert csrf.status_code == 200, csrf.text
    return client, csrf.json()["csrf_token"]


def _sse_result(body: str) -> dict:
    for frame in body.split("\n\n"):
        lines = frame.splitlines()
        if "event: result" not in lines:
            continue
        raw = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        return json.loads(raw)
    raise AssertionError(f"legacy stream did not produce a result: {body[-1000:]}")


def test_phase11_adapter_observation_and_deepseek_proof() -> None:
    _seed()
    mock_root = settings.openrouter_base_url.removesuffix("/api/v1")
    assert httpx.post(f"{mock_root}/history/reset", timeout=5).status_code == 204

    admin, csrf = _admin()
    with admin:
        assert admin.get("/api/v2/admin/cutover").status_code == 200
        denied = admin.post(
            "/api/v2/admin/cutover/observations",
            json={"confirmation": "start cutover observation"},
        )
        assert denied.status_code == 403
        started = admin.post(
            "/api/v2/admin/cutover/observations",
            json={
                "confirmation": "start cutover observation",
                "change_note": "Phase 11 isolated acceptance",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert started.status_code == 201, started.text
        assert started.json()["test_evidence"] is True
        observation_id = started.json()["id"]

    payload = {
        "product_name": "Phase 11 complete fixture",
        "analyze_comments": False,
        "provider": "openai",
    }
    key = "phase11-compatibility-key-0001"
    origin = {"Origin": "http://localhost:3000", "Idempotency-Key": key}
    with TestClient(app) as legacy:
        streamed = legacy.post("/api/analyze/stream", json=payload, headers=origin)
        assert streamed.status_code == 200, streamed.text
        assert streamed.headers["Deprecation"] == "true"
        assert streamed.headers["Link"] == '</api/v2/analyses>; rel="successor-version"'
        assert settings.anonymous_session_cookie in legacy.cookies
        assert "Path=/api" in streamed.headers["set-cookie"]
        result = _sse_result(streamed.text)
        assert result["provider_used"] == "openrouter"
        assert result["model_used"] == "policy-managed"
        assert result["videos"]
        run_id = uuid.UUID(result["analysis_id"])

        completed = legacy.post(
            "/api/analyze",
            json=payload | {"provider": "xai"},
            headers=origin,
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["analysis_id"] == str(run_id)
        assert "Sunset" not in completed.headers

    with session_scope() as db:
        run = db.get(AnalysisRun, run_id)
        assert run is not None and run.status == "complete"
        assert run.requested_options["entrypoint"] == "v1_stream"
        telemetry = list(
            db.scalars(
                select(CompatibilityRequest)
                .where(CompatibilityRequest.run_id == run_id)
                .order_by(CompatibilityRequest.started_at)
            )
        )
        usage = list(db.scalars(select(UsageEvent).where(UsageEvent.run_id == run_id)))
    assert [item.transport for item in telemetry] == ["stream", "sync"]
    assert all(item.status == "complete" and item.mapped_response for item in telemetry)
    assert usage
    assert all(item.requested_models == [DEEPSEEK] for item in usage)
    assert all(item.actual_model == DEEPSEEK for item in usage)

    history = httpx.get(f"{mock_root}/history", timeout=5).json()["inference"]
    assert history
    assert {(item["operation"], item["model"]) for item in history} == {("chat", DEEPSEEK)}

    minimum_age = settings.cutover_stable_window_hours * 3600
    if minimum_age > 15:
        pytest.fail("isolated Phase 11 thresholds were not shortened")
    time.sleep(minimum_age + 0.5)
    with admin:
        current = admin.get("/api/v2/admin/cutover")
        assert current.status_code == 200
        observation = current.json()["current"]
        evaluated = admin.post(
            f"/api/v2/admin/cutover/observations/{observation_id}/evaluate",
            json={
                "confirmation": "evaluate cutover observation",
                "expected_version": observation["version"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert evaluated.status_code == 200, evaluated.text
        evidence = evaluated.json()
        assert evidence["status"] == "passed", evidence
        assert evidence["result"]["ready"] is True
        assert evidence["result"]["blockers"] == []
        assert evidence["thresholds"]["public_run_token_cap"] > 0
        stale = admin.post(
            f"/api/v2/admin/cutover/observations/{observation_id}/evaluate",
            json={
                "confirmation": "evaluate cutover observation",
                "expected_version": observation["version"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert stale.status_code in {409}
