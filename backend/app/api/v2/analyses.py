from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v2.dependencies import get_v2_db, get_v2_redis, require_admin
from app.config import settings
from app.db.models import AnalysisRun, Report, ReportPublication, TaskAttempt, TaskRun
from app.db.session import session_scope
from app.errors import V2Error
from app.public.admission import client_ip_hash, create_analysis, preflight, resolve_session
from app.public.contracts import (
    AnalysisRequest,
    CreateResponse,
    GraphResponse,
    PreflightResponse,
    PublicReportResponse,
    StatusResponse,
)
from app.public.reports import report_token, token_hash, usage_summary
from app.runtime.outbox import read_progress
from app.runtime.service import request_cancellation
from app.services.admin_auth import AdminAuthService, AuthenticatedAdmin
from app.services.audit_service import add_audit_event

router = APIRouter(tags=["public-analyses"])
admin_router = APIRouter(prefix="/admin", tags=["admin-analysis-actions"])
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,160}$")
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_TERMINAL = {"complete", "partial", "failed", "cancelled"}
_NO_STORE = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow, noarchive"}


def _not_found() -> V2Error:
    return V2Error(404, "not_found", "The requested resource was not found.")


def _idempotency(value: str | None) -> str:
    if not value or not _KEY_PATTERN.fullmatch(value):
        raise V2Error(422, "validation_error", "The request is invalid.", details={"Idempotency-Key": "A 16–160 character key is required."})
    return value


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origins:
        raise V2Error(403, "origin_not_allowed", "The request could not be verified.")
    if request.headers.get("sec-fetch-site") == "cross-site" and not origin:
        raise V2Error(403, "origin_not_allowed", "The request could not be verified.")
    if request.cookies.get(settings.anonymous_session_cookie) and not origin:
        raise V2Error(403, "origin_required", "The request could not be verified.")


def _set_anonymous_cookie(response: Response, value: str | None) -> None:
    if value:
        response.set_cookie(
            key=settings.anonymous_session_cookie,
            value=value,
            max_age=settings.anonymous_session_absolute_days * 86400,
            httponly=True,
            secure=not settings.is_local_development,
            samesite="lax",
            path="/api/v2",
        )


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _authorized_run(db: Session, request: Request, run_id: uuid.UUID, *, mutation: bool = False) -> AnalysisRun:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise _not_found()
    admin_cookie = request.cookies.get(settings.admin_session_cookie)
    if admin_cookie:
        auth = AdminAuthService(db).authenticate(admin_cookie, request.state.request_id)
        if mutation:
            AdminAuthService(db).require_csrf(auth, request.headers.get("X-CSRF-Token"))
        return run
    session, _ = resolve_session(db, request.cookies.get(settings.anonymous_session_cookie))
    if session is None or run.initiator_type != "public" or run.initiator_id != session.id:
        raise _not_found()
    if mutation:
        _check_origin(request)
    return run


def _status(db: Session, run: AnalysisRun) -> StatusResponse:
    tasks = list(db.scalars(select(TaskRun).where(TaskRun.run_id == run.id).order_by(TaskRun.created_at, TaskRun.workflow_task_key)))
    publication = db.scalar(select(ReportPublication).where(ReportPublication.run_id == run.id, ReportPublication.revoked_at.is_(None)))
    report_url = None
    warnings = []
    analyzed = sum(task.workflow_task_key.startswith("analyze_review.source_") and task.status == "succeeded" for task in tasks)
    if publication and run.status in {"complete", "partial"}:
        report_url = f"/api/v2/reports/{report_token(publication.report_id)}"
        warnings = list(publication.payload.get("warnings", []))
        analyzed = publication.payload["source_count_analyzed"]
    elif isinstance(run.warning_summary, dict):
        warnings = [str(key) for key in run.warning_summary if isinstance(key, str) and len(key) <= 120]
    usage = usage_summary(db, run.id)
    public_failure = _public_failure(db, run, tasks) if run.status == "failed" else None
    return StatusResponse(
        run_id=run.id,
        status=run.status,
        product_name=run.product_input,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        source_count_requested=int(run.requested_options.get("source_count", 5)),
        source_count_analyzed=analyzed,
        completed_tasks=sum(item.status in {"succeeded", "failed", "skipped", "cancelled", "timed_out"} for item in tasks),
        total_tasks=len(tasks),
        warnings=tuple(warnings),
        failure=public_failure,
        total_tokens=usage["total_tokens"],
        usage_pending=usage["usage_pending"],
        tasks=tuple(
            {
                "task_key": task.workflow_task_key,
                "status": task.status,
                "label": task.workflow_task_key.split(".", 1)[0].replace("_", " ").title(),
                "started_at": task.started_at,
                "completed_at": task.completed_at,
            }
            for task in tasks
        ),
        report_url=report_url,
        progress_sequence=run.progress_sequence,
    )


