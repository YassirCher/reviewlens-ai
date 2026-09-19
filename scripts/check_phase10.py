#!/usr/bin/env python3
"""Single Phase 10 acceptance gate using generated credentials and mocked upstreams."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import check_phase6 as base

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PROTECTED_FRONTEND_FILES = (
    FRONTEND / "tsconfig.json",
    FRONTEND / "AGENTS.md",
    FRONTEND / "CLAUDE.md",
    FRONTEND / "next-env.d.ts",
)
MYPY_TARGETS = (
    "app/config.py",
    "app/platform/http_security.py",
    "app/platform/alerts.py",
    "app/observability.py",
    "app/services/audit_service.py",
    "app/admin/evaluation.py",
    "app/api/v2/admin_analytics.py",
    "app/api/v2/health.py",
    "app/knowledge/storage.py",
    "app/llmops/gateway.py",
)


def _python() -> str:
    local = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(local) if local.exists() else sys.executable


def _npm() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _node() -> str:
    return "node.exe" if os.name == "nt" else "node"


def _occupied(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _protected_hashes() -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in PROTECTED_FRONTEND_FILES}


def _restore_protected(before: dict[Path, bytes | None]) -> None:
    changed: list[str] = []
    for path, content in before.items():
        current = path.read_bytes() if path.exists() else None
        if current == content:
            continue
        changed.append(str(path.relative_to(ROOT)))
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)
    if changed:
        raise RuntimeError(f"acceptance tooling changed protected frontend files: {', '.join(changed)}")


def _cleanup_artifacts() -> None:
    for path in (
        FRONTEND / ".next",
        FRONTEND / "test-results",
        FRONTEND / "playwright-report",
        ROOT / ".audit-cache",
    ):
        resolved = path.resolve()
        if ROOT.resolve() not in resolved.parents:
            raise RuntimeError(f"refusing to clean path outside repository: {resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.exists():
            resolved.unlink()


def _secret_scan() -> None:
    tracked = base.run(["git", "ls-files"], capture_output=True).stdout.splitlines()
    patterns = (
        re.compile(rb"sk-or-v1-[A-Za-z0-9_-]{20,}"),
        re.compile(rb"sk-proj-[A-Za-z0-9_-]{20,}"),
        re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    findings = []
    for relative in tracked:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        body = path.read_bytes()
        if any(pattern.search(body) for pattern in patterns):
            findings.append(relative)
    if findings:
        raise RuntimeError(f"secret scan found credential material in: {', '.join(findings)}")
    env_files = [item for item in tracked if Path(item).name.startswith(".env")]
    unexpected_env = [item for item in env_files if not Path(item).name.endswith(".example")]
    if unexpected_env:
        raise RuntimeError(f"unexpected tracked environment files: {unexpected_env}")


def _run_with_retries(command: list[str], *, cwd: Path, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            subprocess.run(command, cwd=cwd, check=True)
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            time.sleep(5)


def _static_checks() -> None:
    before = _protected_hashes()
    python = _python()
    try:
        subprocess.run([python, "-m", "ruff", "check", "app", "tests"], cwd=BACKEND, check=True)
        subprocess.run([python, "-m", "mypy", *MYPY_TARGETS], cwd=BACKEND, check=True)
        subprocess.run([python, "-m", "pytest", "tests", "-q"], cwd=BACKEND, check=True)
        cache = ROOT / ".audit-cache"
        _run_with_retries(
            [python, "-m", "pip_audit", "--local", "--cache-dir", str(cache)],
            cwd=BACKEND,
        )
        subprocess.run([_npm(), "ci"], cwd=FRONTEND, check=True)
        for command in ("lint", "test:unit", "build"):
            subprocess.run([_npm(), "run", command], cwd=FRONTEND, check=True)
        _run_with_retries([_npm(), "audit", "--audit-level=high"], cwd=FRONTEND)
        if (ROOT / "context").is_dir():
            base.run([python, "scripts/check_context.py"], capture_output=False).check_returncode()
        base.run(["git", "diff", "--check"], capture_output=False).check_returncode()
        _secret_scan()
        if base.run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
            raise RuntimeError("context/ must remain outside Git")
    finally:
        try:
            _restore_protected(before)
        finally:
            _cleanup_artifacts()


def _backup_restore_drill(compose: list[str]) -> None:
    script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/phase10.dump; '
        'dropdb -U "$POSTGRES_USER" --if-exists reviewlens_phase10_restore; '
        'createdb -U "$POSTGRES_USER" reviewlens_phase10_restore; '
        'pg_restore -U "$POSTGRES_USER" -d reviewlens_phase10_restore /tmp/phase10.dump; '
        'psql -U "$POSTGRES_USER" -d reviewlens_phase10_restore -Atc "SELECT version_num FROM alembic_version"; '
        'dropdb -U "$POSTGRES_USER" reviewlens_phase10_restore; '
        'rm -f /tmp/phase10.dump'
    )
    base.run(compose + ["exec", "-T", "postgres", "sh", "-ec", script])


def _dependency_outage_drill(compose: list[str], service: str) -> None:
    base.run(compose + ["stop", service])
    payload = base.request_json("/health/ready", 503)
    if payload.get("ready") is not False:
        raise RuntimeError(f"health did not fail closed during {service} outage")
    base.run(compose + ["start", service])
    base.wait_for_status("/health/ready", 200, "ready", timeout=120)


def _playwright_stack(admin_password: str) -> None:
    environment = {
        **os.environ,
        "PHASE8_EXTERNAL_SERVERS": "1",
        "PHASE10_ADMIN_EMAIL": "phase6-admin@example.test",
        "PHASE10_ADMIN_PASSWORD": admin_password,
    }
    node = _node()
    subprocess.run(
        [node, "node_modules/playwright/cli.js", "test", "tests/stack",
         "--config", "playwright.stack.config.ts", "--reporter=dot"],
        cwd=FRONTEND, env=environment, check=True,
    )


def _environment_value(path: Path, key: str) -> str:
    prefix = f"{key}='"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix) and line.endswith("'"):
            return line[len(prefix):-1]
    raise RuntimeError(f"generated environment is missing {key}")


def _stack_checks() -> None:
    for port in (3000, 8000, 5432, 6379, 7474, 7687, 6333, 6334):
        if _occupied(port):
            raise RuntimeError(f"Phase 10 isolated stack port is occupied: {port}")
    suffix = secrets.token_hex(4)
    environment = ROOT / f".env.phase10.{suffix}.local"
    project = f"reviewlens-phase10-{suffix}"
    compose = [
        "docker", "compose", "-p", project, "--env-file", str(environment),
        "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(ROOT / "docker-compose.test.yml"),
    ]
    base.ENV_FILE = environment
    base.PROJECT = project
    base.COMPOSE = compose
    openrouter_key, youtube_key = base.create_test_environment()
    admin_password = _environment_value(environment, "PHASE1_TEST_ADMIN_PASSWORD")
    try:
        base.run(compose + ["--profile", "test", "config", "--quiet"])
        base.run(compose + [
            "build", "migrate", "api", "worker", "scheduler", "foundation-tests",
            "openrouter-mock", "youtube-mock", "frontend",
        ])
        base.run(compose + [
            "--profile", "test", "up", "-d", "--wait",
            "postgres", "redis", "neo4j", "qdrant", "openrouter-mock", "youtube-mock",
        ])
        base.migration_command("alembic", "downgrade", "base")
        base.migration_command("alembic", "upgrade", "head")
        base.migration_command("python", "-m", "app.cli", "seed")
        _backup_restore_drill(compose)
        base.migration_command("alembic", "downgrade", "20260916_0007")
        base.migration_command("alembic", "upgrade", "head")
        base.migration_command("python", "-m", "app.cli", "seed")
        base.run(compose + [
            "--profile", "test", "up", "-d", "--wait",
            "migrate", "api", "worker", "scheduler", "frontend",
        ])
        base.run(compose + ["run", "--rm", "foundation-tests"])
        if base.request_json("/health", 200) != {"status": "ok", "service": "reviewlens-api"}:
            raise RuntimeError("legacy health contract changed")
        base.request_json("/api/config", 200)
        base.wait_for_status("/health/ready", 200, "ready")
        base.run(compose + ["exec", "-T", "api", "python", "-m", "app.cli", "openrouter-catalog-refresh"])
        base.run(compose + ["exec", "-T", "api", "python", "-m", "app.cli", "analysis-config-seed"])
        _playwright_stack(admin_password)
        _dependency_outage_drill(compose, "redis")
        _dependency_outage_drill(compose, "postgres")
        logs = base.run(compose + ["--profile", "test", "logs", "--no-color"], capture_output=True).stdout
        if openrouter_key in logs or youtube_key in logs or admin_password in logs:
            raise RuntimeError("isolated stack logs exposed a generated credential")
        if "openrouter.ai/api" in logs or "www.googleapis.com/youtube" in logs:
            raise RuntimeError("isolated acceptance attempted a live provider endpoint")
    finally:
        base.run(compose + ["--profile", "test", "down", "--volumes", "--remove-orphans"], check=False)
        environment.unlink(missing_ok=True)
        _cleanup_artifacts()


def _browser_only() -> None:
    subprocess.run([sys.executable, "scripts/check_phase8.py", "--browser-only"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/check_phase9.py", "--admin-browser-only"], cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--static-only", action="store_true")
    modes.add_argument("--stack-only", action="store_true")
    modes.add_argument("--browser-only", action="store_true")
    args = parser.parse_args()

    if args.static_only:
        _static_checks()
        label = "static"
    elif args.stack_only:
        _stack_checks()
        label = "isolated stack and production browser"
    elif args.browser_only:
        _browser_only()
        label = "local browser"
    else:
        _static_checks()
        _stack_checks()
        _browser_only()
        label = "static, isolated stack, and browser"
    print(f"Phase 10 verified: {label} acceptance passed with mocked upstreams and no paid calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
