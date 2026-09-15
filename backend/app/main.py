from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.routes import router as v1_router
from app.api.v2.router import router as v2_router
from app.config import settings
from app.errors import V2Error
from app.observability import configure_logging, request_id_context
from app.platform.health import collect_health

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    errors = settings.v2_configuration_errors("api")
    if errors:
        logger.warning("V2 platform configuration is incomplete: %s", ", ".join(errors))
    yield


app = FastAPI(
    title="ReviewLens API",
    description="Evidence-backed YouTube product review analysis",
    version="2.0.0-foundation",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Content-Type",
        "Idempotency-Key",
        "Last-Event-ID",
        "X-CSRF-Token",
        "X-Request-ID",
    ],
)


@app.middleware("http")
async def request_context_and_security_headers(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID")
    try:
        request_id = uuid.UUID(incoming) if incoming else uuid.uuid4()
    except ValueError:
        request_id = uuid.uuid4()
    request.state.request_id = request_id
    token = request_id_context.set(str(request_id))
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            if not request.url.path.startswith("/api/v2"):
                raise
            logger.error("Unhandled V2 request failure: %s", type(exc).__name__)
            wrapped = V2Error(500, "internal_error", "The service encountered an unexpected error.")
            response = JSONResponse(status_code=500, content=_v2_error_payload(request, wrapped))
    finally:
        request_id_context.reset(token)
    response.headers["X-Request-ID"] = str(request_id)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'none'; img-src 'self' data: https://fastapi.tiangolo.com; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"
    )
    return response


def _v2_error_payload(request: Request, error: V2Error) -> dict:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "request_id": str(request.state.request_id),
            "details": error.details,
        }
    }


@app.exception_handler(V2Error)
async def handle_v2_error(request: Request, error: V2Error) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=_v2_error_payload(request, error),
        headers=error.headers,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    if not request.url.path.startswith("/api/v2"):
        return await request_validation_exception_handler(request, error)
    details = []
    for item in error.errors():
        details.append(
            {
                "location": [str(part) for part in item.get("loc", ())],
                "message": item.get("msg", "Invalid value"),
                "type": item.get("type", "value_error"),
            }
        )
    wrapped = V2Error(422, "validation_error", "The request is invalid.", details=details)
    return JSONResponse(status_code=422, content=_v2_error_payload(request, wrapped))


@app.exception_handler(HTTPException)
async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
    if not request.url.path.startswith("/api/v2"):
        return await http_exception_handler(request, error)
    codes = {404: "not_found", 405: "method_not_allowed"}
    messages = {404: "The requested resource was not found.", 405: "The method is not allowed."}
    wrapped = V2Error(
        error.status_code,
        codes.get(error.status_code, "request_error"),
        messages.get(error.status_code, "The request could not be completed."),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=_v2_error_payload(request, wrapped),
        headers=error.headers,
    )


app.include_router(v1_router, prefix="/api")
app.include_router(v2_router, prefix="/api/v2")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    # Preserve the legacy V1 liveness contract.
    return {"status": "ok", "service": "reviewlens-api"}


@app.get("/health/live", tags=["system"])
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "reviewlens-api"}


@app.get("/health/ready", tags=["system"])
def readiness() -> JSONResponse:
    report = collect_health()
    return JSONResponse(status_code=200 if report.ready else 503, content=report.to_dict(detailed=False))
