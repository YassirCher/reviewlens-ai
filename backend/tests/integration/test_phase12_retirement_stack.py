from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin.retirement import build_evidence_document, validate_evidence_document
from app.analysis.configuration import seed_analysis_configuration
from app.config import settings
from app.db.models import (
    ActiveConfiguration,
    AdminUser,
    CompatibilityRequest,
    CutoverObservation,
    UsageEvent,
)
from app.db.session import session_scope
from app.llmops.catalog import refresh_catalogs
from app.main import app
from app.runtime.outbox import relay_runtime_outbox

FLASH_MODELS = (
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-flash-0731",
)

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1"
    or settings.app_env != "test"
    or settings.openrouter_base_url != "http://openrouter-mock:8089/api/v1",
    reason="requires the isolated Phase 12 Compose stack",
)


def _passed_result(now: datetime) -> dict:
    codes = (
        "window_age",
        "terminal_runs",
        "compatibility_requests",
        "failure_rate",
        "p95_run_latency",
        "central_claim_evidence",
        "compatibility_mapping",
        "unresolved_usage",
        "budget_breaches",
    )
    return {
        "observed_at": now.isoformat(),
        "ready": True,
        "blockers": [],
        "metrics": [
            {
                "code": code,
                "observed_value": 0,
                "threshold": 0,
                "passed": True,
                "unit": "count",
            }
            for code in codes
        ],
        "samples": {"terminal_runs": 20, "compatibility_requests": 5},
        "distributions": {
            "total_tokens": 30_000,
            "p95_tokens_per_run": 2_000,
            "total_cost_microusd": 90_000,
            "p95_cost_microusd_per_run": 8_000,
        },
    }


def test_phase12_production_evidence_export_and_history_are_read_only() -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        admin = db.scalar(select(AdminUser).where(AdminUser.identifier == settings.admin_email))
        assert admin is not None
        observation = CutoverObservation(
            environment="production",
            test_evidence=False,
            status="passed",
            root_mode="v2",
            thresholds={
                "stable_window_hours": 24,
                "min_terminal_runs": 20,
                "min_compatibility_requests": 5,
                "max_failure_rate": 0.10,
                "max_p95_run_latency_seconds": 900,
                "min_compatibility_success_rate": 0.95,
                "public_run_token_cap": 250_000,
                "public_run_cost_cap_microusd": 500_000,
            },
            latest_result=_passed_result(now - timedelta(hours=48)),
            change_note="Phase 12 integration evidence fixture",
            started_by_admin_id=admin.id,
            started_at=now - timedelta(hours=72),
            evaluated_at=now - timedelta(hours=48),
            ended_at=now - timedelta(hours=48),
        )
        db.add(observation)
        db.flush()
        for index in range(5):
            db.add(
                CompatibilityRequest(
                    request_id=uuid.uuid4(),
                    endpoint="/api/analyze",
                    transport="sync",
                    status="complete",
                    http_status=200,
                    mapped_response=True,
                    started_at=now - timedelta(hours=25, minutes=index),
                    completed_at=now - timedelta(hours=25, minutes=index) + timedelta(seconds=1),
                    duration_ms=1_000,
                )
            )
        db.flush()
        document = build_evidence_document(
            db,
            observation.id,
            attestation_reference="change/PHASE12-INTEGRATION",
            now=now,
        )
        observation_id = observation.id
    payload = validate_evidence_document(document, now=now)
    serialized = str(document).casefold()
    assert payload["observation"]["id"] == str(observation_id)
    assert "prompt" not in serialized
    assert "cookie" not in serialized
    assert "credential" not in serialized

    with TestClient(app) as client:
        login = client.post(
            "/api/v2/admin/session",
            json={
                "email": settings.admin_email,
                "password": os.environ["PHASE1_TEST_ADMIN_PASSWORD"],
            },
        )
        assert login.status_code == 200, login.text
        history = client.get("/api/v2/admin/cutover")
        assert history.status_code == 200, history.text
        assert history.json()["phase"] == "retired"
        assert history.json()["retirement_authorized"] is True
        for path in (
            "/api/v2/admin/cutover/observations",
            f"/api/v2/admin/cutover/observations/{observation_id}/evaluate",
            f"/api/v2/admin/cutover/observations/{observation_id}/record-rollback",
        ):
            assert client.post(path, json={}).status_code in {404, 405}


def test_phase12_each_approved_flash_model_completes_an_attributed_v2_run() -> None:
    completed_runs: dict[str, uuid.UUID] = {}
    with session_scope() as db:
        active = db.get(ActiveConfiguration, 1)
        assert active is not None
        original_workflow = active.workflow_version_id
        original_budget = active.budget_policy_version_id
    try:
        for index, model in enumerate(FLASH_MODELS):
            model_settings = settings.model_copy(update={"v2_agent_chat_models": model})
            asyncio.run(refresh_catalogs(config=model_settings))
            with session_scope() as db:
                seeded = seed_analysis_configuration(db, config=model_settings)
                active = db.get(ActiveConfiguration, 1)
                assert active is not None
                active.workflow_version_id = uuid.UUID(seeded["workflow_version_id"])
                active.budget_policy_version_id = uuid.UUID(seeded["budget_policy_version_id"])
                active.kill_switch = False

            origin = {"Origin": "http://localhost:3000"}
            product = {
                "product_name": "Phase 6 complete fixture",
                "video_count": 3,
                "analyze_comments": False,
            }
            with TestClient(app) as client:
                preflight = client.post("/api/v2/analyses/preflight", json=product, headers=origin)
                assert preflight.status_code == 200, preflight.text
                assert "Path=/api/v2" in preflight.headers.get("set-cookie", "")
                created = client.post(
                    "/api/v2/analyses",
                    json=product,
                    headers={**origin, "Idempotency-Key": f"phase12-{index}-flash-model-run"},
                )
                assert created.status_code == 202, created.text
                run_id = uuid.UUID(created.json()["run_id"])
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    relay_runtime_outbox(run_id=run_id)
                    status = client.get(f"/api/v2/analyses/{run_id}")
                    assert status.status_code == 200, status.text
                    if status.json()["status"] in {"complete", "partial", "failed", "cancelled"}:
                        break
                    time.sleep(1)
                else:
                    pytest.fail(f"Phase 12 run for {model} did not finish")
                assert status.json()["status"] == "complete", status.json()
                completed_runs[model] = run_id

            with session_scope() as db:
                events = list(db.scalars(select(UsageEvent).where(UsageEvent.run_id == run_id)))
                assert events
                assert all(event.requested_models == [model] for event in events)
                assert all(event.actual_model == model for event in events)
                assert all(event.task_attempt_id and event.model_policy_version_id for event in events)
    finally:
        with session_scope() as db:
            active = db.get(ActiveConfiguration, 1)
            if active is not None:
                active.workflow_version_id = original_workflow
                active.budget_policy_version_id = original_budget
    assert set(completed_runs) == set(FLASH_MODELS)
