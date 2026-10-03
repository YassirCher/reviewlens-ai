from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.errors import V2Error
from app.main import app
from app.public.admission import _limits, _quota_decision, _recovery, _reserve_rate
from app.config import Settings


NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
POLICY = SimpleNamespace(public_runs_per_hour=3, public_runs_per_day=10, public_concurrent_runs=2)


def test_kill_switch_has_paused_recovery_without_an_invented_retry_time():
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(kill_switch=True)
    with pytest.raises(V2Error) as caught:
        _limits(db, Settings())
    assert caught.value.status_code == 503
    assert caught.value.details["recovery"] == {"reasons": ["paused"], "retry_at": None, "retry_after_seconds": None}
    assert not caught.value.headers


def test_overlapping_rolling_limits_wait_for_every_expiring_window():
    db, redis = MagicMock(), MagicMock()
    db.scalar.side_effect = [4, 12, 10, 2, NOW - timedelta(minutes=20), NOW - timedelta(hours=2), NOW - timedelta(minutes=10)]
    redis.zcount.return_value = 0
    remaining, reasons, resets = _quota_decision(db, redis, POLICY, uuid.uuid4(), "hashed-ip", NOW)
    recovery = _recovery(NOW, reasons, resets)
    assert remaining == dict(hourly_remaining=0, daily_ip_remaining=0, daily_session_remaining=0, concurrent_remaining=0)
    assert recovery["reasons"] == ["hourly", "daily_ip", "daily_session", "concurrent"]
    assert datetime.fromisoformat(recovery["retry_at"]) == NOW + timedelta(hours=23, minutes=50)
    assert recovery["retry_after_seconds"] == 85800
    # Above-limit windows must expire enough records, not only the oldest one.
    queries = [call.args[0].compile().params for call in db.scalar.call_args_list[4:]]
    assert queries[0]["param_2"] == 1 and queries[1]["param_2"] == 2


def test_redis_reservation_can_block_preflight_before_a_submission_is_committed():
    db, redis = MagicMock(), MagicMock()
    db.scalar.side_effect = [0, 0, 0, 0]
    redis.zcount.side_effect = [3, 0, 0]
    redis.zrangebyscore.return_value = [("reservation", (NOW - timedelta(minutes=59)).timestamp())]
    remaining, reasons, resets = _quota_decision(db, redis, POLICY, uuid.uuid4(), "hashed-ip", NOW)
    assert remaining["hourly_remaining"] == 0
    assert _recovery(NOW, reasons, resets)["retry_after_seconds"] == 60


@pytest.mark.parametrize("reason,seconds", [("concurrent", None), ("paused", None), ("run_budget", None), ("queue", 60)])
def test_dynamic_limits_do_not_invent_a_reset_time(reason, seconds):
    recovery = _recovery(NOW, [reason])
    assert recovery == {"reasons": [reason], "retry_at": None, "retry_after_seconds": seconds}


def test_atomic_reservation_error_retains_limiting_reasons_and_retry_header(monkeypatch):
    monkeypatch.setattr("app.public.admission.utc_now", lambda: NOW)
    redis = MagicMock()
    redis.eval.return_value = [int(NOW.timestamp()) + 120, 1, 0, 1]
    with pytest.raises(V2Error) as caught:
        _reserve_rate(redis, POLICY, "hashed-ip", uuid.uuid4(), "reservation")
    assert caught.value.headers == {"Retry-After": "120"}
    assert caught.value.details["recovery"]["reasons"] == ["hourly", "daily_session"]
    assert caught.value.status_code == 429


def test_cors_exposes_recovery_header_and_rejects_unrelated_origins():
    with TestClient(app) as client:
        allowed = client.options("/api/v2/analyses", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
        assert allowed.status_code == 200
        response = client.get("/health/live", headers={"Origin": "http://localhost:3000"})
        assert "retry-after" in response.headers["access-control-expose-headers"].lower()
        denied = client.options("/api/v2/analyses", headers={"Origin": "https://unrelated.invalid", "Access-Control-Request-Method": "POST"})
        assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers
