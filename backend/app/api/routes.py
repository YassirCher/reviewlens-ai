from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from redis import Redis
from sqlalchemy.orm import Session

from app.api.v2.analyses import _check_origin, _client_host, _set_anonymous_cookie
from app.api.v2.dependencies import get_v2_db, get_v2_redis
from app.compatibility.v1 import (
    begin_request,
    deprecation_headers,
    finish_request,
    legacy_progress,
    map_publication,
    record_rejection,
    run_status,
    wait_for_terminal,
)
from app.config import settings
from app.errors import V2Error
from app.models import AnalyzeRequest, AnalyzeResponse, ConfigResponse, ProviderInfo
from app.public.admission import client_ip_hash, create_analysis, resolve_session
from app.public.contracts import AnalysisRequest
from app.runtime.outbox import read_progress

router = APIRouter()
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,160}$")
_TERMINAL = {"complete", "partial", "failed", "cancelled"}


@router.get("/config", response_model=ConfigResponse, tags=["system"])
async def config() -> JSONResponse:
    payload = ConfigResponse(
        providers=[
            ProviderInfo(
                id="openrouter",
                label="Policy-managed OpenRouter routing",
                available=settings.provider_available("openrouter"),
                model="policy-managed",
                free_friendly=False,
            )
        ],
        default_provider="openrouter",
        max_videos=8,
    )
    return JSONResponse(payload.model_dump(mode="json"), headers=deprecation_headers())


def _validated_name(payload: AnalyzeRequest) -> str:
    name = " ".join(payload.product_name.split()).strip()
    if len(name) < 2:
        raise V2Error(422, "validation_error", "The request is invalid.")
    return name


def _idempotency(value: str | None, request_id: uuid.UUID) -> str:
    if value and _KEY_PATTERN.fullmatch(value):
        return value
    return f"legacy-{request_id}"


def _v2_payload(payload: AnalyzeRequest) -> AnalysisRequest:
    # The legacy provider selector is intentionally ignored. Active published
    # V2 configuration owns routing and remains snapshotted on the run.
    return AnalysisRequest(
        product_name=_validated_name(payload),
        analyze_comments=payload.analyze_comments,
    )


def _raise_with_deprecation(error: V2Error) -> None:
    error.headers.update(deprecation_headers())
    raise error


def _create_compatibility_run(
    *,
    payload: AnalyzeRequest,
    request: Request,
    db: Session,
    redis: Redis,
    transport: str,
    idempotency_key: str | None,
) -> tuple[uuid.UUID, str | None, uuid.UUID]:
    request_id = request.state.request_id
    endpoint = request.url.path
    if not settings.legacy_analysis_adapter_enabled:
        record_rejection(
            request_id=request_id,
            endpoint=endpoint,
            transport=transport,
            http_status=410,
            error_code="legacy_adapter_disabled",
        )
        _raise_with_deprecation(
            V2Error(410, "legacy_adapter_disabled", "The legacy analysis API is no longer available.")
        )
    try:
        _check_origin(request)
        session, cookie = resolve_session(
            db,
            request.cookies.get(settings.anonymous_session_cookie),
            create=True,
        )
        assert session is not None
        run = create_analysis(
            db,
            redis,
            payload=_v2_payload(payload),
            actor_type="public",
            actor_id=session.id,
            idempotency_key=_idempotency(idempotency_key, request_id),
            ip_hash=client_ip_hash(_client_host(request)),
            entrypoint=f"v1_{transport}",
        )
    except V2Error as exc:
        record_rejection(
            request_id=request_id,
            endpoint=endpoint,
            transport=transport,
            http_status=exc.status_code,
            error_code=exc.code,
        )
        _raise_with_deprecation(exc)
    begin_request(
        request_id=request_id,
        run_id=run.id,
        endpoint=endpoint,
        transport=transport,
    )
    return run.id, cookie, request_id


def _safe_error(message: str, *, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": message, "code": code},
        headers=deprecation_headers(),
    )


@router.post("/analyze", response_model=AnalyzeResponse, tags=["analysis"])
async def analyze(
    payload: AnalyzeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_v2_db),
    redis: Redis = Depends(get_v2_redis),
) -> JSONResponse:
    run_id, cookie, request_id = _create_compatibility_run(
        payload=payload,
        request=request,
        db=db,
        redis=redis,
        transport="sync",
        idempotency_key=idempotency_key,
    )
    terminal = await asyncio.to_thread(
        wait_for_terminal,
        run_id,
        settings.legacy_adapter_wait_seconds,
    )
    if terminal in {"complete", "partial"}:
        try:
            result = await asyncio.to_thread(map_publication, run_id)
        except (LookupError, ValueError):
            finish_request(
                request_id,
                status="mapping_failed",
                http_status=500,
                mapped_response=False,
                error_code="compatibility_mapping_failed",
            )
            return _safe_error(
                "The completed analysis could not be mapped to the legacy response.",
                status_code=500,
                code="compatibility_mapping_failed",
            )
        finish_request(
            request_id,
            status=terminal,
            http_status=200,
            mapped_response=True,
        )
        response = JSONResponse(
            result.model_dump(mode="json"),
            headers=deprecation_headers(),
        )
        _set_anonymous_cookie(response, cookie)
        return response
    if terminal is None:
        finish_request(
            request_id,
            status="timed_out",
            http_status=504,
            mapped_response=False,
            error_code="legacy_adapter_timeout",
        )
        response = _safe_error(
            "The analysis is still running. Use the V2 API to follow durable progress.",
            status_code=504,
            code="legacy_adapter_timeout",
        )
    else:
        error_code = "analysis_cancelled" if terminal == "cancelled" else "analysis_failed"
        finish_request(
            request_id,
            status=terminal,
            http_status=502,
            mapped_response=False,
            error_code=error_code,
        )
        response = _safe_error(
            "The analysis could not be completed.",
            status_code=502,
            code="analysis_failed",
        )
    _set_anonymous_cookie(response, cookie)
    return response


def _sse(event: str, data: object) -> str:
    encoded = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


@router.post("/analyze/stream", tags=["analysis"])
async def analyze_stream(
    payload: AnalyzeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_v2_db),
    redis: Redis = Depends(get_v2_redis),
) -> StreamingResponse:
    run_id, cookie, request_id = _create_compatibility_run(
        payload=payload,
        request=request,
        db=db,
        redis=redis,
        transport="stream",
        idempotency_key=idempotency_key,
    )

    async def event_generator() -> AsyncIterator[str]:
        cursor = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.legacy_adapter_wait_seconds
        finalized = False
        try:
            while loop.time() < deadline:
                if await request.is_disconnected():
                    finish_request(
                        request_id,
                        status="disconnected",
                        http_status=499,
                        mapped_response=False,
                        error_code="client_disconnected",
                    )
                    finalized = True
                    return
                batch = await asyncio.to_thread(
                    read_progress,
                    run_id,
                    after_sequence=cursor,
                    limit=100,
                )
                for item in batch["events"]:
                    cursor = int(item["sequence"])
                    yield _sse("progress", legacy_progress(item["data"], item["event_type"]))
                status = await asyncio.to_thread(run_status, run_id)
                if status in {"complete", "partial"}:
                    try:
                        result = await asyncio.to_thread(map_publication, run_id)
                    except (LookupError, ValueError):
                        finish_request(
                            request_id,
                            status="mapping_failed",
                            http_status=500,
                            mapped_response=False,
                            error_code="compatibility_mapping_failed",
                        )
                        finalized = True
                        yield _sse(
                            "error",
                            {"message": "The completed analysis could not be mapped."},
                        )
                        return
                    for analysis in result.videos:
                        yield _sse(
                            "video_result",
                            {"analysis": analysis.model_dump(mode="json")},
                        )
                    yield _sse("result", result.model_dump(mode="json"))
                    finish_request(
                        request_id,
                        status=status,
                        http_status=200,
                        mapped_response=True,
                    )
                    finalized = True
                    return
                if status in {"failed", "cancelled"}:
                    error_code = "analysis_cancelled" if status == "cancelled" else "analysis_failed"
                    finish_request(
                        request_id,
                        status=status,
                        http_status=502,
                        mapped_response=False,
                        error_code=error_code,
                    )
                    finalized = True
                    yield _sse("error", {"message": "The analysis could not be completed."})
                    return
                await asyncio.sleep(0.5)
            finish_request(
                request_id,
                status="timed_out",
                http_status=504,
                mapped_response=False,
                error_code="legacy_adapter_timeout",
            )
            finalized = True
            yield _sse(
                "error",
                {"message": "The analysis is still running. Use the V2 API for durable progress."},
            )
        finally:
            if not finalized:
                finish_request(
                    request_id,
                    status="disconnected",
                    http_status=499,
                    mapped_response=False,
                    error_code="client_disconnected",
                )

    response = StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            **deprecation_headers(),
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    _set_anonymous_cookie(response, cookie)
    return response