def _public_failure(db: Session, run: AnalysisRun, tasks: list[TaskRun]) -> dict[str, str]:
    """Allowlist safe explanations; never serialize task inputs or upstream errors."""
    attempts = list(db.scalars(
        select(TaskAttempt).join(TaskRun, TaskRun.id == TaskAttempt.task_run_id)
        .where(TaskRun.run_id == run.id)
    ))
    codes = {attempt.error_code for attempt in attempts if attempt.status == "failed"}
    if "youtube_no_candidates" in codes:
        return {"code": "no_relevant_videos", "message": "No relevant review videos were found. Try a more specific product model."}
    transcript_tasks = [task for task in tasks if task.workflow_task_key.startswith("fetch_transcript.source_")]
    if transcript_tasks:
        by_task = {task.id: task for task in transcript_tasks}
        transcript_outputs = [
            attempt.output_payload for attempt in attempts
            if attempt.task_run_id in by_task and attempt.status == "succeeded" and isinstance(attempt.output_payload, dict)
        ]
        if transcript_outputs and len(transcript_outputs) == len(transcript_tasks) and all(not output.get("available") for output in transcript_outputs):
            return {"code": "no_transcripts", "message": "Review videos were found, but usable captions were unavailable. Try another product or model."}
    if any(attempt.error_category == "budget" for attempt in attempts if attempt.status == "failed"):
        return {"code": "analysis_capacity_reached", "message": "Research capacity was reached before a report could be completed. Try again later."}
    return {"code": "analysis_failed", "message": "The analysis could not be completed. Try again later."}


@router.post("/analyses/preflight", response_model=PreflightResponse)
def analysis_preflight(
    payload: AnalysisRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_v2_db),
    redis: Redis = Depends(get_v2_redis),
) -> PreflightResponse:
    _check_origin(request)
    session, cookie = resolve_session(db, request.cookies.get(settings.anonymous_session_cookie), create=True)
    assert session is not None
    data = preflight(db, redis, payload, session.id, client_ip_hash(_client_host(request)))
    db.commit()
    _set_anonymous_cookie(response, cookie)
    response.headers.update(_NO_STORE)
    return PreflightResponse.model_validate(data)


@router.post("/analyses", response_model=CreateResponse, status_code=202)
def create_public_analysis(
    payload: AnalysisRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_v2_db),
    redis: Redis = Depends(get_v2_redis),
) -> CreateResponse:
    _check_origin(request)
    key = _idempotency(idempotency_key)
    session, cookie = resolve_session(db, request.cookies.get(settings.anonymous_session_cookie), create=True)
    assert session is not None
    run = create_analysis(db, redis, payload=payload, actor_type="public", actor_id=session.id, idempotency_key=key, ip_hash=client_ip_hash(_client_host(request)))
    _set_anonymous_cookie(response, cookie)
    response.headers.update(_NO_STORE)
    return CreateResponse(run_id=run.id, status=run.status, status_url=f"/api/v2/analyses/{run.id}", events_url=f"/api/v2/analyses/{run.id}/events")


