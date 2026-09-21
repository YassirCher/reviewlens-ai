from __future__ import annotations

import logging
from collections.abc import Generator

from fastapi import Cookie, Depends, Request
from redis import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cache import RedisConfigurationError, get_redis
from app.config import settings
from app.db.session import DatabaseConfigurationError, get_session_factory
from app.errors import V2Error
from app.services.admin_auth import AdminAuthService, AuthenticatedAdmin
from app.services.user_auth import AuthenticatedUser, UserAuthService

logger = logging.getLogger(__name__)


def get_v2_db() -> Generator[Session, None, None]:
    try:
        db = get_session_factory()()
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        logger.exception("Failed to initialize database session in get_v2_db: %s", exc)
        raise V2Error(503, "database_unavailable", "The service is temporarily unavailable.", retryable=True) from exc
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_v2_redis() -> Redis:
    try:
        return get_redis()
    except RedisConfigurationError as exc:
        raise V2Error(
            503, "auth_throttle_unavailable", "Sign-in is temporarily unavailable.", retryable=True
        ) from exc


def require_admin(
    request: Request,
    db: Session = Depends(get_v2_db),
    session_token: str | None = Cookie(default=None, alias=settings.admin_session_cookie),
) -> AuthenticatedAdmin:
    try:
        return AdminAuthService(db).authenticate(session_token, request.state.request_id)
    except SQLAlchemyError as exc:
        logger.exception("require_admin failed due to database error: %s", exc)
        raise V2Error(503, "database_unavailable", "The service is temporarily unavailable.", retryable=True) from exc


def require_user(
    request: Request,
    db: Session = Depends(get_v2_db),
    session_token: str | None = Cookie(default=None, alias=settings.user_session_cookie),
) -> AuthenticatedUser:
    try:
        return UserAuthService(db).authenticate(session_token)
    except SQLAlchemyError as exc:
        logger.exception("require_user failed due to database error: %s", exc)
        raise V2Error(503, "database_unavailable", "The service is temporarily unavailable.", retryable=True) from exc


def get_optional_user(
    request: Request,
    db: Session = Depends(get_v2_db),
    session_token: str | None = Cookie(default=None, alias=settings.user_session_cookie),
) -> AuthenticatedUser | None:
    if not session_token:
        return None
    try:
        return UserAuthService(db).authenticate(session_token)
    except Exception:
        return None

