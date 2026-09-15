from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import time

from app.config import ProcessRole, settings
from app.platform.health import (
    collect_health,
    process_dependencies_are_ready,
    scheduler_heartbeat_is_fresh,
    validate_process,
    worker_is_reachable,
)
from app.security import hash_password
from app.seed import seed_foundation


def _openrouter_catalog_refresh() -> int:
    from app.llmops.catalog import refresh_catalogs

    try:
        result = asyncio.run(refresh_catalogs(manual=True))
    except Exception as exc:
        print(f"OpenRouter catalog refresh failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _openrouter_reset_account() -> int:
    from app.llmops.accounting import reset_account_block

    reset_account_block()
    print("OpenRouter account block reset; the next paid call will revalidate the configured key.")
    return 0


def _openrouter_live_smoke(*, confirmed: bool) -> int:
    from app.llmops.live_smoke import run_live_smoke

    if not confirmed:
        print("openrouter-live-smoke requires --confirm-paid-smoke", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(run_live_smoke())
    except Exception as exc:
        print(f"OpenRouter live smoke failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _llmops_fixture(operation: str) -> int:
    from app.db.session import session_scope
    from app.llmops.contracts import (
        ChatInvocation,
        ChatMessage,
        EmbeddingInvocation,
    )
    from app.llmops.fixtures import create_llmops_fixture_attempt
    from app.llmops.gateway import OpenRouterGateway

    if settings.app_env.lower() != "test":
        print("LLMOps fixtures are only available when APP_ENV=test", file=sys.stderr)
        return 2
    with session_scope() as db:
        context, model_policy, embedding_policy = create_llmops_fixture_attempt(db)
    gateway = OpenRouterGateway()
    if operation == "chat":
        result = asyncio.run(
            gateway.chat(
                ChatInvocation(
                    context=context,
                    policy=model_policy,
                    messages=(ChatMessage(role="user", content="Return the fixture result."),),
                    response_schema={
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                    schema_name="phase3_fixture",
                    estimated_prompt_tokens=10,
                    estimated_cost_microusd=100,
                )
            )
        )
        payload = {
            "status": "succeeded",
            "operation": operation,
            "generation_id": result.generation_id,
            "usage_complete": result.usage is not None,
        }
    else:
        result = asyncio.run(
            gateway.embeddings(
                EmbeddingInvocation(
                    context=context.model_copy(update={"call_key": "fixture.embedding"}),
                    policy=embedding_policy.model_copy(update={"input_type": "search_query"}),
                    inputs=("phase three fixture",),
                    operation="query_embedding",
                    estimated_tokens=4,
                    estimated_cost_microusd=10,
                )
            )
        )
        payload = {
            "status": "succeeded",
            "operation": operation,
            "generation_id": result.generation_id,
            "vector_count": len(result.vectors),
            "usage_complete": result.usage is not None,
        }
    print(json.dumps(payload, separators=(",", ":")))
    return 0


def _runtime_fixture(scenario: str, *, wait: bool, timeout_seconds: int) -> int:
    from app.db.models import AnalysisRun, TaskRun
    from app.db.session import session_scope
    from app.runtime.contracts import RUN_TERMINAL_STATUSES, RunStatus, TaskStatus
    from app.runtime.fixtures import create_fixture_run
    from app.runtime.outbox import relay_runtime_outbox
    from app.runtime.service import reconstruct_run, request_cancellation
    from sqlalchemy import select

    if settings.app_env.lower() != "test":
        print("runtime fixtures are only available when APP_ENV=test", file=sys.stderr)
        return 2
    with session_scope() as db:
        run = create_fixture_run(db, scenario)  # type: ignore[arg-type]
        run_id = run.id
    relay_runtime_outbox()
    if not wait:
        print(json.dumps({"run_id": str(run_id), "status": "queued"}, separators=(",", ":")))
        return 0

    deadline = time.monotonic() + timeout_seconds
    cancellation_sent = False
    while time.monotonic() < deadline:
        relay_runtime_outbox()
        with session_scope() as db:
            current_status = db.scalar(select(AnalysisRun.status).where(AnalysisRun.id == run_id))
            if scenario == "cancel" and not cancellation_sent:
                running = db.scalar(
                    select(TaskRun.id).where(
                        TaskRun.run_id == run_id,
                        TaskRun.status == TaskStatus.RUNNING,
                    )
                )
                if running:
                    request_cancellation(db, run_id)
                    cancellation_sent = True
            if current_status and RunStatus(current_status) in RUN_TERMINAL_STATUSES:
                result = reconstruct_run(db, run_id)
                print(json.dumps(result, separators=(",", ":"), default=str))
                expected = RunStatus.CANCELLED if scenario == "cancel" else RunStatus.COMPLETE
                return 0 if current_status == expected else 1
        time.sleep(0.2)
    print(f"fixture run {run_id} did not finish within {timeout_seconds} seconds", file=sys.stderr)
    return 1


def _hash_password() -> int:
    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        print(hash_password(password))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _validate(role: ProcessRole) -> int:
    errors = validate_process(role)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"{role} configuration is valid")
    return 0


def _healthcheck(role: ProcessRole) -> int:
    errors = validate_process(role)
    if errors:
        return 1
    if role == "api":
        return 0 if collect_health().ready else 1
    if role == "worker":
        return 0 if process_dependencies_are_ready(role) and worker_is_reachable() else 1
    if role == "scheduler":
        return 0 if process_dependencies_are_ready(role) and scheduler_heartbeat_is_fresh() else 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ReviewLens platform commands")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("hash-password", help="Generate an Argon2id hash without echoing the password")
    subcommands.add_parser("seed", help="Idempotently seed the Phase 1 foundation")
    fixture_parser = subcommands.add_parser(
        "runtime-fixture",
        help="Run a deterministic Phase 2 fixture (APP_ENV=test only)",
    )
    fixture_parser.add_argument("--scenario", choices=("success", "retry_once", "cancel"), required=True)
    fixture_parser.add_argument("--wait", action="store_true")
    fixture_parser.add_argument("--timeout-seconds", type=int, default=60)
    subcommands.add_parser(
        "openrouter-catalog-refresh",
        help="Refresh V2 OpenRouter chat, embedding, and provider catalogs",
    )
    subcommands.add_parser(
        "openrouter-reset-account",
        help="Clear a durable 401/402 block after credentials or credits are repaired",
    )
    live_smoke = subcommands.add_parser(
        "openrouter-live-smoke",
        help="Run the explicitly enabled and cost-bounded live OpenRouter smoke",
    )
    live_smoke.add_argument("--confirm-paid-smoke", action="store_true")
    llmops_fixture = subcommands.add_parser(
        "llmops-fixture",
        help="Run a Phase 3 mocked gateway fixture (APP_ENV=test only)",
    )
    llmops_fixture.add_argument("--operation", choices=("chat", "embedding"), required=True)
    for command in ("validate", "healthcheck"):
        command_parser = subcommands.add_parser(command)
        command_parser.add_argument("role", choices=("api", "worker", "scheduler", "migrate"))
    args = parser.parse_args()

    if args.command == "hash-password":
        return _hash_password()
    if args.command == "seed":
        result = seed_foundation()
        print(result)
        return 0
    if args.command == "runtime-fixture":
        return _runtime_fixture(
            args.scenario,
            wait=args.wait,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "openrouter-catalog-refresh":
        return _openrouter_catalog_refresh()
    if args.command == "openrouter-reset-account":
        return _openrouter_reset_account()
    if args.command == "openrouter-live-smoke":
        return _openrouter_live_smoke(confirmed=args.confirm_paid_smoke)
    if args.command == "llmops-fixture":
        return _llmops_fixture(args.operation)
    if args.command == "validate":
        return _validate(args.role)
    if args.command == "healthcheck":
        return _healthcheck(args.role)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