@admin_router.post("/analyses", response_model=CreateResponse, status_code=202)
def create_admin_analysis(
    payload: AnalysisRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Session = Depends(get_v2_db),
    authenticated: AuthenticatedAdmin = Depends(require_admin),
) -> CreateResponse:
    AdminAuthService(db).require_csrf(authenticated, csrf_token)
    run = create_analysis(db, None, payload=payload, actor_type="admin", actor_id=authenticated.admin.id, idempotency_key=_idempotency(idempotency_key), ip_hash=None)
    return CreateResponse(run_id=run.id, status=run.status, status_url=f"/api/v2/analyses/{run.id}", events_url=f"/api/v2/analyses/{run.id}/events")


@router.get("/analyses/{run_id}", response_model=StatusResponse)
def analysis_status(run_id: uuid.UUID, request: Request, response: Response, db: Session = Depends(get_v2_db)) -> StatusResponse:
    run = _authorized_run(db, request, run_id)
    result = _status(db, run)
    db.commit()
    response.headers.update(_NO_STORE)
    return result


@router.post("/analyses/{run_id}/cancel")
def cancel_analysis(run_id: uuid.UUID, request: Request, response: Response, db: Session = Depends(get_v2_db)) -> dict:
    run = _authorized_run(db, request, run_id, mutation=True)
    was_terminal = run.status in _TERMINAL
    result = request_cancellation(db, run.id)
    db.commit()
    response.status_code = 200 if was_terminal else 202
    response.headers.update(_NO_STORE)
    return {"run_id": str(run_id), "status": result.status}


def _stream_access(run_id: uuid.UUID, cookies: dict[str, str], request_id: uuid.UUID) -> tuple[str, int]:
    with session_scope() as db:
        run = db.get(AnalysisRun, run_id)
        if run is None:
            raise _not_found()
        if cookies.get(settings.admin_session_cookie):
            AdminAuthService(db).authenticate(cookies[settings.admin_session_cookie], request_id)
        else:
            session, _ = resolve_session(db, cookies.get(settings.anonymous_session_cookie))
            if session is None or run.initiator_type != "public" or run.initiator_id != session.id:
                raise _not_found()
        return run.status, run.progress_sequence


@router.get("/analyses/{run_id}/events")
async def analysis_events(
    run_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_v2_db),
) -> StreamingResponse:
    run = _authorized_run(db, request, run_id)
    if last_event_id is None:
        cursor = 0
    elif not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id) > 18:
        raise V2Error(422, "invalid_event_cursor", "The event cursor is invalid.")
    else:
        cursor = int(last_event_id)
    if cursor > run.progress_sequence:
        raise V2Error(422, "invalid_event_cursor", "The event cursor is invalid.")
    db.commit()
    cookies = dict(request.cookies)
    request_id = request.state.request_id

    async def stream() -> AsyncIterator[str]:
        current = cursor
        last_heartbeat = asyncio.get_running_loop().time()
        while True:
            if await request.is_disconnected():
                return
            try:
                status, sequence = await asyncio.to_thread(_stream_access, run_id, cookies, request_id)
            except V2Error:
                return
            batch = await asyncio.to_thread(read_progress, run_id, after_sequence=current, limit=100)
            for item in batch["events"]:
                current = item["sequence"]
                body = json.dumps(item["data"], separators=(",", ":"), ensure_ascii=True)
                yield f"id: {current}\nevent: {item['event_type']}\ndata: {body}\n\n"
            if status in _TERMINAL and current >= sequence:
                return
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={**_NO_STORE, "X-Accel-Buffering": "no"})


