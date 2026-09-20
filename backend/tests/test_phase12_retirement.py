from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.admin.retirement import RetirementEvidenceError, SCHEMA, validate_evidence_document
from app.main import app
from tests.openrouter_mock import app as openrouter_mock_app

FLASH_0423 = "deepseek/deepseek-v4-flash"
FLASH_0731 = "deepseek/deepseek-v4-flash-0731"


def _document() -> dict:
    started = datetime(2026, 9, 18, tzinfo=timezone.utc)
    ended = started + timedelta(hours=24)
    exported = ended + timedelta(hours=24, minutes=1)
    metric_codes = (
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
    payload = {
        "exported_at": exported.isoformat(),
        "observation": {
            "id": "11111111-1111-4111-8111-111111111111",
            "environment": "production",
            "test_evidence": False,
            "status": "passed",
            "root_mode": "v2",
            "thresholds": {
                "stable_window_hours": 24,
                "min_terminal_runs": 20,
                "min_compatibility_requests": 5,
                "max_failure_rate": 0.10,
                "max_p95_run_latency_seconds": 900,
                "min_compatibility_success_rate": 0.95,
            },
            "result": {
                "observed_at": ended.isoformat(),
                "ready": True,
                "blockers": [],
                "metrics": [
                    {"code": code, "observed_value": 1, "threshold": 1, "passed": True, "unit": "count"}
                    for code in metric_codes
                ],
                "samples": {"terminal_runs": 20, "compatibility_requests": 5},
                "distributions": {},
            },
            "started_at": started.isoformat(),
            "evaluated_at": ended.isoformat(),
            "ended_at": ended.isoformat(),
            "version": 2,
        },
        "compatibility": {
            "last_request_at": ended.isoformat(),
            "quiet_period_hours": 24,
            "observed_quiet_hours": 24.0167,
        },
        "attestation": {
            "confirmed_no_active_consumers": True,
            "reference": "change/RL-PHASE12",
        },
        "audit_events": [],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return {"schema": SCHEMA, "payload": payload, "sha256": digest}


def _redigest(document: dict) -> None:
    document["sha256"] = hashlib.sha256(
        json.dumps(document["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def test_retirement_evidence_accepts_only_production_floors_and_quiet_period() -> None:
    document = _document()
    validation_time = datetime.fromisoformat(document["payload"]["exported_at"]) + timedelta(hours=1)
    assert validate_evidence_document(document, now=validation_time)["attestation"]["reference"] == "change/RL-PHASE12"

    for mutate, message in (
        (lambda value: value["payload"]["observation"].update(test_evidence=True), "non-test"),
        (
            lambda value: value["payload"]["observation"]["thresholds"].update(min_terminal_runs=19),
            "weakened",
        ),
        (
            lambda value: value["payload"]["compatibility"].update(last_request_at=value["payload"]["exported_at"]),
            "quiet period",
        ),
    ):
        invalid = deepcopy(document)
        mutate(invalid)
        _redigest(invalid)
        with pytest.raises(RetirementEvidenceError, match=message):
            validate_evidence_document(invalid, now=validation_time)

    stale = deepcopy(document)
    stale["payload"]["observation"]["result"]["observed_at"] = (
        datetime.fromisoformat(stale["payload"]["observation"]["ended_at"]) - timedelta(minutes=1)
    ).isoformat()
    _redigest(stale)
    with pytest.raises(RetirementEvidenceError, match="stale"):
        validate_evidence_document(stale, now=validation_time)


def test_retirement_evidence_rejects_tampering() -> None:
    document = _document()
    document["payload"]["attestation"]["reference"] = "changed"
    with pytest.raises(RetirementEvidenceError, match="digest"):
        validate_evidence_document(
            document,
            now=datetime.fromisoformat(document["payload"]["exported_at"]) + timedelta(hours=1),
        )


def test_v1_routes_are_absent_and_v2_health_remains() -> None:
    with TestClient(app) as client:
        assert client.get("/health").status_code == 404
        assert client.get("/api/config").status_code == 404
        assert client.post("/api/analyze", json={"product_name": "Camera"}).status_code == 404
        assert client.post("/api/analyze/stream", json={"product_name": "Camera"}).status_code == 404
        assert client.get("/health/live").json() == {"status": "ok", "service": "reviewlens-api"}


def test_openrouter_mock_allows_exactly_the_two_pinned_flash_models(monkeypatch) -> None:
    monkeypatch.setenv("PHASE12_ALLOWED_INFERENCE_MODELS", f"{FLASH_0423},{FLASH_0731}")
    headers = {
        "Authorization": "Bearer fixture",
        "X-Title": "ReviewLens tests",
        "HTTP-Referer": "https://reviewlens.test",
        "X-Request-ID": "phase12-model-fixture",
    }
    body = {
        "provider": {"require_parameters": True},
        "response_format": {"type": "json_schema", "json_schema": {"name": "phase3_fixture"}},
        "messages": [{"role": "user", "content": "fixture"}],
        "metadata": {},
    }
    with TestClient(openrouter_mock_app) as client:
        client.post("/history/reset")
        for slug in (FLASH_0423, FLASH_0731):
            response = client.post(
                "/api/v1/chat/completions",
                headers=headers,
                json=body | {"models": [slug]},
            )
            assert response.status_code == 200, response.text
        for slug in (
            "~deepseek/deepseek-v4-flash-latest",
            "deepseek/deepseek-v4.1-flash",
            "deepseek/deepseek-v4-flash-vision-exp",
            "deepseek/deepseek-v4-pro",
            "fixture/chat-model",
        ):
            response = client.post(
                "/api/v1/chat/completions",
                headers=headers,
                json=body | {"models": [slug]},
            )
            assert response.status_code == 400
        history = client.get("/history").json()["inference"]
    assert {(item["model"], item["calls"]) for item in history} == {(FLASH_0423, 1), (FLASH_0731, 1)}
