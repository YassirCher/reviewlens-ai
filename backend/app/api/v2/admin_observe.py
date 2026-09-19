from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin.common import not_found, page_rows, require_admin_mutation
from app.api.v2.dependencies import get_v2_db, require_admin
from app.db.models import (
    AdminSession, AnalysisRun, AuditEvent, ConfigurationSnapshot, ContextManifest,
    ReportPublication, TaskAttempt, TaskDependency, TaskRun, ToolDefinition, ToolInvocation,
    ToolVersion, UsageEvent, Workspace,
)
from app.errors import V2Error
from app.runtime.service import RuntimeTaskError, request_cancellation, retry_task
from app.services.admin_auth import AuthenticatedAdmin
from app.services.audit_service import add_audit_event

router = APIRouter(prefix="/admin", tags=["admin-observe"])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _usage(db: Session, run_id: uuid.UUID) -> dict:
    row = db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
            func.coalesce(func.sum(UsageEvent.total_cost_microusd), 0),
            func.count(UsageEvent.id),
            func.count(UsageEvent.id).filter(UsageEvent.usage_status == "pending"),
        ).where(UsageEvent.run_id == run_id)
    ).one()
    return {
        "total_tokens": int(row[0]), "total_cost_microusd": int(row[1]),
        "model_call_count": int(row[2]), "pending_usage_count": int(row[3]),
    }


def _run_row(db: Session, run: AnalysisRun) -> dict:
    usage = _usage(db, run.id)
    return {
        "id": str(run.id), "product": run.product_input, "initiator_type": run.initiator_type,
        "status": run.status, "coverage": run.coverage, "warnings": list((run.warning_summary or {}).keys()),
        "created_at": _iso(run.created_at), "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "duration_ms": int((run.completed_at - run.started_at).total_seconds() * 1000)
        if run.completed_at and run.started_at else None,
        **usage,
    }


@router.get("/runs")
def list_runs(
    status: str | None = None,
    initiator_type: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_v2_db),
    _: AuthenticatedAdmin = Depends(require_admin),
) -> dict:
    filters = {"status": status, "initiator_type": initiator_type, "q": q}
    statement = select(AnalysisRun)
    if status:
        statement = statement.where(AnalysisRun.status == status)
    if initiator_type:
        statement = statement.where(AnalysisRun.initiator_type == initiator_type)
    if q:
        needle = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(AnalysisRun.product_input.ilike(f"%{needle}%", escape="\\"))
    page = page_rows(db, AnalysisRun, statement, limit=limit, cursor=cursor, filters=filters)
    return {"items": [_run_row(db, row) for row in page["rows"]], "next_cursor": page["next_cursor"]}