def _publication(db: Session, token: str) -> ReportPublication:
    if not _TOKEN_PATTERN.fullmatch(token):
        raise _not_found()
    digest = token_hash(token)
    publication = db.scalar(select(ReportPublication).where(ReportPublication.token_hash == digest, ReportPublication.revoked_at.is_(None)))
    if publication is None or not hmac.compare_digest(publication.token_hash, digest):
        raise _not_found()
    report = db.get(Report, publication.report_id)
    run = db.get(AnalysisRun, publication.run_id)
    if report is None or report.status != "published" or run is None or run.status not in {"complete", "partial"}:
        raise _not_found()
    return publication


@router.get("/reports/{public_token}", response_model=PublicReportResponse)
def read_report(public_token: str, response: Response, db: Session = Depends(get_v2_db)) -> PublicReportResponse:
    publication = _publication(db, public_token)
    result = PublicReportResponse.model_validate({**publication.payload, **usage_summary(db, publication.run_id)})
    response.headers.update(_NO_STORE)
    return result


def _cursor(report_id: uuid.UUID, offset: int, type_filter: str | None) -> str:
    body = f"{report_id}:{offset}:{type_filter or '*'}".encode()
    signature = hmac.new(settings.public_token_hash_secret.encode(), b"graph-cursor-v1:" + body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body).rstrip(b"=").decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def _parse_cursor(value: str | None, report_id: uuid.UUID, type_filter: str | None) -> int:
    if not value:
        return 0
    try:
        encoded_body, encoded_signature = value.split(".", 1)
        body = base64.urlsafe_b64decode(encoded_body + "=" * (-len(encoded_body) % 4))
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        expected = hmac.new(settings.public_token_hash_secret.encode(), b"graph-cursor-v1:" + body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        rid, offset, kind = body.decode().split(":")
        if rid != str(report_id) or kind != (type_filter or "*"):
            raise ValueError
        result = int(offset)
        if result < 0:
            raise ValueError
        return result
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise V2Error(422, "invalid_graph_cursor", "The graph cursor is invalid.") from exc


@router.get("/reports/{public_token}/graph", response_model=GraphResponse)
def read_report_graph(
    public_token: str,
    response: Response,
    type_filter: str | None = Query(default=None, alias="type", pattern="^(product|source|finding|evidence)$"),
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_v2_db),
) -> GraphResponse:
    publication = _publication(db, public_token)
    graph = publication.graph_payload
    nodes = [node for node in graph["nodes"] if type_filter is None or node["type"] == type_filter]
    offset = _parse_cursor(cursor, publication.report_id, type_filter)
    page = nodes[offset:offset + limit]
    visible = {node["id"] for node in page}
    # Include connections crossing page boundaries. Clients may fetch later node
    # pages and join by public opaque IDs without losing those relationships.
    edges = [edge for edge in graph["edges"] if edge["source"] in visible or edge["target"] in visible][:200]
    next_cursor = _cursor(publication.report_id, offset + limit, type_filter) if offset + limit < len(nodes) else None
    response.headers.update(_NO_STORE)
    return GraphResponse(nodes=tuple(page), edges=tuple(edges), next_cursor=next_cursor)


@admin_router.post("/reports/{report_id}/revoke", status_code=204)
def revoke_report(
    report_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_v2_db),
    authenticated: AuthenticatedAdmin = Depends(require_admin),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Response:
    AdminAuthService(db).require_csrf(authenticated, csrf_token)
    publication = db.scalar(select(ReportPublication).where(ReportPublication.report_id == report_id).with_for_update())
    if publication is None:
        raise _not_found()
    if publication.revoked_at is None:
        now = datetime.now(timezone.utc)
        publication.revoked_at = now
        report = db.get(Report, report_id)
        if report:
            report.status = "revoked"
            report.revoked_at = now
        add_audit_event(db, action="report.public_access_revoked", actor_type="admin", actor_id=authenticated.admin.id, target_type="report", target_id=str(report_id), request_id=request.state.request_id)
    db.commit()
    return Response(status_code=204, headers=_NO_STORE)
