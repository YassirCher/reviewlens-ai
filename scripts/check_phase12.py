#!/usr/bin/env python3
"""Single Phase 12 evidence, static, isolated-stack, and browser acceptance gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import acceptance_support as support

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
EVIDENCE = ROOT / "docs" / "release-evidence" / "phase12-cutover.json"
APPROVED_MODELS = {
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-flash-0731",
}
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
    "app/admin/retirement.py",
    "app/admin/cutover.py",
    "app/api/v2/admin_analytics.py",
    "app/api/v2/admin_cutover.py",
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


def _cleanup_artifacts() -> None:
    for path in (
        FRONTEND / ".next",
        FRONTEND / "test-results",
        FRONTEND / "playwright-report",
        ROOT / ".audit-cache",
    ):
        resolved = path.resolve()
        if ROOT.resolve() not in resolved.parents:
            raise RuntimeError(f"refusing to clean outside the repository: {resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.exists():
            resolved.unlink()


def _secret_scan() -> None:
    tracked = support.run(["git", "ls-files"], capture_output=True).stdout.splitlines()
    patterns = (
        re.compile(rb"sk-or-v1-[A-Za-z0-9_-]{20,}"),
        re.compile(rb"sk-proj-[A-Za-z0-9_-]{20,}"),
        re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    findings: list[str] = []
    for relative in tracked:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        body = path.read_bytes()
        if any(pattern.search(body) for pattern in patterns):
            findings.append(relative)
    if findings:
        raise RuntimeError(f"secret scan found credential material in: {', '.join(findings)}")
    unexpected_env = [
        item
        for item in tracked
        if Path(item).name.startswith(".env") and not Path(item).name.endswith(".example")
    ]
    if unexpected_env:
        raise RuntimeError(f"tracked generated environment files: {unexpected_env}")


def _run_with_retries(command: list[str], *, cwd: Path, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            subprocess.run(command, cwd=cwd, check=True)
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            time.sleep(5)


def _evidence_check() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.admin.retirement import read_evidence_document

    if not EVIDENCE.is_file():
        raise RuntimeError(
            "Phase 12 production evidence is missing; export it from the authoritative database "
            "with `python -m app.cli phase12-export-cutover-evidence`."
        )
    payload = read_evidence_document(EVIDENCE)
    if payload["payload"]["observation"]["environment"] != "production":
        raise RuntimeError("Phase 12 evidence is not from the production environment")


def _legacy_source_guard() -> None:
    forbidden = (
        "PUBLIC_ROOT_EXPERIENCE",
        "LEGACY_ANALYSIS_ADAPTER_ENABLED",
        "LEGACY_ADAPTER_WAIT_SECONDS",
        "LEGACY_API_SUNSET_AT",
        "OPENROUTER_MODEL",
        "XAI_API_KEY",
        "OPENAI_API_KEY",
    )
    active_files = [
        *BACKEND.joinpath("app").rglob("*.py"),
        *FRONTEND.joinpath("src").rglob("*.ts"),
        *FRONTEND.joinpath("src").rglob("*.tsx"),
        ROOT / ".env.example",
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.test.yml",
    ]
    hits = [
        f"{path.relative_to(ROOT)}:{term}"
        for path in active_files
        if path.is_file()
        for term in forbidden
        if term in path.read_text(encoding="utf-8")
    ]
    if hits:
        raise RuntimeError(f"retired configuration remains in active source: {', '.join(hits)}")


def _static_checks() -> None:
    before = support.repository_inventory(PROTECTED_FRONTEND_FILES)
    python = _python()
    try:
        subprocess.run([python, "-m", "ruff", "check", "app", "tests"], cwd=BACKEND, check=True)
        subprocess.run([python, "-m", "mypy", *MYPY_TARGETS], cwd=BACKEND, check=True)
        subprocess.run([python, "-m", "pytest", "tests", "-q"], cwd=BACKEND, check=True)
        _run_with_retries(
            [python, "-m", "pip_audit", "--local", "--cache-dir", str(ROOT / ".audit-cache")],
            cwd=BACKEND,
        )
        subprocess.run([_npm(), "ci"], cwd=FRONTEND, check=True)
        for command in ("lint", "test:unit", "build"):
            subprocess.run([_npm(), "run", command], cwd=FRONTEND, check=True)
        _run_with_retries([_npm(), "audit", "--audit-level=high"], cwd=FRONTEND)
        if (ROOT / "context").is_dir():
            support.run([python, "scripts/check_context.py"])
        support.run(["git", "diff", "--check"])
        if support.run(["git", "ls-files", "context"], capture_output=True).stdout.strip():
            raise RuntimeError("context/ must remain outside Git")
        _legacy_source_guard()
        _secret_scan()
    finally:
        try:
            support.restore_inventory(before)
        finally:
            _cleanup_artifacts()


def _migration(compose: list[str], *command: str) -> None:
    support.run(compose + ["run", "--rm", "--no-deps", "migrate", *command])


def _backup_restore_drill(compose: list[str]) -> None:
    script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        "pg_dump -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Fc -f /tmp/phase12.dump; "
        "dropdb -U \"$POSTGRES_USER\" --if-exists reviewlens_phase12_restore; "
        "createdb -U \"$POSTGRES_USER\" reviewlens_phase12_restore; "
        "pg_restore -U \"$POSTGRES_USER\" -d reviewlens_phase12_restore /tmp/phase12.dump; "
        "psql -U \"$POSTGRES_USER\" -d reviewlens_phase12_restore -Atc "
        "\"SELECT version_num FROM alembic_version\"; "
        "dropdb -U \"$POSTGRES_USER\" reviewlens_phase12_restore; "
        "rm -f /tmp/phase12.dump"
    )
    support.run(compose + ["exec", "-T", "postgres", "sh", "-ec", script])


def _dependency_outage_drill(compose: list[str], service: str) -> None:
    support.run(compose + ["stop", service])
    payload = support.request_json("/health/ready", 503)
    if payload.get("ready") is not False:
        raise RuntimeError(f"readiness did not fail closed during the {service} outage")
    support.run(compose + ["start", service])
    support.wait_for_status("/health/ready", 200, "ready")


def _assert_retired_routes() -> None:
    for path in ("/health", "/api/config", "/api/analyze", "/api/analyze/stream"):
        support.request_json(path, 404)
    live = support.request_json("/health/live", 200)
    if live != {"status": "ok", "service": "reviewlens-api"}:
        raise RuntimeError("liveness contract changed")


def _assert_frontend_redirect() -> None:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open("http://127.0.0.1:3000/research", timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code == 308 and exc.headers.get("Location") == "/":
            return
        raise RuntimeError(f"/research returned {exc.code} instead of a 308 to /") from exc
    raise RuntimeError("/research did not return a permanent redirect")


def _mock_history(compose: list[str]) -> list[dict]:
    command = (
        "import json,urllib.request;"
        "print(json.dumps(json.load(urllib.request.urlopen("
        "'http://openrouter-mock:8089/history'))['inference']))"
    )
    result = support.run(
        compose + ["exec", "-T", "api", "python", "-c", command],
        capture_output=True,
    )
    return json.loads(result.stdout.strip())


def _assert_approved_models(compose: list[str]) -> None:
    history = _mock_history(compose)
    if not history:
        raise RuntimeError("Phase 12 did not dispatch mocked inference")
    seen = {item.get("model") for item in history}
    unexpected = [
        item
        for item in history
        if item.get("operation") not in {"chat", "embedding"}
        or item.get("model") not in APPROVED_MODELS
    ]
    if unexpected:
        raise RuntimeError(f"non-approved Phase 12 inference detected: {unexpected}")
    if seen != APPROVED_MODELS:
        raise RuntimeError(f"both pinned Flash models were not exercised: {sorted(seen)}")


def _stack_browser(admin_password: str) -> None:
    environment = {
        **os.environ,
        "PHASE8_EXTERNAL_SERVERS": "1",
        "PHASE10_ADMIN_EMAIL": "phase12-admin@example.test",
        "PHASE10_ADMIN_PASSWORD": admin_password,
    }
    subprocess.run(
        [
            _node(),
            "node_modules/playwright/cli.js",
            "test",
            "tests/stack",
            "--config",
            "playwright.stack.config.ts",
            "--reporter=dot",
        ],
        cwd=FRONTEND,
        env=environment,
        check=True,
    )


def _stack_checks() -> None:
    for port in (3000, 8000, 5432, 6379, 7474, 7687, 6333, 6334):
        if _occupied(port):
            raise RuntimeError(f"Phase 12 isolated stack port is occupied: {port}")
    suffix = secrets.token_hex(4)
    environment = ROOT / f".env.phase12.{suffix}.local"
    project = f"reviewlens-phase12-{suffix}"
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "--env-file",
        str(environment),
        "-f",
        str(ROOT / "docker-compose.yml"),
        "-f",
        str(ROOT / "docker-compose.test.yml"),
    ]
    openrouter_key, youtube_key, admin_password = support.create_test_environment(environment)
    try:
        support.run(compose + ["--profile", "test", "config", "--quiet"])
        support.run(
            compose
            + [
                "build",
                "migrate",
                "api",
                "worker",
                "scheduler",
                "foundation-tests",
                "openrouter-mock",
                "youtube-mock",
                "frontend",
            ]
        )
        support.run(
            compose
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
        _migration(compose, "alembic", "downgrade", "base")
        _migration(compose, "alembic", "upgrade", "head")
        _migration(compose, "python", "-m", "app.cli", "seed")
        _backup_restore_drill(compose)
        _migration(compose, "alembic", "downgrade", "20260917_0008")
        _migration(compose, "alembic", "upgrade", "head")
        _migration(compose, "python", "-m", "app.cli", "seed")
        support.run(
            compose
            + [
                "--profile",
                "test",
                "up",
                "-d",
                "--wait",
                "migrate",
                "api",
                "worker",
                "scheduler",
                "frontend",
            ]
        )
        support.wait_for_status("/health/ready", 200, "ready")
        _assert_retired_routes()
        _assert_frontend_redirect()
        support.run(compose + ["run", "--rm", "foundation-tests"])
        _stack_browser(admin_password)
        _assert_approved_models(compose)
        _dependency_outage_drill(compose, "redis")
        _dependency_outage_drill(compose, "postgres")
        logs = support.run(
            compose + ["--profile", "test", "logs", "--no-color"],
            capture_output=True,
        ).stdout
        if openrouter_key in logs or youtube_key in logs or admin_password in logs:
            raise RuntimeError("isolated stack logs exposed a generated credential")
        if "openrouter.ai/api" in logs or "www.googleapis.com/youtube" in logs:
            raise RuntimeError("isolated acceptance attempted a live provider endpoint")
    finally:
        support.run(
            compose + ["--profile", "test", "down", "--volumes", "--remove-orphans"],
            check=False,
        )
        environment.unlink(missing_ok=True)
        _cleanup_artifacts()


def _wait_url(url: str, process: subprocess.Popen[bytes], label: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited before becoming ready")
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError(f"{label} did not become ready")


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _browser_checks() -> None:
    for port in (3000, 8899):
        if _occupied(port):
            raise RuntimeError(f"Phase 12 browser port is occupied: {port}")
    before = support.repository_inventory(PROTECTED_FRONTEND_FILES)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = {
        **os.environ,
        "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8899",
        "V2_API_INTERNAL_URL": "http://127.0.0.1:8899",
        "PHASE8_EXTERNAL_SERVERS": "1",
    }
    mock: subprocess.Popen[bytes] | None = None
    frontend: subprocess.Popen[bytes] | None = None
    try:
        mock = subprocess.Popen(
            [_node(), "tests/mock-api.mjs"],
            cwd=FRONTEND,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _wait_url("http://127.0.0.1:8899/health", mock, "local V2 API mock")
        frontend = subprocess.Popen(
            [_node(), "node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1"],
            cwd=FRONTEND,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _wait_url("http://127.0.0.1:3000", frontend, "Next.js browser server")
        subprocess.run(
            [
                _node(),
                "node_modules/playwright/cli.js",
                "test",
                "tests/e2e",
                "tests/admin",
                "--config",
                "playwright.config.ts",
                "--reporter=dot",
            ],
            cwd=FRONTEND,
            env=environment,
            check=True,
        )
    finally:
        try:
            _stop_process(frontend)
            _stop_process(mock)
            support.restore_inventory(before)
        finally:
            _cleanup_artifacts()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--evidence-only", action="store_true")
    modes.add_argument("--static-only", action="store_true")
    modes.add_argument("--stack-only", action="store_true")
    modes.add_argument("--browser-only", action="store_true")
    modes.add_argument("--full", action="store_true")
    args = parser.parse_args()

    if args.evidence_only:
        _evidence_check()
        label = "production evidence"
    elif args.static_only:
        _static_checks()
        label = "static"
    elif args.stack_only:
        _stack_checks()
        label = "isolated stack"
    elif args.browser_only:
        _browser_checks()
        label = "mocked browser"
    else:
        _static_checks()
        _stack_checks()
        _browser_checks()
        label = "full project"
    print(f"Phase 12 verified: {label} acceptance passed with mocked upstreams and no paid calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