@router.get("/runs/{run_id}")
def read_run(run_id: uuid.UUID, db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise not_found("run")
    snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id)
    tasks = list(db.scalars(select(TaskRun).where(TaskRun.run_id == run.id).order_by(TaskRun.created_at, TaskRun.id)))
    task_ids = [task.id for task in tasks]
    attempts = list(db.scalars(select(TaskAttempt).where(TaskAttempt.task_run_id.in_(task_ids)).order_by(TaskAttempt.created_at))) if task_ids else []
    dependencies = list(db.execute(select(TaskDependency).where(TaskDependency.downstream_task_id.in_(task_ids)))) if task_ids else []
    usage = list(db.scalars(select(UsageEvent).where(UsageEvent.run_id == run.id).order_by(UsageEvent.created_at, UsageEvent.id)))
    invocations = list(db.scalars(select(ToolInvocation).where(ToolInvocation.run_id == run.id).order_by(ToolInvocation.started_at)))
    manifests = list(db.scalars(select(ContextManifest).where(ContextManifest.task_attempt_id.in_([a.id for a in attempts])))) if attempts else []
    workspace = db.scalar(select(Workspace).where(Workspace.run_id == run.id))
    publication = db.scalar(select(ReportPublication).where(ReportPublication.run_id == run.id))
    return {
        **_run_row(db, run),
        "snapshot": {"id": str(snapshot.id), "content_hash": snapshot.content_hash,
                     "workflow_version_id": str(snapshot.workflow_version_id),
                     "budget_policy_version_id": str(snapshot.budget_policy_version_id),
                     "embedding_policy_version_id": str(snapshot.embedding_policy_version_id) if snapshot.embedding_policy_version_id else None,
                     "agents": snapshot.snapshot.get("agents", []), "model_policies": snapshot.snapshot.get("model_policies", [])} if snapshot else None,
        "tasks": [{"id": str(t.id), "task_key": t.workflow_task_key, "status": t.status,
                   "agent_version_id": str(t.agent_version_id) if t.agent_version_id else None,
                   "current_attempt": t.current_attempt, "max_attempts": t.max_attempts,
                   "started_at": _iso(t.started_at), "completed_at": _iso(t.completed_at)} for t in tasks],
        "dependencies": [{"from": str(d.upstream_task_id), "to": str(d.downstream_task_id)} for (d,) in dependencies],
        "attempts": [{"id": str(a.id), "task_run_id": str(a.task_run_id), "number": a.attempt_number,
                      "kind": a.attempt_kind, "status": a.status, "error_category": a.error_category,
                      "error_code": a.error_code, "retryable": a.retryable, "duration_ms": a.duration_ms,
                      "prompt_hash": a.prompt_hash, "input_hash": a.input_hash, "output_hash": a.output_hash,
                      "started_at": _iso(a.started_at), "ended_at": _iso(a.ended_at)} for a in attempts],
        "usage": [{"id": str(u.id), "task_attempt_id": str(u.task_attempt_id), "agent_version_id": str(u.agent_version_id),
                   "operation": u.operation, "requested_models": u.requested_models, "actual_model": u.actual_model,
                   "actual_provider": u.actual_provider, "prompt_tokens": u.prompt_tokens,
                   "completion_tokens": u.completion_tokens, "reasoning_tokens": u.reasoning_tokens,
                   "cached_tokens": u.cached_tokens, "total_tokens": u.total_tokens,
                   "total_cost_microusd": u.total_cost_microusd, "status": u.status,
                   "usage_status": u.usage_status, "error_category": u.error_category,
                   "latency_ms": u.latency_ms, "created_at": _iso(u.created_at)} for u in usage],
        "tool_invocations": [{"id": str(i.id), "task_attempt_id": str(i.task_attempt_id), "tool_key": i.tool_key,
                              "tool_version_id": str(i.tool_version_id), "status": i.status,
                              "duration_ms": i.duration_ms, "input_hash": i.input_hash, "output_hash": i.output_hash,
                              "error_category": i.error_category} for i in invocations],
        "context_manifests": [{"id": str(m.id), "task_attempt_id": str(m.task_attempt_id),
                               "retrieval_mode": m.retrieval_mode, "token_budget": m.token_budget,
                               "estimated_tokens": m.estimated_tokens, "rendered_hash": m.rendered_hash} for m in manifests],
        "workspace_id": str(workspace.id) if workspace else None,
        "report_id": str(run.report_id) if run.report_id else None,
        "public_report_active": bool(publication and publication.revoked_at is None),
    }


