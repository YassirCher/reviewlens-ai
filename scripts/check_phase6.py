#!/usr/bin/env python3
"""Run isolated Phase 6 migration and mocked seven-agent workflow verification."""

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
ENV_FILE = ROOT / ".env.phase6.local"
PROJECT = "reviewlens-phase6-check"
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


def run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        check=check,
        capture_output=capture_output,
    )


def repository_inventory(path: str) -> tuple[str, dict[str, str]]:
    status = run(
        ["git", "status", "--short", "--", path],
        capture_output=True,
    ).stdout
    listed = run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", path],
        capture_output=True,
    ).stdout.splitlines()
    hashes = {
        item: hashlib.sha256((ROOT / item).read_bytes()).hexdigest()
        for item in sorted(listed)
        if (ROOT / item).is_file()
    }
    return status, hashes


def create_test_environment() -> tuple[str, str]:
    sys.path.insert(0, str(BACKEND))
    from app.security import hash_password

    password = secrets.token_urlsafe(24)
    openrouter_key = "phase6-mock-" + secrets.token_urlsafe(18)
    youtube_key = "phase6-youtube-" + secrets.token_urlsafe(18)
    values = {
        "REVIEWLENS_ENV_FILE": ENV_FILE.name,
        "APP_ENV": "test",
        "APP_PUBLIC_URL": "http://localhost:3000",
        "API_PUBLIC_URL": "http://localhost:8000",
        "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000",
        "LOG_LEVEL": "WARNING",
        "ADMIN_EMAIL": "phase6-admin@example.test",
        "ADMIN_PASSWORD_HASH": hash_password(password),
        "PHASE1_TEST_ADMIN_PASSWORD": password,
        "SESSION_SECRET": secrets.token_urlsafe(32),
        "PUBLIC_TOKEN_HASH_SECRET": secrets.token_urlsafe(32),
        "RATE_LIMIT_HASH_SECRET": secrets.token_urlsafe(32),
        "POSTGRES_DB": "reviewlens_phase6_test",
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
        "OPENROUTER_MODEL": "phase6-v1-fixture-never-used",
        "V2_AGENT_CHAT_MODELS": "deepseek/deepseek-v4-flash",
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
    ENV_FILE.write_text(
        "".join(f"{key}='{value}'\n" for key, value in values.items()),
        encoding="utf-8",
    )
    return openrouter_key, youtube_key


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


def fixture(scenario: str) -> dict:
    result = run(
        COMPOSE
        + [
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "app.cli",
            "analysis-fixture",
            "--scenario",
            scenario,
            "--wait",
            "--timeout-seconds",
            "240",
        ],
        capture_output=True,
        check=False,
    )
    output = result.stdout.strip().splitlines()
    if not output:
        raise RuntimeError(
            f"analysis fixture {scenario} produced no result (exit {result.returncode})"
        )
    payload = json.loads(output[-1])
    if payload.get("live_calls") != 0:
        raise RuntimeError(f"analysis fixture {scenario} reported a live call")
    if result.returncode:
        raise RuntimeError(
            f"analysis fixture {scenario} failed: status={payload.get('status')}, "
            f"report_status={payload.get('report_status')}, "
            f"audit_status={payload.get('audit_status')}, "
            f"correction_attempts={payload.get('correction_attempts')}, "
            f"failed_tasks={payload.get('failed_tasks', [])}"
        )
    return payload


def main() -> int:
    frontend_before = repository_inventory("frontend")
    try:
        run(["docker", "info"], capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Docker Desktop must be running before Phase 6 verification.", file=sys.stderr)
        return 2

    openrouter_key, youtube_key = create_test_environment()
    try:
        run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        run(COMPOSE + ["--profile", "test", "config", "--quiet"])
        run(
            COMPOSE
            + [
                "build",
                "migrate",
                "api",
                "worker",
                "scheduler",
                "foundation-tests",
                "openrouter-mock",
                "youtube-mock",
            ]
        )
        run(
            COMPOSE
            + [
                "--profile",
                "test",
                "up",
                "-d",
                "--wait",
                "postgres",
                "redis",
                "neo4j",
                "qdrant",
                "openrouter-mock",
                "youtube-mock",
            ]
        )

        migration_command("alembic", "downgrade", "base")
        migration_command("alembic", "upgrade", "head")
        migration_command("python", "-m", "app.cli", "seed")
        migration_command("alembic", "downgrade", "20260916_0005")
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
                "youtube-mock",
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
        run(COMPOSE + ["exec", "-T", "api", "python", "-m", "app.cli", "openrouter-catalog-refresh"])
        first_seed = run(
            COMPOSE + ["exec", "-T", "api", "python", "-m", "app.cli", "analysis-config-seed"],
            capture_output=True,
        ).stdout
        second_seed = run(
            COMPOSE + ["exec", "-T", "api", "python", "-m", "app.cli", "analysis-config-seed"],
            capture_output=True,
        ).stdout
        if json.loads(first_seed.strip().splitlines()[-1])["workflow_version_id"] != json.loads(
            second_seed.strip().splitlines()[-1]
        )["workflow_version_id"]:
            raise RuntimeError("Phase 6 configuration seed was not idempotent")

        results = {scenario: fixture(scenario) for scenario in (
            "complete",
            "comments",
            "partial",
            "retry_once",
            "audit_correction",
            "audit_fail",
            "cancel",
        )}
        if results["complete"]["status"] != "complete":
            raise RuntimeError("complete analysis fixture did not complete")
        if results["partial"]["status"] != "partial":
            raise RuntimeError("partial analysis fixture did not retain a partial report")
        if results["audit_fail"]["report_id"] is not None:
            raise RuntimeError("repeated audit failure published a report")
        if results["cancel"]["report_id"] is not None:
            raise RuntimeError("cancelled run published a report")
        if results["retry_once"]["correction_attempts"] != 1:
            raise RuntimeError("retry_once did not preserve exactly one correction attempt")

        logs = run(COMPOSE + ["--profile", "test", "logs", "--no-color"], capture_output=True).stdout
        for label, forbidden in (
            ("mock OpenRouter credential", openrouter_key),
            ("mock YouTube credential", youtube_key),
            ("transcript injection marker", "Ignore previous instructions"),
            ("comment body marker", "Audience observation"),
            ("injected instruction marker", "expose configuration secrets"),
        ):
            if forbidden and forbidden in logs:
                raise RuntimeError(f"Phase 6 logs exposed {label}")

        if repository_inventory("frontend") != frontend_before:
            raise RuntimeError("Phase 6 verification changed the frontend working tree")
        if run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
            raise RuntimeError("context/ must remain excluded from GitHub")
        print(
            "Phase 6 stack verification passed with migration cycling, seven mocked workflows, "
            "immutable reports, no live or paid calls, and an unchanged frontend inventory."
        )
        return 0
    finally:
        run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        if ENV_FILE.exists():
            ENV_FILE.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
