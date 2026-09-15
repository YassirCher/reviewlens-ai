from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request, Response
from redis import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.v2.dependencies import get_v2_db, get_v2_redis, require_admin
from app.api.v2.schemas import AdminLoginRequest, AdminProfile, AdminSessionResponse, CsrfResponse
from app.config import settings
from app.errors import V2Error
from app.services.admin_auth import AdminAuthService, AuthenticatedAdmin

router = APIRouter(prefix="/admin", tags=["admin-auth"])


def _request_id(request: Request) -> uuid.UUID:
    return request.state.request_id


def _session_response(authenticated: AuthenticatedAdmin) -> AdminSessionResponse:
    return AdminSessionResponse(
        admin=AdminProfile(id=authenticated.admin.id, email=authenticated.admin.identifier),
        expires_at=authenticated.session.expires_at,
        absolute_expires_at=authenticated.session.absolute_expires_at,
    )


@router.post("/session", response_model=AdminSessionResponse)
def create_session(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_v2_db),
    redis_client: Redis = Depends(get_v2_redis),
) -> AdminSessionResponse:
    service = AdminAuthService(db, redis_client)
    try:
        tokens = service.login(
            identifier=payload.email,
            password=payload.password.get_secret_value(),
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", "")[:1024],
            request_id=_request_id(request),
            prior_session_token=request.cookies.get(settings.admin_session_cookie),
        )
    except SQLAlchemyError as exc:
        raise V2Error(503, "database_unavailable", "Sign-in is temporarily unavailable.", retryable=True) from exc

    max_age = settings.session_absolute_hours * 60 * 60
    response.set_cookie(
        key=settings.admin_session_cookie,
        value=tokens.session_token,
        max_age=max_age,
        httponly=True,
        secure=not settings.is_local_development,
        samesite="lax",
        path="/",
    )
    return _session_response(AuthenticatedAdmin(session=tokens.session, admin=tokens.admin))


@router.get("/session", response_model=AdminSessionResponse)
def read_session(authenticated: AuthenticatedAdmin = Depends(require_admin)) -> AdminSessionResponse:
    return _session_response(authenticated)


@router.get("/csrf", response_model=CsrfResponse)
def read_csrf(
    db: Session = Depends(get_v2_db),
    authenticated: AuthenticatedAdmin = Depends(require_admin),
) -> CsrfResponse:
    return CsrfResponse(csrf_token=AdminAuthService(db).rotate_csrf(authenticated))


@router.delete("/session", status_code=204)
def delete_session(
    request: Request,
    db: Session = Depends(get_v2_db),
    authenticated: AuthenticatedAdmin = Depends(require_admin),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Response:
    AdminAuthService(db).logout(authenticated, csrf_token, _request_id(request))
    response = Response(status_code=204)
    response.delete_cookie(
        key=settings.admin_session_cookie,
        httponly=True,
        secure=not settings.is_local_development,
        samesite="lax",
        path="/",
    )
    return response