@router.get("/tasks/{task_id}")
def read_task(task_id: uuid.UUID, db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    task = db.get(TaskRun, task_id)
    if task is None:
        raise not_found("task")
    attempts = list(db.scalars(select(TaskAttempt).where(TaskAttempt.task_run_id == task.id).order_by(TaskAttempt.attempt_number)))
    return {"id": str(task.id), "run_id": str(task.run_id), "task_key": task.workflow_task_key,
            "status": task.status, "max_attempts": task.max_attempts, "current_attempt": task.current_attempt,
            "attempts": [{"id": str(a.id), "number": a.attempt_number, "status": a.status,
                          "error_category": a.error_category, "error_code": a.error_code,
                          "retryable": a.retryable, "duration_ms": a.duration_ms,
                          "started_at": _iso(a.started_at), "ended_at": _iso(a.ended_at)} for a in attempts]}


@router.get("/usage-events")
def list_usage_events(run_id: uuid.UUID | None = None, operation: str | None = None,
                      cursor: str | None = None, limit: int = Query(default=25, ge=1, le=100),
                      db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    filters = {"run_id": str(run_id) if run_id else None, "operation": operation}
    statement = select(UsageEvent)
    if run_id:
        statement = statement.where(UsageEvent.run_id == run_id)
    if operation:
        statement = statement.where(UsageEvent.operation == operation)
    page = page_rows(db, UsageEvent, statement, limit=limit, cursor=cursor, filters=filters)
    return {"items": [{"id": str(row.id), "run_id": str(row.run_id),
                       "task_attempt_id": str(row.task_attempt_id), "agent_version_id": str(row.agent_version_id),
                       "operation": row.operation, "actual_model": row.actual_model,
                       "actual_provider": row.actual_provider, "status": row.status,
                       "usage_status": row.usage_status, "total_tokens": row.total_tokens,
                       "total_cost_microusd": row.total_cost_microusd,
                       "created_at": _iso(row.created_at)} for row in page["rows"]],
            "next_cursor": page["next_cursor"]}


@router.get("/task-attempts")
def list_task_attempts(run_id: uuid.UUID | None = None, task_run_id: uuid.UUID | None = None,
                       status: str | None = None, cursor: str | None = None,
                       limit: int = Query(default=25, ge=1, le=100),
                       db: Session = Depends(get_v2_db),
                       _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    filters = {"run_id": str(run_id) if run_id else None,
               "task_run_id": str(task_run_id) if task_run_id else None, "status": status}
    statement = select(TaskAttempt)
    if run_id:
        statement = statement.join(TaskRun, TaskRun.id == TaskAttempt.task_run_id).where(TaskRun.run_id == run_id)
    if task_run_id:
        statement = statement.where(TaskAttempt.task_run_id == task_run_id)
    if status:
        statement = statement.where(TaskAttempt.status == status)
    page = page_rows(db, TaskAttempt, statement, limit=limit, cursor=cursor, filters=filters)
    return {"items": [{"id": str(a.id), "task_run_id": str(a.task_run_id),
                       "number": a.attempt_number, "kind": a.attempt_kind,
                       "status": a.status, "error_category": a.error_category,
                       "error_code": a.error_code, "retryable": a.retryable,
                       "duration_ms": a.duration_ms, "input_hash": a.input_hash,
                       "output_hash": a.output_hash, "created_at": _iso(a.created_at)}
                      for a in page["rows"]], "next_cursor": page["next_cursor"]}


@router.get("/tools")
def list_tools(db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    definitions = list(db.scalars(select(ToolDefinition).order_by(ToolDefinition.key)))
    return {"items": [{"id": str(row.id), "key": row.key, "name": row.name,
                       "versions": [{"id": str(v.id), "number": v.version_number,
                                     "semantic_version": v.semantic_version,
                                     "lifecycle": v.lifecycle, "content_hash": v.content_hash,
                                     "capability_metadata": v.capability_metadata}
                                    for v in db.scalars(select(ToolVersion).where(ToolVersion.definition_id == row.id)
                                                        .order_by(ToolVersion.version_number.desc()))]}
                      for row in definitions]}


@router.get("/tool-invocations")
def list_tool_invocations(run_id: uuid.UUID | None = None, tool_key: str | None = None,
                          status: str | None = None, cursor: str | None = None,
                          limit: int = Query(default=25, ge=1, le=100),
                          db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    filters = {"run_id": str(run_id) if run_id else None, "tool_key": tool_key, "status": status}
    statement = select(ToolInvocation)
    if run_id:
        statement = statement.where(ToolInvocation.run_id == run_id)
    if tool_key:
        statement = statement.where(ToolInvocation.tool_key == tool_key)
    if status:
        statement = statement.where(ToolInvocation.status == status)
    page = page_rows(db, ToolInvocation, statement, limit=limit, cursor=cursor, filters=filters)
    return {"items": [{"id": str(row.id), "run_id": str(row.run_id), "task_run_id": str(row.task_run_id),
                       "task_attempt_id": str(row.task_attempt_id), "tool_key": row.tool_key,
                       "tool_version_id": str(row.tool_version_id), "status": row.status,
                       "error_category": row.error_category, "error_code": row.error_code,
                       "duration_ms": row.duration_ms, "input_hash": row.input_hash,
                       "output_hash": row.output_hash, "safe_metadata": row.safe_metadata,
                       "created_at": _iso(row.created_at)} for row in page["rows"]],
            "next_cursor": page["next_cursor"]}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: uuid.UUID, request: Request, db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise not_found("run")
    result = request_cancellation(db, run_id)
    add_audit_event(db, action="run.cancel_requested", actor_type="admin", actor_id=admin.admin.id,
                    target_type="analysis_run", target_id=str(run_id), request_id=request.state.request_id)
    db.commit()
    return {"run_id": str(run_id), "status": result.status}


@router.post("/tasks/{task_id}/retry")
def retry_failed_task(task_id: uuid.UUID, request: Request, db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    try:
        task = retry_task(db, task_id)
    except LookupError as exc:
        raise not_found("task") from exc
    except RuntimeTaskError as exc:
        raise V2Error(409, "task_retry_ineligible", "This task cannot be retried.") from exc
    add_audit_event(db, action="task.retry_requested", actor_type="admin", actor_id=admin.admin.id,
                    target_type="task_run", target_id=str(task_id), request_id=request.state.request_id)
    db.commit()
    return {"task_id": str(task.id), "status": task.status, "next_attempt": task.current_attempt + 1}


@router.get("/sessions")
def list_sessions(cursor: str | None = None, limit: int = Query(default=25, ge=1, le=100),
                  db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    page = page_rows(db, AdminSession, select(AdminSession).where(AdminSession.admin_id == admin.admin.id),
                     limit=limit, cursor=cursor, filters={"admin_id": str(admin.admin.id)})
    return {"items": [{"id": str(s.id), "created_at": _iso(s.created_at), "last_seen_at": _iso(s.last_seen_at),
                       "expires_at": _iso(s.expires_at), "absolute_expires_at": _iso(s.absolute_expires_at),
                       "revoked_at": _iso(s.revoked_at), "current": s.id == admin.session.id} for s in page["rows"]],
            "next_cursor": page["next_cursor"]}


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: uuid.UUID, request: Request, db: Session = Depends(get_v2_db),
                   admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    session = db.get(AdminSession, session_id)
    if session is None or session.admin_id != admin.admin.id:
        raise not_found("session")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        add_audit_event(db, action="admin.session_revoked", actor_type="admin", actor_id=admin.admin.id,
                        target_type="admin_session", target_id=str(session.id), request_id=request.state.request_id)
        db.commit()
    return {"id": str(session.id), "revoked_at": _iso(session.revoked_at)}


@router.get("/audit-events")
def list_audit_events(action: str | None = None, target_type: str | None = None,
                      cursor: str | None = None, limit: int = Query(default=25, ge=1, le=100),
                      db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    filters = {"action": action, "target_type": target_type}
    statement = select(AuditEvent)
    if action:
        statement = statement.where(AuditEvent.action == action)
    if target_type:
        statement = statement.where(AuditEvent.target_type == target_type)
    page = page_rows(db, AuditEvent, statement, limit=limit, cursor=cursor, filters=filters)
    return {"items": [{"id": str(e.id), "actor_type": e.actor_type, "actor_id": str(e.actor_id) if e.actor_id else None,
                       "action": e.action, "target_type": e.target_type, "target_id": e.target_id,
                       "before_hash": e.before_hash, "after_hash": e.after_hash,
                       "request_id": str(e.request_id), "safe_metadata": e.safe_metadata,
                       "created_at": _iso(e.created_at)} for e in page["rows"]],
            "next_cursor": page["next_cursor"]}
