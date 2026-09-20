"""Shared process and isolated-environment helpers for the current acceptance gate."""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        check=check,
        capture_output=capture_output,
    )


def repository_inventory(paths: tuple[Path, ...]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def restore_inventory(before: dict[Path, bytes | None]) -> list[str]:
    changed: list[str] = []
    for path, content in before.items():
        if (path.read_bytes() if path.exists() else None) == content:
            continue
        changed.append(str(path.relative_to(ROOT)))
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)
    return changed


def create_test_environment(path: Path) -> tuple[str, str, str]:
    sys.path.insert(0, str(BACKEND))
    from app.security import hash_password

    password = secrets.token_urlsafe(24)
    openrouter_key = "phase12-mock-" + secrets.token_urlsafe(18)
    youtube_key = "phase12-youtube-" + secrets.token_urlsafe(18)
    values = {
        "REVIEWLENS_ENV_FILE": path.name,
        "APP_ENV": "test",
        "APP_PUBLIC_URL": "http://localhost:3000",
        "API_PUBLIC_URL": "http://localhost:8000",
        "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000",
        "LOG_LEVEL": "WARNING",
        "ADMIN_EMAIL": "phase12-admin@example.test",
        "ADMIN_PASSWORD_HASH": hash_password(password),
        "PHASE1_TEST_ADMIN_PASSWORD": password,
        "SESSION_SECRET": secrets.token_urlsafe(32),
        "PUBLIC_TOKEN_HASH_SECRET": secrets.token_urlsafe(32),
        "RATE_LIMIT_HASH_SECRET": secrets.token_urlsafe(32),
        "POSTGRES_DB": "reviewlens_phase12_test",
        "POSTGRES_USER": "reviewlens",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "REDIS_PASSWORD": secrets.token_urlsafe(24),
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": secrets.token_urlsafe(24),
        "YOUTUBE_API_KEY": youtube_key,
        "YOUTUBE_BASE_URL": "http://youtube-mock:8090/youtube/v3",
        "YOUTUBE_RETRY_BASE_SECONDS": "0",
        "YOUTUBE_RETRY_MAX_SECONDS": "0",
        "YOUTUBE_LIVE_SMOKE_ENABLED": "false",
        "OPENROUTER_API_KEY": openrouter_key,
        "OPENROUTER_MANAGEMENT_KEY": "",
        "OPENROUTER_BASE_URL": "http://openrouter-mock:8089/api/v1",
        "V2_AGENT_CHAT_MODELS": "deepseek/deepseek-v4-flash",
        "PHASE12_ALLOWED_INFERENCE_MODELS": (
            "deepseek/deepseek-v4-flash,deepseek/deepseek-v4-flash-0731"
        ),
        "V2_AGENT_MAX_CONCURRENCY": "4",
        "V2_ANALYSIS_RUN_TIMEOUT_SECONDS": "900",
        "OPENROUTER_CATALOG_REFRESH_MINUTES": "15",
        "OPENROUTER_CATALOG_STALE_MINUTES": "60",
        "OPENROUTER_MANUAL_REFRESH_COOLDOWN_SECONDS": "10",
        "OPENROUTER_RECONCILIATION_INTERVAL_SECONDS": "10",
        "OPENROUTER_RECONCILIATION_MAX_AGE_MINUTES": "15",
        "OPENROUTER_RECONCILIATION_BATCH_SIZE": "100",
        "OPENROUTER_RETRY_BASE_SECONDS": "0",
        "OPENROUTER_RETRY_MAX_SECONDS": "0",
        "OPENROUTER_LIVE_SMOKE_ENABLED": "false",
        "PUBLIC_ANALYSIS_ENABLED": "true",
        "PUBLIC_RUN_COST_CAP_USD": "20",
        "PUBLIC_DAILY_COST_CAP_USD": "100",
        "CELERY_VISIBILITY_TIMEOUT_SECONDS": "120",
        "RUN_EVENT_STREAM_MAX_LENGTH": "1000",
        "RUNTIME_OUTBOX_BATCH_SIZE": "100",
        "RUNTIME_OUTBOX_LEASE_SECONDS": "20",
        "RUNTIME_TASK_LEASE_SECONDS": "120",
        "RUNTIME_RECOVERY_INTERVAL_SECONDS": "5",
        "RUNTIME_DISPATCH_LOCK_SECONDS": "10",
        "CONTEXT_RECONCILIATION_INTERVAL_SECONDS": "60",
        "PROJECTION_OUTBOX_BATCH_SIZE": "100",
        "PROJECTION_BACKLOG_ALERT_THRESHOLD": "100",
        "CONTEXT_NODE_PREVIEW_CHARACTERS": "256",
    }
    path.write_text(
        "".join(f"{key}='{value}'\n" for key, value in values.items()),
        encoding="utf-8",
    )
    return openrouter_key, youtube_key, password


def request_json(path: str, expected_status: int) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:8000{path}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read())
    if status != expected_status:
        raise RuntimeError(f"{path} returned {status}, expected {expected_status}")
    return payload


def wait_for_status(
    path: str,
    expected_status: int,
    expected_state: str,
    *,
    timeout: int = 120,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = request_json(path, expected_status)
            if payload.get("status") == expected_state:
                return
        except (OSError, ValueError, RuntimeError):
            pass
        time.sleep(2)
    raise RuntimeError(f"{path} did not reach {expected_state}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
