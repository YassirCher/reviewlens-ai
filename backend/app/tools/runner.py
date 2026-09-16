from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, text

from app.config import Settings, settings
from app.db.models import (
    AgentDefinition,
    AgentVersion,
    AgentVersionTool,
    AnalysisRun,
    ConfigurationSnapshot,
    TaskAttempt,
    TaskRun,
    TaskRunTool,
    ToolDefinition,
    ToolInvocation,
    ToolVersion,
    Workspace,
)
from app.db.session import session_scope
from app.runtime.contracts import canonical_json_hash
from app.tools import graph as graph_tools
from app.tools.contracts import ToolExecutionContext
from app.tools.evidence import validate_evidence
from app.tools.errors import ToolAuthorizationError, ToolExecutionError
from app.tools.registry import HANDLER_REGISTRY, TOOL_REGISTRY, ToolSpec
from app.tools.scoring import preview_scoring
from app.tools.youtube import YouTubeDataClient, fetch_transcript

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_metadata(tool_key: str, output: BaseModel) -> dict[str, Any]:
    payload = output.model_dump(mode="json")
    metadata: dict[str, Any] = {"tool_key": tool_key}
    for field in (
        "queries_executed",
        "comments_sampled",
        "unavailable",
        "queued",
        "degraded",
        "valid",
        "publishable",
        "overall_score",
        "confidence",
    ):
        if field in payload:
            metadata[field] = payload[field]
    for field in ("hits", "videos", "segments", "comments", "nodes", "relations", "edge_ids", "node_version_ids"):
        value = payload.get(field)
        if isinstance(value, list):
            metadata[f"{field}_count"] = len(value)
    return metadata


def _authorize(
    attempt_id: uuid.UUID,
    tool_key: str,
    payload: dict[str, Any],
    call_key: str,
) -> tuple[ToolSpec, ToolExecutionContext, str]:
    spec = TOOL_REGISTRY.get(tool_key)
    if spec is None:
        raise ToolAuthorizationError("tool_not_registered")
    with session_scope() as db:
        attempt = db.get(TaskAttempt, attempt_id)
        task = db.get(TaskRun, attempt.task_run_id) if attempt else None
        run = db.get(AnalysisRun, task.run_id) if task else None
        snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id) if run else None
        if not attempt or not task or not run or not snapshot:
            raise ToolAuthorizationError("tool_attempt_not_authorized")
        if attempt.status != "running" or task.status != "running" or run.status != "running":
            raise ToolAuthorizationError("tool_attempt_not_running")
        definition = db.scalar(select(ToolDefinition).where(ToolDefinition.key == tool_key))
        version = db.scalar(
            select(ToolVersion).where(
                ToolVersion.definition_id == definition.id if definition else False,
                ToolVersion.semantic_version == spec.semantic_version,
            )
        )
        if not definition or not version or version.lifecycle != "published" or version.content_hash != spec.content_hash:
            raise ToolAuthorizationError("tool_version_not_published")
        snapshot_tools = {item["id"]: item.get("content_hash") for item in snapshot.snapshot.get("tools", [])}
        if snapshot_tools.get(str(version.id)) != version.content_hash:
            raise ToolAuthorizationError("tool_not_in_run_snapshot")

        role = "deterministic"
        if task.agent_version_id:
            agent = db.get(AgentVersion, task.agent_version_id)
            agent_definition = db.get(AgentDefinition, agent.definition_id) if agent else None
            allowed = db.get(
                AgentVersionTool,
                {"agent_version_id": task.agent_version_id, "tool_version_id": version.id},
            )
            if not agent or not agent_definition or not allowed:
                raise ToolAuthorizationError("tool_not_in_agent_allowlist")
            role = agent_definition.key
        else:
            declared = db.get(
                TaskRunTool,
                {"task_run_id": task.id, "tool_version_id": version.id},
            )
            legacy_direct = task.tool_version_id == version.id and task.handler == spec.handler
            if not declared and not legacy_direct:
                raise ToolAuthorizationError("deterministic_tool_mismatch")
        if role not in spec.allowed_roles:
            raise ToolAuthorizationError("tool_role_not_allowed")
        if tool_key == "youtube.comments" and not bool(run.requested_options.get("analyze_comments", False)):
            raise ToolAuthorizationError("comments_not_enabled")
        workspace_id = db.scalar(select(Workspace.id).where(Workspace.run_id == run.id))
        input_hash = canonical_json_hash(payload)
        idempotency_key = canonical_json_hash(
            [str(attempt.id), str(version.id), call_key, input_hash]
        )
        context = ToolExecutionContext(
            run_id=run.id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            tool_version_id=version.id,
            agent_version_id=task.agent_version_id,
            workspace_id=workspace_id,
            deadline_at=task.deadline_at,
            comments_enabled=bool(run.requested_options.get("analyze_comments", False)),
            idempotency_key=idempotency_key,
        )
        return spec, context, input_hash


