#!/usr/bin/env python3
"""Single Phase 11 acceptance gate for cutover, compatibility, and operational proof."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
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
    "app/admin/cutover.py",
    "app/api/routes.py",
    "app/api/v2/admin_cutover.py",
    "app/compatibility/v1.py",
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
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/phase11.dump; '
        'dropdb -U "$POSTGRES_USER" --if-exists reviewlens_phase11_restore; '
        'createdb -U "$POSTGRES_USER" reviewlens_phase11_restore; '
        'pg_restore -U "$POSTGRES_USER" -d reviewlens_phase11_restore /tmp/phase11.dump; '
        'psql -U "$POSTGRES_USER" -d reviewlens_phase11_restore -Atc "SELECT version_num FROM alembic_version"; '
        'dropdb -U "$POSTGRES_USER" reviewlens_phase11_restore; '
        'rm -f /tmp/phase11.dump'
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
        "PHASE11_ADMIN_EMAIL": "phase6-admin@example.test",
        "PHASE11_ADMIN_PASSWORD": admin_password,
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


def _append_phase11_environment(environment: Path) -> None:
    with environment.open("a", encoding="utf-8") as handle:
        for key, value in {
            "PUBLIC_ROOT_EXPERIENCE": "v2",
            "LEGACY_ANALYSIS_ADAPTER_ENABLED": "true",
            "LEGACY_ADAPTER_WAIT_SECONDS": "240",
            "LEGACY_API_SUNSET_AT": "",
            "CUTOVER_STABLE_WINDOW_HOURS": "0.001",
            "CUTOVER_MIN_TERMINAL_RUNS": "1",
            "CUTOVER_MIN_COMPATIBILITY_REQUESTS": "2",
            "CUTOVER_MAX_FAILURE_RATE": "0.10",
            "CUTOVER_MAX_P95_RUN_LATENCY_SECONDS": "900",
            "CUTOVER_MIN_COMPATIBILITY_SUCCESS_RATE": "0.95",
            "PHASE11_ALLOWED_INFERENCE_MODEL": "deepseek/deepseek-v4-flash",
        }.items():
            handle.write(f"{key}='{value}'\n")


def _set_environment_value(environment: Path, key: str, value: str) -> None:
    prefix = f"{key}="
    lines = environment.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}='{value}'"
            replaced = True
    if not replaced:
        lines.append(f"{key}='{value}'")
    environment.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mock_inference_history(compose: list[str]) -> dict:
    command = (
        "import json,urllib.request;"
        "print(json.dumps(json.load(urllib.request.urlopen('http://openrouter-mock:8089/history'))))"
    )
    result = base.run(
        compose + ["exec", "-T", "api", "python", "-c", command],
        capture_output=True,
    )
    return json.loads(result.stdout.strip())


def _assert_deepseek_only(compose: list[str]) -> None:
    history = _mock_inference_history(compose).get("inference", [])
    if not history:
        raise RuntimeError("Phase 11 did not dispatch any mocked inference")
    unexpected = [
        item for item in history
        if item.get("operation") not in {"chat", "embedding"}
        or item.get("model") != "deepseek/deepseek-v4-flash"
    ]
    if unexpected:
        raise RuntimeError(f"non-DeepSeek Phase 11 inference detected: {unexpected}")


def _wait_frontend_text(expected: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:3000", timeout=5) as response:
                if expected in response.read().decode("utf-8"):
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f"frontend root did not render expected runtime mode: {expected}")


def _admin_opener(admin_password: str) -> tuple[urllib.request.OpenerDirector, str]:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    login = urllib.request.Request(
        "http://localhost:8000/api/v2/admin/session",
        data=json.dumps({
            "email": "phase6-admin@example.test",
            "password": admin_password,
        }).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(login, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("admin login failed during root rollback drill")
    with opener.open("http://localhost:8000/api/v2/admin/csrf", timeout=10) as response:
        csrf = json.load(response)["csrf_token"]
    return opener, csrf


def _set_kill_switch(
    opener: urllib.request.OpenerDirector,
    csrf: str,
    enabled: bool,
) -> None:
    request = urllib.request.Request(
        "http://localhost:8000/api/v2/admin/settings/kill-switch",
        data=json.dumps({
            "enabled": enabled,
            "confirmation": "enable kill switch" if enabled else "disable kill switch",
        }).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        method="PUT",
    )
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("kill-switch action failed during root rollback drill")


def _root_rollback_drill(
    compose: list[str],
    environment: Path,
    admin_password: str,
) -> None:
    opener, csrf = _admin_opener(admin_password)
    _set_kill_switch(opener, csrf, True)
    try:
        _set_environment_value(environment, "PUBLIC_ROOT_EXPERIENCE", "v1")
        base.run(compose + ["up", "-d", "--wait", "--force-recreate", "--no-deps", "frontend"])
        _wait_frontend_text("Three reviews in.")
        with urllib.request.urlopen("http://localhost:3000/research", timeout=10) as response:
            if "See the buying signal" not in response.read().decode("utf-8"):
                raise RuntimeError("/research alias failed during root rollback")
    finally:
        _set_environment_value(environment, "PUBLIC_ROOT_EXPERIENCE", "v2")
        base.run(
            compose + ["up", "-d", "--wait", "--force-recreate", "--no-deps", "frontend"],
            check=False,
        )
        _wait_frontend_text("See the buying signal")
        _set_kill_switch(opener, csrf, False)


def _stack_checks() -> None:
    for port in (3000, 8000, 5432, 6379, 7474, 7687, 6333, 6334):
        if _occupied(port):
            raise RuntimeError(f"Phase 11 isolated stack port is occupied: {port}")
    suffix = secrets.token_hex(4)
    environment = ROOT / f".env.phase11.{suffix}.local"
    project = f"reviewlens-phase11-{suffix}"
    compose = [
        "docker", "compose", "-p", project, "--env-file", str(environment),
        "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(ROOT / "docker-compose.test.yml"),
    ]
    base.ENV_FILE = environment
    base.PROJECT = project
    base.COMPOSE = compose
    openrouter_key, youtube_key = base.create_test_environment()
    _append_phase11_environment(environment)
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
        base.migration_command("alembic", "downgrade", "20260917_0008")
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
        _assert_deepseek_only(compose)
        _root_rollback_drill(compose, environment, admin_password)
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


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _local_v1_root_drill() -> None:
    node = _node()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    mock: subprocess.Popen[bytes] | None = None
    frontend: subprocess.Popen[bytes] | None = None
    next_env = FRONTEND / "next-env.d.ts"
    next_env_before = next_env.read_bytes() if next_env.exists() else None
    try:
        mock = subprocess.Popen(
            [node, "tests/mock-api.mjs"],
            cwd=FRONTEND,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        environment = {
            **os.environ,
            "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8899",
            "V2_API_INTERNAL_URL": "http://127.0.0.1:8899",
            "PUBLIC_ROOT_EXPERIENCE": "v1",
        }
        frontend = subprocess.Popen(
            [node, "node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1"],
            cwd=FRONTEND,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _wait_frontend_text("Three reviews in.")
        with urllib.request.urlopen("http://127.0.0.1:3000/research", timeout=10) as response:
            if "See the buying signal" not in response.read().decode("utf-8"):
                raise RuntimeError("V2 alias was unavailable in V1 rollback mode")
    finally:
        _stop_process(frontend)
        _stop_process(mock)
        if next_env_before is not None and next_env.exists() and next_env.read_bytes() != next_env_before:
            next_env.write_bytes(next_env_before)


def _browser_only() -> None:
    subprocess.run([sys.executable, "scripts/check_phase8.py", "--browser-only"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "scripts/check_phase9.py", "--admin-browser-only"], cwd=ROOT, check=True)
    _local_v1_root_drill()


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
        label = "isolated stack, DeepSeek proof, and production browser"
    elif args.browser_only:
        _browser_only()
        label = "local browser"
    else:
        _static_checks()
        _stack_checks()
        _browser_only()
        label = "static, isolated stack, and browser"
    print(f"Phase 11 verified: {label} acceptance passed with mocked upstreams and no paid calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
