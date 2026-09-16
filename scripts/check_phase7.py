#!/usr/bin/env python3
"""Verify Phase 7 with isolated data, fake credentials, and local upstream mocks."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import check_phase6 as base

ROOT = Path(__file__).resolve().parents[1]
SUFFIX = secrets.token_hex(4)
ENV_FILE = ROOT / f".env.phase7.{SUFFIX}.local"
PROJECT = f"reviewlens-phase7-{SUFFIX}"
COMPOSE = [
    "docker", "compose", "-p", PROJECT,
    "--env-file", str(ENV_FILE),
    "-f", str(ROOT / "docker-compose.yml"),
    "-f", str(ROOT / "docker-compose.test.yml"),
]
base.ENV_FILE = ENV_FILE
base.PROJECT = PROJECT
base.COMPOSE = COMPOSE


def main() -> int:
    frontend_before = base.repository_inventory("frontend")
    if ENV_FILE.exists():
        raise RuntimeError("generated Phase 7 environment path already exists")
    try:
        base.run(["docker", "info"], capture_output=True)
    except (FileNotFoundError, base.subprocess.CalledProcessError):
        print("Docker Desktop must be running for Phase 7 verification.", file=sys.stderr)
        return 2
    openrouter_key, youtube_key = base.create_test_environment()
    try:
        base.run(COMPOSE + ["--profile", "test", "config", "--quiet"])
        base.run(COMPOSE + ["build", "migrate", "api", "worker", "scheduler", "foundation-tests", "openrouter-mock", "youtube-mock"])
        base.run(COMPOSE + ["--profile", "test", "up", "-d", "--wait", "postgres", "redis", "neo4j", "qdrant", "openrouter-mock", "youtube-mock"])
        base.migration_command("alembic", "downgrade", "base")
        base.migration_command("alembic", "upgrade", "head")
        base.migration_command("python", "-m", "app.cli", "seed")
        base.migration_command("alembic", "downgrade", "20260916_0006")
        base.migration_command("alembic", "upgrade", "head")
        base.migration_command("python", "-m", "app.cli", "seed")
        base.run(COMPOSE + ["up", "--build", "migrate"])
        base.run(COMPOSE + ["up", "-d", "--build", "--wait", "worker", "scheduler"])
        if "--focused" in sys.argv[1:]:
            base.run(COMPOSE + ["run", "--rm", "foundation-tests", "python", "-m", "pytest", "tests/integration/test_phase7_public_stack.py", "-q"])
            return 0
        base.run(COMPOSE + ["run", "--rm", "foundation-tests"])
        base.run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"])

        base.run(COMPOSE + ["--profile", "test", "up", "-d", "--build", "--wait", "postgres", "redis", "neo4j", "qdrant", "openrouter-mock", "youtube-mock", "migrate", "api", "worker", "scheduler", "frontend"])
        assert base.request_json("/health", 200) == {"status": "ok", "service": "reviewlens-api"}
        base.wait_for_status("/health/ready", 200, "ready")
        base.request_json("/api/config", 200)
        base.run(COMPOSE + ["exec", "-T", "api", "python", "-m", "app.cli", "openrouter-catalog-refresh"])
        base.run(COMPOSE + ["exec", "-T", "api", "python", "-m", "app.cli", "analysis-config-seed"])
        partial = base.fixture("partial")
        if partial["status"] != "partial" or not partial["report_id"]:
            raise RuntimeError("partial mocked workflow was not published")
        failed = base.fixture("audit_fail")
        if failed["report_id"] is not None:
            raise RuntimeError("failed audit exposed a report")
        logs = base.run(COMPOSE + ["--profile", "test", "logs", "--no-color"], capture_output=True).stdout
        for label, forbidden in (
            ("mock OpenRouter credential", openrouter_key),
            ("mock YouTube credential", youtube_key),
            ("transcript injection marker", "Ignore previous instructions"),
            ("comment body marker", "Audience observation"),
        ):
            if forbidden in logs:
                raise RuntimeError(f"Phase 7 logs exposed {label}")
        if base.repository_inventory("frontend") != frontend_before:
            raise RuntimeError("Phase 7 verification changed the frontend working tree")
        if base.run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
            raise RuntimeError("context/ must remain excluded from GitHub")
        print("Phase 7 verified: migration cycle, mocked API/report lifecycle, partial and failed audit, full Compose health, V1 smoke, no live or paid calls, unchanged frontend.")
        return 0
    finally:
        base.run(COMPOSE + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        if ENV_FILE.exists():
            ENV_FILE.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