def _begin_invocation(
    spec: ToolSpec,
    context: ToolExecutionContext,
    input_hash: str,
) -> ToolInvocation:
    with session_scope() as db:
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"tool:{context.run_id}:{spec.key}"},
        )
        run_status = db.scalar(
            select(AnalysisRun.status).where(AnalysisRun.id == context.run_id)
        )
        task_status = db.scalar(
            select(TaskRun.status).where(TaskRun.id == context.task_run_id)
        )
        attempt_status = db.scalar(
            select(TaskAttempt.status).where(TaskAttempt.id == context.task_attempt_id)
        )
        if (run_status, task_status, attempt_status) != ("running", "running", "running"):
            raise ToolExecutionError("tool_attempt_not_running", category="cancelled")
        existing = db.scalar(
            select(ToolInvocation).where(
                ToolInvocation.idempotency_key == context.idempotency_key
            )
        )
        if existing:
            raise ToolExecutionError("duplicate_tool_invocation", category="idempotency")
        running = db.scalar(
            select(func.count())
            .select_from(ToolInvocation)
            .where(
                ToolInvocation.run_id == context.run_id,
                ToolInvocation.tool_key == spec.key,
                ToolInvocation.status == "running",
            )
        )
        if int(running or 0) >= spec.max_concurrency:
            raise ToolExecutionError("tool_concurrency_exhausted", category="limit")
        invocation = ToolInvocation(
            id=uuid.uuid4(),
            run_id=context.run_id,
            task_run_id=context.task_run_id,
            task_attempt_id=context.task_attempt_id,
            agent_version_id=context.agent_version_id,
            tool_version_id=context.tool_version_id,
            tool_key=spec.key,
            idempotency_key=context.idempotency_key,
            input_hash=input_hash,
            safe_metadata={"tool_key": spec.key},
            status="running",
            started_at=utc_now(),
        )
        db.add(invocation)
        db.flush()
        return invocation


async def _admit_invocation(
    spec: ToolSpec,
    context: ToolExecutionContext,
    input_hash: str,
) -> tuple[ToolInvocation, datetime]:
    """Wait for a bounded per-run tool slot without changing immutable tool limits."""
    deadline = min(
        context.deadline_at,
        utc_now() + timedelta(seconds=spec.timeout_seconds),
    )
    while True:
        try:
            return _begin_invocation(spec, context, input_hash), deadline
        except ToolExecutionError as exc:
            if exc.code != "tool_concurrency_exhausted":
                raise
            remaining = (deadline - utc_now()).total_seconds()
            if remaining <= 0:
                raise ToolExecutionError(
                    "tool_concurrency_exhausted",
                    category="timeout",
                    retryable=True,
                ) from exc
            await asyncio.sleep(min(0.1, remaining))


def _finish_invocation(
    invocation_id: uuid.UUID,
    *,
    status: str,
    output: BaseModel | None = None,
    retry_count: int = 0,
    error: ToolExecutionError | None = None,
) -> None:
    now = utc_now()
    with session_scope() as db:
        invocation = db.scalar(
            select(ToolInvocation)
            .where(ToolInvocation.id == invocation_id)
            .with_for_update()
        )
        if invocation is None or invocation.status != "running":
            return
        invocation.status = status
        invocation.ended_at = now
        started = invocation.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        invocation.duration_ms = max(0, round((now - started).total_seconds() * 1000))
        invocation.retry_count = retry_count
        if output is not None:
            invocation.output_hash = canonical_json_hash(output.model_dump(mode="json"))
            invocation.safe_metadata = _safe_metadata(invocation.tool_key, output)
        if error is not None:
            invocation.error_category = error.category[:80]
            invocation.error_code = error.code[:120]
            invocation.safe_metadata = {
                "tool_key": invocation.tool_key,
                **{
                    key: value
                    for key, value in error.safe_metadata.items()
                    if isinstance(value, (str, int, float, bool, type(None)))
                },
            }


