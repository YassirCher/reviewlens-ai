#!/usr/bin/env python3
"""Run Phase 1 migration, integration, stack, and degraded-health checks."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
ENV_FILE = ROOT / ".env.phase1.local"
PROJECT = "reviewlens-phase1-check"
COMPOSE = [
    "docker",
    "compose",
    "-p",
    PROJECT,
    "--env-file",
    str(ENV_FILE),
    "-f",
    str(ROOT / "docker-compose.yml"),
    "-f",
    str(ROOT / "docker-compose.test.yml"),
]


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, check=check)


def create_test_environment() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.security import hash_password

    password = secrets.token_urlsafe(24)
    values = {
        "REVIEWLENS_ENV_FILE": ENV_FILE.name,
        "APP_ENV": "test",
        "APP_PUBLIC_URL": "http://localhost:3000",
        "API_PUBLIC_URL": "http://localhost:8000",
        "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000",
        "LOG_LEVEL": "WARNING",
        "ADMIN_EMAIL": "phase1-admin@example.test",
        "ADMIN_PASSWORD_HASH": hash_password(password),
        "PHASE1_TEST_ADMIN_PASSWORD": password,
        "SESSION_SECRET": secrets.token_urlsafe(32),
        "PUBLIC_TOKEN_HASH_SECRET": secrets.token_urlsafe(32),
        "RATE_LIMIT_HASH_SECRET": secrets.token_urlsafe(32),
        "POSTGRES_DB": "reviewlens_phase1_test",
        "POSTGRES_USER": "reviewlens",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "REDIS_PASSWORD": secrets.token_urlsafe(24),
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": secrets.token_urlsafe(24),
        "YOUTUBE_API_KEY": "test-only-youtube-key",
        "OPENROUTER_API_KEY": "test-only-openrouter-key",
        "OPENROUTER_MODEL": "test-only-v1-model",
        "PUBLIC_ANALYSIS_ENABLED": "true",
    }
    # Compose interpolates dollar signs in unquoted .env values. Single quotes keep
    # the Argon2 hash literal while still being removed before container injection.
    ENV_FILE.write_text("".join(f"{key}='{value}'\n" for key, value in values.items()), encoding="utf-8")


def request_json(path: str, expected_status: int) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as response:
            status = response.status
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read())
    if status != expected_status:
        raise RuntimeError(f"{path} returned {status}, expected {expected_status}")
    return payload


def wait_for_status(path: str, expected_status: int, expected_state: str, timeout: int = 90) -> None:
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


def main() -> int:
    try:
        subprocess.run(
            ["docker", "info"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Docker Desktop must be running before Phase 1 verification.", file=sys.stderr)
        return 2

    create_test_environment()
    try:
        # Profiles are opt-in even for `down`; include the test profile so an
        # interrupted prior run cannot retain the disposable test container.
        run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        run(COMPOSE + ["config", "--quiet"])
        run(
            COMPOSE
            + [
                "up",
                "--build",
                "--abort-on-container-exit",
                "--exit-code-from",
                "foundation-tests",
                "foundation-tests",
            ]
        )
        run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"])

        run(COMPOSE + ["up", "-d", "--build", "--wait"])
        assert request_json("/health", 200) == {"status": "ok", "service": "reviewlens-api"}
        wait_for_status("/health/ready", 200, "ready")
        request_json("/api/config", 200)

        run(COMPOSE + ["stop", "neo4j"])
        wait_for_status("/health/ready", 200, "degraded")
        run(COMPOSE + ["start", "neo4j"])
        wait_for_status("/health/ready", 200, "ready")

        run(COMPOSE + ["stop", "qdrant"])
        wait_for_status("/health/ready", 200, "degraded")
        run(COMPOSE + ["start", "qdrant"])
        wait_for_status("/health/ready", 200, "ready")

        run(COMPOSE + ["stop", "redis"])
        wait_for_status("/health/ready", 503, "not_ready")
        run(COMPOSE + ["start", "redis"])
        wait_for_status("/health/ready", 200, "ready")

        run(COMPOSE + ["stop", "postgres"])
        wait_for_status("/health/ready", 503, "not_ready")
        print("Phase 1 stack verification passed.")
        return 0
    finally:
        run(
            COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"],
            check=False,
        )
        if ENV_FILE.exists():
            ENV_FILE.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
