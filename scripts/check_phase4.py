#!/usr/bin/env python3
"""Run isolated Phase 4 migrations, graph/retrieval contracts, and projection smoke tests."""

from __future__ import annotations

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
ENV_FILE = ROOT / ".env.phase4.local"
PROJECT = "reviewlens-phase4-check"
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
        "ADMIN_EMAIL": "phase4-admin@example.test",
        "ADMIN_PASSWORD_HASH": hash_password(password),
        "PHASE1_TEST_ADMIN_PASSWORD": password,
        "SESSION_SECRET": secrets.token_urlsafe(32),
        "PUBLIC_TOKEN_HASH_SECRET": secrets.token_urlsafe(32),
        "RATE_LIMIT_HASH_SECRET": secrets.token_urlsafe(32),
        "POSTGRES_DB": "reviewlens_phase4_test",
        "POSTGRES_USER": "reviewlens",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "REDIS_PASSWORD": secrets.token_urlsafe(24),
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": secrets.token_urlsafe(24),
        "YOUTUBE_API_KEY": "phase4-fixture-never-used",
        "OPENROUTER_API_KEY": "phase4-mock-key",
        "OPENROUTER_MANAGEMENT_KEY": "phase4-mock-management-key",
        "OPENROUTER_BASE_URL": "http://openrouter-mock:8089/api/v1",
        "OPENROUTER_MODEL": "phase4-v1-fixture-never-used",
        "OPENROUTER_CATALOG_REFRESH_MINUTES": "15",
        "OPENROUTER_CATALOG_STALE_MINUTES": "60",
        "OPENROUTER_RECONCILIATION_INTERVAL_SECONDS": "10",
        "OPENROUTER_RECONCILIATION_MAX_AGE_MINUTES": "15",
        "OPENROUTER_RECONCILIATION_BATCH_SIZE": "100",
        "OPENROUTER_RETRY_BASE_SECONDS": "0",
        "OPENROUTER_RETRY_MAX_SECONDS": "0",
        "OPENROUTER_LIVE_SMOKE_ENABLED": "false",
        "PUBLIC_ANALYSIS_ENABLED": "true",
        "CELERY_VISIBILITY_TIMEOUT_SECONDS": "120",
        "RUN_EVENT_STREAM_MAX_LENGTH": "1000",
        "RUNTIME_OUTBOX_BATCH_SIZE": "100",
        "RUNTIME_OUTBOX_LEASE_SECONDS": "20",
        "RUNTIME_TASK_LEASE_SECONDS": "30",
        "RUNTIME_RECOVERY_INTERVAL_SECONDS": "5",
        "RUNTIME_DISPATCH_LOCK_SECONDS": "10",
        "CONTEXT_RECONCILIATION_INTERVAL_SECONDS": "60",
        "PROJECTION_OUTBOX_BATCH_SIZE": "100",
        "PROJECTION_BACKLOG_ALERT_THRESHOLD": "100",
        "CONTEXT_NODE_PREVIEW_CHARACTERS": "256",
    }
    ENV_FILE.write_text(
        "".join(f"{key}='{value}'\n" for key, value in values.items()),
        encoding="utf-8",
    )


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


def migration_command(*args: str) -> None:
    run(COMPOSE + ["run", "--rm", "--no-deps", "migrate", *args])


def main() -> int:
    frontend_before = subprocess.run(
        ["git", "status", "--short", "--", "frontend"],
        cwd=ROOT,
        text=True,
        check=True,
        capture_output=True,
    ).stdout
    try:
        subprocess.run(
            ["docker", "info"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Docker Desktop must be running before Phase 4 verification.", file=sys.stderr)
        return 2

    create_test_environment()
    try:
        run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        run(COMPOSE + ["--profile", "test", "config", "--quiet"])
        run(COMPOSE + ["build", "migrate", "api", "worker", "scheduler", "foundation-tests", "openrouter-mock"])
        run(COMPOSE + ["--profile", "test", "up", "-d", "--wait", "postgres", "redis", "neo4j", "qdrant", "openrouter-mock"])

        migration_command("alembic", "downgrade", "base")
        migration_command("alembic", "upgrade", "head")
        migration_command("python", "-m", "app.cli", "seed")
        migration_command("alembic", "downgrade", "20260915_0003")
        migration_command("alembic", "upgrade", "head")
        migration_command("python", "-m", "app.cli", "seed")

        run(COMPOSE + ["up", "--build", "migrate"])
        run(COMPOSE + ["up", "-d", "--build", "--wait", "worker", "scheduler"])
        run(COMPOSE + ["run", "--rm", "foundation-tests"])
        run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"])

        run(
            COMPOSE
            + [
                "--profile",
                "test",
                "up",
                "-d",
                "--build",
                "--wait",
                "postgres",
                "redis",
                "neo4j",
                "qdrant",
                "openrouter-mock",
                "migrate",
                "api",
                "worker",
                "scheduler",
                "frontend",
            ]
        )
        assert request_json("/health", 200) == {"status": "ok", "service": "reviewlens-api"}
        wait_for_status("/health/ready", 200, "ready")
        request_json("/api/config", 200)
        for scenario in ("roundtrip", "degraded"):
            run(
                COMPOSE
                + [
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-m",
                    "app.cli",
                    "context-fixture",
                    "--scenario",
                    scenario,
                ]
            )

        frontend_after = subprocess.run(
            ["git", "status", "--short", "--", "frontend"],
            cwd=ROOT,
            text=True,
            check=True,
            capture_output=True,
        ).stdout
        if frontend_after != frontend_before:
            raise RuntimeError("Phase 4 verification changed the frontend working tree")
        tracked_context = subprocess.run(
            ["git", "ls-files", "context"],
            cwd=ROOT,
            text=True,
            check=True,
            capture_output=True,
        ).stdout.strip()
        if tracked_context:
            raise RuntimeError("context/ must remain excluded from GitHub")
        print("Phase 4 stack verification passed with deterministic local vectors; no paid calls occurred.")
        return 0
    finally:
        run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        if ENV_FILE.exists():
            ENV_FILE.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