async def _dispatch(
    spec: ToolSpec,
    context: ToolExecutionContext,
    invocation_id: uuid.UUID,
    request: BaseModel,
    *,
    config: Settings,
) -> tuple[BaseModel, int]:
    if spec.key.startswith("youtube.") and spec.key != "youtube.transcript":
        client = YouTubeDataClient(
            invocation_id,
            config=config,
            deadline_at=context.deadline_at,
            max_attempts=spec.max_attempts,
        )
        try:
            if spec.key == "youtube.search":
                result = await client.search(request)  # type: ignore[arg-type]
            elif spec.key == "youtube.video_details":
                result = await client.video_details(request)  # type: ignore[arg-type]
            else:
                result = await client.comments(request)  # type: ignore[arg-type]
            return result, client.retry_count
        finally:
            await client.close()
    if spec.key == "youtube.transcript":
        last_error: ToolExecutionError | None = None
        for attempt in range(1, spec.max_attempts + 1):
            try:
                return await fetch_transcript(request, config=config), attempt - 1  # type: ignore[arg-type]
            except ToolExecutionError as exc:
                last_error = exc
                if not exc.retryable or attempt >= spec.max_attempts:
                    raise
                delay = min(
                    config.youtube_retry_max_seconds,
                    config.youtube_retry_base_seconds * (2 ** (attempt - 1)),
                )
                now = utc_now()
                deadline = context.deadline_at
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                if now.timestamp() + delay >= deadline.timestamp():
                    raise ToolExecutionError(
                        "youtube_retry_deadline_exceeded", category="timeout"
                    ) from exc
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error
    if spec.key == "scoring.preview":
        return preview_scoring(request), 0  # type: ignore[arg-type]
    with session_scope() as db:
        if spec.key == "graph.get_nodes":
            return graph_tools.get_nodes(db, context, request, config=config), 0  # type: ignore[arg-type]
        if spec.key == "graph.query_relations":
            return graph_tools.query_relations(db, context, request), 0  # type: ignore[arg-type]
        if spec.key == "graph.create_nodes":
            return graph_tools.create_nodes(db, context, request, config=config), 0  # type: ignore[arg-type]
        if spec.key == "graph.create_edges":
            return graph_tools.create_edges(db, context, request), 0  # type: ignore[arg-type]
        if spec.key == "vector.request_upsert":
            return graph_tools.request_vector_upsert(db, context, request), 0  # type: ignore[arg-type]
        if spec.key == "vector.search":
            return await graph_tools.vector_search(db, context, request, config=config), 0  # type: ignore[arg-type]
        if spec.key == "evidence.validate":
            return validate_evidence(db, context, request, config=config), 0  # type: ignore[arg-type]
    raise ToolExecutionError("tool_handler_missing", category="configuration")


async def invoke_tool(
    attempt_id: uuid.UUID,
    tool_key: str,
    payload: dict[str, Any],
    *,
    call_key: str = "primary",
    config: Settings = settings,
) -> dict[str, Any]:
    spec, context, input_hash = _authorize(attempt_id, tool_key, payload, call_key)
    try:
        request = spec.input_model.model_validate(payload)
    except ValidationError as exc:
        raise ToolExecutionError("tool_input_invalid", category="validation") from exc
    invocation, deadline = await _admit_invocation(spec, context, input_hash)
    retry_count = 0
    try:
        from app.runtime.service import task_is_cancelled

        if task_is_cancelled(context.run_id, context.task_run_id):
            raise ToolExecutionError("tool_attempt_not_running", category="cancelled")
        result, retry_count = await asyncio.wait_for(
            _dispatch(spec, context, invocation.id, request, config=config),
            timeout=max(0, (deadline - utc_now()).total_seconds()),
        )
        validated = spec.output_model.model_validate(result)
        encoded = json.dumps(
            validated.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        maximum = min(spec.max_output_bytes, config.youtube_tool_max_output_bytes)
        if len(encoded) > maximum:
            raise ToolExecutionError("tool_output_too_large", category="limit")
    except TimeoutError:
        error = ToolExecutionError("tool_timeout", category="timeout", retryable=True)
        _finish_invocation(invocation.id, status="timed_out", retry_count=retry_count, error=error)
        raise error
    except ToolExecutionError as exc:
        status = "cancelled" if exc.category == "cancelled" else "failed"
        _finish_invocation(invocation.id, status=status, retry_count=retry_count, error=exc)
        raise
    except Exception as exc:
        logger.error(
            "Tool execution failed invocation_id=%s tool_key=%s exception_type=%s",
            invocation.id,
            tool_key,
            type(exc).__name__,
        )
        error = ToolExecutionError("tool_execution_failed", category="internal")
        _finish_invocation(invocation.id, status="failed", retry_count=retry_count, error=error)
        raise error from exc
    _finish_invocation(
        invocation.id,
        status="succeeded",
        output=validated,
        retry_count=retry_count,
    )
    return validated.model_dump(mode="json")


def execute_registered_tool(
    attempt_id: uuid.UUID,
    handler: str,
    payload: dict[str, Any],
    *,
    config: Settings = settings,
) -> dict[str, Any]:
    tool_key = HANDLER_REGISTRY.get(handler)
    if tool_key is None:
        raise ToolExecutionError("unknown_tool_handler", category="configuration")
    return asyncio.run(
        invoke_tool(
            attempt_id,
            tool_key,
            payload,
            call_key=handler,
            config=config,
        )
    )


def recover_stale_invocations() -> int:
    recovered = 0
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(ToolInvocation)
                .join(TaskAttempt, TaskAttempt.id == ToolInvocation.task_attempt_id)
                .where(
                    ToolInvocation.status == "running",
                    TaskAttempt.status != "running",
                )
                .with_for_update(skip_locked=True)
            )
        )
        now = utc_now()
        for invocation in rows:
            attempt = db.get(TaskAttempt, invocation.task_attempt_id)
            invocation.status = "cancelled" if attempt and attempt.status == "cancelled" else "failed"
            invocation.error_category = "worker_interrupted"
            invocation.error_code = "parent_attempt_terminal"
            invocation.ended_at = now
            started = invocation.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            invocation.duration_ms = max(0, round((now - started).total_seconds() * 1000))
            recovered += 1
    return recovered
