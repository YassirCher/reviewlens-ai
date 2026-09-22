from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import time
import uuid
from pathlib import Path

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


def _export_phase12_evidence(
    observation_id: str,
    *,
    attestation_reference: str,
    output: Path,
) -> int:
    from app.admin.retirement import (
        RetirementEvidenceError,
        build_evidence_document,
        write_evidence_document,
    )
    from app.db.session import session_scope

    try:
        parsed_id = uuid.UUID(observation_id)
        with session_scope() as db:
            document = build_evidence_document(
                db,
                parsed_id,
                attestation_reference=attestation_reference,
            )
        write_evidence_document(document, output)
    except (ValueError, RetirementEvidenceError) as exc:
        print(f"Phase 12 evidence export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "exported", "path": str(output), "sha256": document["sha256"]}))
    return 0


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
        run = create_fixture_run(db, scenario)
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


def _context_fixture(scenario: str) -> int:
    from app.db.models import Workspace
    from app.db.session import session_scope
    from app.knowledge.fixtures import create_context_fixture
    from app.knowledge.projections import rebuild_neo4j_projection, rebuild_qdrant_projection
    from app.knowledge.service import export_workspace, reconcile_workspace

    if settings.app_env.lower() != "test":
        print("context fixtures are only available when APP_ENV=test", file=sys.stderr)
        return 2
    with session_scope() as db:
        fixture = create_context_fixture(db)

    projection_result: dict = {"mode": "degraded", "neo4j_nodes": 0, "qdrant_points": 0}
    if scenario == "roundtrip":
        async def vectorizer(values: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0, float(index + 1), float(len(value) % 17)) for index, value in enumerate(values))

        with session_scope() as db:
            neo4j_result = rebuild_neo4j_projection(db, fixture.workspace_id)
            qdrant_result = asyncio.run(
                rebuild_qdrant_projection(
                    db,
                    fixture.workspace_id,
                    fixture.embedding_policy_version_id,
                    vectorizer,
                )
            )
            projection_result = {
                "mode": "ready",
                "neo4j_nodes": neo4j_result["nodes"],
                "qdrant_points": qdrant_result["points"],
            }
    else:
        with session_scope() as db:
            workspace = db.get(Workspace, fixture.workspace_id)
            assert workspace is not None
            workspace.neo4j_status = "degraded"
            workspace.qdrant_status = "degraded"
            workspace.status = "degraded"

    with session_scope() as db:
        reconciliation = reconcile_workspace(db, fixture.workspace_id)
        export = export_workspace(db, fixture.workspace_id)
    print(
        json.dumps(
            {
                "status": "succeeded",
                "scenario": scenario,
                "workspace_id": str(fixture.workspace_id),
                "nodes": fixture.node_count,
                "edges": fixture.edge_count,
                "retrieval_mode": fixture.retrieval_mode,
                "manifest_id": str(fixture.manifest_id),
                "reconciliation": reconciliation,
                "export_created": export.exists(),
                "projection": projection_result,
            },
            separators=(",", ":"),
        )
    )
    return 0


