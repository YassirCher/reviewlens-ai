from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from fastapi import APIRouter, Depends, Request, Response
from redis import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.v2.dependencies import get_v2_db, get_v2_redis, require_user
from app.config import settings
from app.errors import V2Error
from app.services.user_auth import AuthenticatedUser, UserAuthService

router = APIRouter(prefix="/user", tags=["user-auth"])


class UserRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=120)


class UserLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=8, max_length=128)


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    email: str
    name: str | None = None
    created_at: datetime


class UserAuthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: UserProfileResponse


def _set_user_cookie(response: Response, token: str) -> None:
    max_age = settings.user_session_absolute_days * 24 * 60 * 60
    response.set_cookie(
        key=settings.user_session_cookie,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=not settings.is_local_development,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=UserAuthResponse, status_code=201)
def register_user(
    payload: UserRegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_v2_db),
    redis_client: Redis = Depends(get_v2_redis),
) -> UserAuthResponse:
    service = UserAuthService(db, redis_client)
    try:
        tokens = service.register(
            email=payload.email,
            password=payload.password.get_secret_value(),
            name=payload.name,
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", ""),
            prior_anon_cookie=request.cookies.get(settings.anonymous_session_cookie),
        )
    except SQLAlchemyError as exc:
        raise V2Error(503, "database_unavailable", "Registration is temporarily unavailable.", retryable=True) from exc

    _set_user_cookie(response, tokens.session_token)
    return UserAuthResponse(
        user=UserProfileResponse(
            id=tokens.user.id,
            email=tokens.user.email,
            name=tokens.user.name,
            created_at=tokens.user.created_at,
        )
    )


@router.post("/login", response_model=UserAuthResponse)
def login_user(
    payload: UserLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_v2_db),
    redis_client: Redis = Depends(get_v2_redis),
) -> UserAuthResponse:
    service = UserAuthService(db, redis_client)
    try:
        tokens = service.login(
            email=payload.email,
            password=payload.password.get_secret_value(),
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", ""),
            prior_anon_cookie=request.cookies.get(settings.anonymous_session_cookie),
        )
    except SQLAlchemyError as exc:
        raise V2Error(503, "database_unavailable", "Sign-in is temporarily unavailable.", retryable=True) from exc

    _set_user_cookie(response, tokens.session_token)
    return UserAuthResponse(
        user=UserProfileResponse(
            id=tokens.user.id,
            email=tokens.user.email,
            name=tokens.user.name,
            created_at=tokens.user.created_at,
        )
    )


@router.post("/logout")
def logout_user(
    response: Response,
    db: Session = Depends(get_v2_db),
    authenticated: AuthenticatedUser = Depends(require_user),
) -> dict[str, str]:
    service = UserAuthService(db)
    service.logout(authenticated)
    response.delete_cookie(
        key=settings.user_session_cookie,
        path="/",
        samesite="lax",
    )
    return {"status": "logged_out"}


@router.get("/me", response_model=UserAuthResponse)
def get_current_user_profile(
    authenticated: AuthenticatedUser = Depends(require_user),
) -> UserAuthResponse:
    return UserAuthResponse(
        user=UserProfileResponse(
            id=authenticated.user.id,
            email=authenticated.user.email,
            name=authenticated.user.name,
            created_at=authenticated.user.created_at,
        )
    )
