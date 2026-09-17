#!/usr/bin/env python3
"""Verify Phase 8's mocked Compose backend and public browser journey.

No repository .env or live provider credential is read. The Compose portion
delegates to the isolated Phase 7 checker, which generates fake credentials.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import check_phase6 as base

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.HTTPError):
        return False


def _occupied(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait(url: str, process: subprocess.Popen[bytes], label: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited before becoming ready")
        if _ready(url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"{label} did not become ready")


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _frontend_material_inventory() -> tuple[str, dict[str, str]]:
    status, hashes = base.repository_inventory("frontend")
    # Playwright's result marker and TypeScript's incremental cache are test
    # artifacts, not source or user-owned worktree content.
    return status, {
        path: digest for path, digest in hashes.items()
        if not path.startswith("frontend/test-results/")
        and path != "frontend/tsconfig.tsbuildinfo"
    }


def _real_stack_browser() -> None:
    suffix = secrets.token_hex(4)
    environment = ROOT / f".env.phase8.{suffix}.local"
    if environment.exists():
        raise RuntimeError("generated Phase 8 environment path already exists")
    project = f"reviewlens-phase8-{suffix}"
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
        base.run(compose + ["--profile", "test", "up", "-d", "--build", "--wait",
                            "postgres", "redis", "neo4j", "qdrant", "openrouter-mock", "youtube-mock",
                            "migrate", "api", "worker", "scheduler", "frontend"])
        assert base.request_json("/health", 200) == {"status": "ok", "service": "reviewlens-api"}
        base.wait_for_status("/health/ready", 200, "ready")
        base.request_json("/api/config", 200)  # Legacy V1 route remains healthy.
        base.run(compose + ["exec", "-T", "api", "python", "-m", "app.cli", "openrouter-catalog-refresh"])
        base.run(compose + ["exec", "-T", "api", "python", "-m", "app.cli", "analysis-config-seed"])
        node = "node.exe" if os.name == "nt" else "node"
        subprocess.run(
            [node, "node_modules/playwright/cli.js", "test", "--config", "playwright.stack.config.ts", "--reporter=dot"],
            cwd=FRONTEND, check=True,
        )
        logs = base.run(compose + ["--profile", "test", "logs", "--no-color"], capture_output=True).stdout
        if openrouter_key in logs or youtube_key in logs:
            raise RuntimeError("Phase 8 stack logs exposed a mock credential")
    finally:
        base.run(compose + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        if environment.exists():
            environment.unlink()


def main() -> int:
    if base.run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
        raise RuntimeError("context/ must remain outside Git")
    browser_only = "--browser-only" in sys.argv[1:]
    # Fail instead of connecting to a developer's existing server or changing it.
    ports = [8899, 3000]
    if not browser_only:
        ports.append(8000)
    for port in ports:
        if _occupied(port):
            raise RuntimeError(f"Phase 8 test port already occupied: {port}")
    if not browser_only:
        subprocess.run([sys.executable, "scripts/check_phase7.py"], cwd=ROOT, check=True)
        _real_stack_browser()

    before = _frontend_material_inventory()
    node = "node.exe" if os.name == "nt" else "node"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    mock: subprocess.Popen[bytes] | None = None
    next_server: subprocess.Popen[bytes] | None = None
    try:
        mock = subprocess.Popen(
            [node, "tests/mock-api.mjs"], cwd=FRONTEND,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
        )
        _wait("http://127.0.0.1:8899/health", mock, "local V2 API mock")
        env = {**os.environ, "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8899",
               "V2_API_INTERNAL_URL": "http://127.0.0.1:8899",
               "PHASE8_EXTERNAL_SERVERS": "1"}
        next_server = subprocess.Popen(
            [node, "node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1"],
            cwd=FRONTEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _wait("http://127.0.0.1:3000/research", next_server, "Next.js preview")
        subprocess.run(
            [node, "node_modules/playwright/cli.js", "test", "tests/e2e", "--reporter=dot"],
            cwd=FRONTEND, env=env, check=True,
        )
    finally:
        _stop(next_server)
        _stop(mock)

    if _frontend_material_inventory() != before:
        raise RuntimeError("Phase 8 browser verification changed frontend source or user-owned files")
    if base.run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
        raise RuntimeError("context/ must remain outside Git")
    label = "local browser" if browser_only else "mocked Compose backend and local browser"
    print(f"Phase 8 verified: {label} flows, responsive/keyboard checks, no live or paid calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