def _research_fixture(scenario: str) -> int:
    from sqlalchemy import func, select

    from app.db.models import ContextNode, ToolInvocation
    from app.db.session import session_scope
    from app.tools.contracts import ResearchQueryPlan
    from app.tools.fixtures import create_research_fixture_attempt, finish_research_fixture
    from app.tools.research import execute_research

    if settings.app_env.lower() != "test":
        print("research fixtures are only available when APP_ENV=test", file=sys.stderr)
        return 2
    with session_scope() as db:
        run, task, attempt = create_research_fixture_attempt(db, scenario)
        run_id, task_id, attempt_id = run.id, task.id, attempt.id
        analyze_comments = bool(run.requested_options.get("analyze_comments"))
        product = run.product_input
    plan = ResearchQueryPlan(
        canonical_product=product,
        queries=(f"{product} review", f"{product} long term review"),
        requested_source_count=5,
        requested_language="en",
        analyze_comments=analyze_comments,
    )
    try:
        result = asyncio.run(execute_research(attempt_id, plan))
    except Exception as exc:
        print(f"Research fixture failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    with session_scope() as db:
        finish_research_fixture(
            db,
            run_id,
            task_id,
            attempt_id,
            result.model_dump(mode="json"),
        )
        invocation_count = db.scalar(
            select(func.count()).select_from(ToolInvocation).where(ToolInvocation.run_id == run_id)
        )
        node_count = db.scalar(
            select(func.count()).select_from(ContextNode).where(
                ContextNode.workspace_id == result.workspace_id
            )
        )
    print(
        json.dumps(
            {
                "status": "succeeded",
                "scenario": scenario,
                "run_id": str(run_id),
                "requested_sources": result.requested_source_count,
                "selected_sources": len(result.selected_sources),
                "warnings": list(result.warning_codes),
                "tool_invocations": invocation_count,
                "context_nodes": node_count,
                "comments_enabled": analyze_comments,
                "paid_calls": 0,
            },
            separators=(",", ":"),
        )
    )
    return 0


def _youtube_live_smoke(video_id: str, *, confirmed: bool) -> int:
    from app.tools.live_smoke import run_youtube_live_smoke

    if not confirmed:
        print("youtube-live-smoke requires --confirm-live-smoke", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(run_youtube_live_smoke(video_id))
    except Exception as exc:
        print(f"YouTube live smoke failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _analysis_config_seed() -> int:
    from app.analysis.configuration import seed_analysis_configuration
    from app.db.session import session_scope

    try:
        if settings.openrouter_api_key:
            try:
                from app.llmops.catalog import refresh_catalogs

                asyncio.run(refresh_catalogs(manual=False))
            except Exception as refresh_exc:
                print(f"Catalog refresh pre-seed note: {refresh_exc}", file=sys.stderr)
        with session_scope() as db:
            result = seed_analysis_configuration(db)
    except Exception as exc:
        print(f"Phase 6 configuration seed failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _analysis_fixture(scenario: str, *, wait: bool, timeout_seconds: int) -> int:
    from sqlalchemy import func, select

    from app.analysis.configuration import seed_analysis_configuration
    from app.db.models import AnalysisRun, Report, TaskAttempt, TaskRun, UsageEvent
    from app.db.session import session_scope
    from app.runtime.contracts import RUN_TERMINAL_STATUSES, RunStatus, TaskStatus
    from app.runtime.outbox import relay_runtime_outbox
    from app.runtime.service import create_run, request_cancellation

    if settings.app_env.lower() != "test":
        print("analysis fixtures are only available when APP_ENV=test", file=sys.stderr)
        return 2
    comments = scenario == "comments"
    source_count = 5
    with session_scope() as db:
        seed_analysis_configuration(db)
        run = create_run(
            db,
            product_name=f"Phase 6 {scenario.replace('_', ' ')} fixture",
            initiator_type="system_fixture",
            requested_options={
                "scenario": scenario,
                "source_count": source_count,
                "language": "en",
                "analyze_comments": comments,
                "contacts_mocked_upstreams": True,
            },
        )
        run_id = run.id
    relay_runtime_outbox(run_id=run_id)
    if not wait:
        print(json.dumps({"run_id": str(run_id), "status": "queued"}, separators=(",", ":")))
        return 0

    deadline = time.monotonic() + timeout_seconds
    cancellation_sent = False
    while time.monotonic() < deadline:
        relay_runtime_outbox(run_id=run_id)
        with session_scope() as db:
            current = db.get(AnalysisRun, run_id)
            if current is None:
                print("analysis fixture run disappeared", file=sys.stderr)
                return 1
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
            if RunStatus(current.status) in RUN_TERMINAL_STATUSES:
                report = db.scalar(select(Report).where(Report.run_id == run_id))
                model_attempts = int(
                    db.scalar(
                        select(func.count())
                        .select_from(UsageEvent)
                        .where(UsageEvent.run_id == run_id, UsageEvent.operation == "chat")
                    )
                    or 0
                )
                corrections = int(
                    db.scalar(
                        select(func.count())
                        .select_from(TaskAttempt)
                        .join(TaskRun, TaskRun.id == TaskAttempt.task_run_id)
                        .where(TaskRun.run_id == run_id, TaskAttempt.attempt_kind == "correction")
                    )
                    or 0
                )
                payload = {
                    "run_id": str(run_id),
                    "scenario": scenario,
                    "status": current.status,
                    "report_id": str(report.id) if report else None,
                    "report_status": report.payload.get("status") if report else None,
                    "audit_status": report.audit_status if report else None,
                    "model_requests": model_attempts,
                    "correction_attempts": corrections,
                    "live_calls": 0,
                }
                if RunStatus(current.status) == RunStatus.FAILED:
                    failed_tasks = db.scalars(
                        select(TaskRun).where(
                            TaskRun.run_id == run_id,
                            TaskRun.status.in_((TaskStatus.FAILED, TaskStatus.TIMED_OUT)),
                        )
                    ).all()
                    payload["failed_tasks"] = []
                    for failed_task in failed_tasks:
                        latest_attempt = db.scalar(
                            select(TaskAttempt)
                            .where(TaskAttempt.task_run_id == failed_task.id)
                            .order_by(TaskAttempt.attempt_number.desc())
                            .limit(1)
                        )
                        payload["failed_tasks"].append(
                            {
                                "task_key": failed_task.workflow_task_key,
                                "error_code": latest_attempt.error_code if latest_attempt else None,
                                "error_category": latest_attempt.error_category if latest_attempt else None,
                            }
                        )
                print(json.dumps(payload, separators=(",", ":")))
                expected = {
                    "complete": {RunStatus.COMPLETE},
                    "comments": {RunStatus.COMPLETE},
                    "partial": {RunStatus.PARTIAL},
                    "retry_once": {RunStatus.COMPLETE, RunStatus.PARTIAL},
                    "audit_correction": {RunStatus.COMPLETE, RunStatus.PARTIAL},
                    "audit_fail": {RunStatus.FAILED},
                    "cancel": {RunStatus.CANCELLED},
                }[scenario]
                published_expected = scenario not in {"audit_fail", "cancel"}
                return 0 if RunStatus(current.status) in expected and bool(report) == published_expected else 1
        time.sleep(0.2)
    print(f"analysis fixture run {run_id} did not finish within {timeout_seconds} seconds", file=sys.stderr)
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
    context_fixture = subcommands.add_parser(
        "context-fixture",
        help="Run a deterministic Phase 4 graph/retrieval fixture (APP_ENV=test only)",
    )
    context_fixture.add_argument("--scenario", choices=("roundtrip", "degraded"), required=True)
    research_fixture = subcommands.add_parser(
        "research-fixture",
        help="Run a deterministic Phase 5 research fixture (APP_ENV=test only)",
    )
    research_fixture.add_argument(
        "--scenario",
        choices=("complete", "missing_transcript", "comments_off", "partial"),
        required=True,
    )
    youtube_smoke = subcommands.add_parser(
        "youtube-live-smoke",
        help="Run an explicitly enabled one-video YouTube metadata/transcript smoke",
    )
    youtube_smoke.add_argument("--confirm-live-smoke", action="store_true")
    youtube_smoke.add_argument("--video-id", required=True)
    subcommands.add_parser(
        "analysis-config-seed",
        help="Validate and publish the bounded analysis agents and workflow",
    )
    retirement_export = subcommands.add_parser(
        "phase12-export-cutover-evidence",
        help="Export sanitized retirement evidence from a passed production observation",
    )
    retirement_export.add_argument("--observation-id", required=True)
    retirement_export.add_argument("--attestation-reference", required=True)
    retirement_export.add_argument("--output", type=Path, required=True)
    analysis_fixture = subcommands.add_parser(
        "analysis-fixture",
        help="Run the mocked bounded Phase 6 workflow (APP_ENV=test only)",
    )
    analysis_fixture.add_argument(
        "--scenario",
        choices=("complete", "comments", "partial", "retry_once", "audit_correction", "audit_fail", "cancel"),
        required=True,
    )
    analysis_fixture.add_argument("--wait", action="store_true")
    analysis_fixture.add_argument("--timeout-seconds", type=int, default=180)
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
    if args.command == "context-fixture":
        return _context_fixture(args.scenario)
    if args.command == "research-fixture":
        return _research_fixture(args.scenario)
    if args.command == "youtube-live-smoke":
        return _youtube_live_smoke(args.video_id, confirmed=args.confirm_live_smoke)
    if args.command == "analysis-config-seed":
        return _analysis_config_seed()
    if args.command == "phase12-export-cutover-evidence":
        return _export_phase12_evidence(
            args.observation_id,
            attestation_reference=args.attestation_reference,
            output=args.output,
        )
    if args.command == "analysis-fixture":
        return _analysis_fixture(
            args.scenario,
            wait=args.wait,
            timeout_seconds=args.timeout_seconds,
        )
    if args.command == "validate":
        return _validate(args.role)
    if args.command == "healthcheck":
        return _healthcheck(args.role)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
