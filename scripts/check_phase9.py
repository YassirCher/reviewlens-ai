#!/usr/bin/env python3
"""Phase 9 isolated stack and browser checks; every upstream credential is a mock."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import check_phase6 as base
import check_phase8 as browser

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _browser_journeys() -> None:
    if browser._occupied(3000):
        raise RuntimeError("Phase 9 browser port already occupied: 3000")
    before = browser._frontend_material_inventory()
    next_env = FRONTEND / "next-env.d.ts"
    next_env_before = next_env.read_bytes() if next_env.exists() else None
    node = "node.exe" if os.name == "nt" else "node"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = {**os.environ, "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8899"}
    server: subprocess.Popen[bytes] | None = None
    try:
        server = subprocess.Popen(
            [node, "node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1"],
            cwd=FRONTEND, env=environment, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags,
        )
        browser._wait("http://127.0.0.1:3000/admin/login", server, "Phase 9 Next.js preview")
        subprocess.run(
            [node, "node_modules/playwright/cli.js", "test", "tests/admin",
             "--config", "playwright.admin.config.ts", "--reporter=dot"],
            cwd=FRONTEND, env=environment, check=True,
        )
    finally:
        browser._stop(server)
        if next_env_before is not None and next_env.read_bytes() != next_env_before:
            next_env.write_bytes(next_env_before)
    if browser._frontend_material_inventory() != before:
        raise RuntimeError("Phase 9 browser check changed frontend source or user-owned files")


def _isolated_stack() -> None:
    suffix = secrets.token_hex(4)
    environment = ROOT / f".env.phase9.{suffix}.local"
    if environment.exists():
        raise RuntimeError("generated Phase 9 environment path already exists")
    project = f"reviewlens-phase9-{suffix}"
    compose = [
        "docker", "compose", "-p", project, "--env-file", str(environment),
        "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(ROOT / "docker-compose.test.yml"),
    ]
    base.ENV_FILE = environment
    base.PROJECT = project
    base.COMPOSE = compose
    openrouter_key, youtube_key = base.create_test_environment()
    try:
        base.run(compose + ["--profile", "test", "config", "--quiet"])
        base.run(compose + ["build", "migrate", "api", "worker", "scheduler",
                            "foundation-tests", "openrouter-mock", "youtube-mock"])
        base.run(compose + ["--profile", "test", "up", "-d", "--wait",
                            "postgres", "redis", "neo4j", "qdrant", "openrouter-mock", "youtube-mock"])
        base.migration_command("alembic", "downgrade", "base")
        base.migration_command("alembic", "upgrade", "head")
        base.migration_command("python", "-m", "app.cli", "seed")
        base.migration_command("alembic", "downgrade", "20260916_0007")
        base.migration_command("alembic", "upgrade", "head")
        base.migration_command("python", "-m", "app.cli", "seed")
        base.run(compose + ["up", "--build", "migrate"])
        base.run(compose + ["run", "--rm", "foundation-tests", "python", "-m", "pytest",
                            "tests/integration/test_phase9_admin_stack.py", "-q"])
        logs = base.run(compose + ["--profile", "test", "logs", "--no-color"],
                        capture_output=True).stdout
        if openrouter_key in logs or youtube_key in logs:
            raise RuntimeError("Phase 9 stack logs exposed a mock credential")
    finally:
        base.run(compose + ["--profile", "test", "down", "--volumes", "--remove-orphans"],
                 check=False)
        if environment.exists():
            environment.unlink()


def main() -> int:
    if base.run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
        raise RuntimeError("context/ must remain outside Git")
    admin_only = "--admin-browser-only" in sys.argv[1:]
    browser_only = "--browser-only" in sys.argv[1:]
    if not browser_only and not admin_only:
        try:
            base.run(["docker", "info"], capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            print("Docker Desktop must be running for Phase 9 isolated-stack verification.",
                  file=sys.stderr)
            return 2
        subprocess.run([sys.executable, "scripts/check_phase8.py"], cwd=ROOT, check=True)
        _isolated_stack()
    elif browser_only:
        subprocess.run([sys.executable, "scripts/check_phase8.py", "--browser-only"],
                       cwd=ROOT, check=True)
    _browser_journeys()
    if base.run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
        raise RuntimeError("context/ must remain outside Git")
    print("Phase 9 admin browser journeys passed." if admin_only else
          "Phase 9 verified: isolated migration cycle, prior gates, mocked API and admin browser journeys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
