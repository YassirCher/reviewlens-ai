from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from redis import Redis
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, settings
from app.db.models import AnonymousSession, AnalysisRun, User, UserSession
from app.errors import V2Error
from app.security import (
    generate_opaque_token,
    hash_password,
    keyed_hash,
    normalize_user_email,
    verify_password,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class UserSessionTokens:
    session_token: str
    session: UserSession
    user: User


@dataclass(frozen=True)
class AuthenticatedUser:
    session: UserSession
    user: User


class UserAuthService:
    def __init__(self, db: Session, redis_client: Redis | None = None, config: Settings = settings) -> None:
        self.db = db
        self.redis = redis_client
        self.config = config

    def _hash_ip(self, ip: str) -> str:
        return keyed_hash(ip, self.config.rate_limit_hash_secret)

    def _hash_ua(self, user_agent: str) -> str:
        return keyed_hash(user_agent[:1024], self.config.session_secret)

    def register(
        self,
        *,
        email: str,
        password: str,
        name: str | None = None,
        client_ip: str = "unknown",
        user_agent: str = "",
        prior_anon_cookie: str | None = None,
    ) -> UserSessionTokens:
        try:
            normalized = normalize_user_email(email)
        except ValueError as exc:
            raise V2Error(422, "invalid_email", str(exc)) from exc

        if len(password) < 8:
            raise V2Error(422, "weak_password", "Password must contain at least 8 characters.")

        existing = self.db.scalar(select(User).where(User.email == normalized))
        if existing is not None:
            raise V2Error(409, "email_already_registered", "An account with this email address already exists.")

        now = _utc_now()
        user = User(
            id=uuid.uuid4(),
            email=normalized,
            password_hash=hash_password(password),
            name=name.strip() if name and name.strip() else None,
            is_active=True,
            last_login_at=now,
        )
        self.db.add(user)
        self.db.flush()

        # Create session
        token = generate_opaque_token()
        token_hash = keyed_hash(token, self.config.session_secret)
        idle = timedelta(days=self.config.user_session_idle_days)
        absolute = timedelta(days=self.config.user_session_absolute_days)

        session = UserSession(
            id=uuid.uuid4(),
            token_hash=token_hash,
            user_id=user.id,
            expires_at=now + idle,
            absolute_expires_at=now + absolute,
            last_seen_at=now,
            ip_hash=self._hash_ip(client_ip),
            user_agent_hash=self._hash_ua(user_agent),
        )
        self.db.add(session)

        # Adopt any researches started anonymously
        if prior_anon_cookie:
            self.adopt_anonymous_runs(user.id, prior_anon_cookie)

        self.db.commit()
        self.db.refresh(user)
        self.db.refresh(session)
        return UserSessionTokens(session_token=token, session=session, user=user)

    def login(
        self,
        *,
        email: str,
        password: str,
        client_ip: str = "unknown",
        user_agent: str = "",
        prior_anon_cookie: str | None = None,
    ) -> UserSessionTokens:
        try:
            normalized = normalize_user_email(email)
        except ValueError as exc:
            raise V2Error(401, "invalid_credentials", "Invalid email or password.") from exc

        user = self.db.scalar(select(User).where(User.email == normalized))
        if user is None or not verify_password(password, user.password_hash):
            raise V2Error(401, "invalid_credentials", "Invalid email or password.")

        if not user.is_active:
            raise V2Error(403, "account_inactive", "This account has been deactivated.")

        now = _utc_now()
        user.last_login_at = now

        token = generate_opaque_token()
        token_hash = keyed_hash(token, self.config.session_secret)
        idle = timedelta(days=self.config.user_session_idle_days)
        absolute = timedelta(days=self.config.user_session_absolute_days)

        session = UserSession(
            id=uuid.uuid4(),
            token_hash=token_hash,
            user_id=user.id,
            expires_at=now + idle,
            absolute_expires_at=now + absolute,
            last_seen_at=now,
            ip_hash=self._hash_ip(client_ip),
            user_agent_hash=self._hash_ua(user_agent),
        )
        self.db.add(session)

        if prior_anon_cookie:
            self.adopt_anonymous_runs(user.id, prior_anon_cookie)

        self.db.commit()
        self.db.refresh(user)
        self.db.refresh(session)
        return UserSessionTokens(session_token=token, session=session, user=user)

    def authenticate(self, session_token: str | None) -> AuthenticatedUser:
        if not session_token:
            raise V2Error(401, "user_authentication_required", "Please sign in to continue.")

        token_hash = keyed_hash(session_token, self.config.session_secret)
        session = self.db.scalar(
            select(UserSession)
            .options(joinedload(UserSession.user))
            .where(UserSession.token_hash == token_hash)
        )

        now = _utc_now()
        expires_at = _ensure_utc(session.expires_at) if session else None
        absolute_expires_at = _ensure_utc(session.absolute_expires_at) if session else None
        if (
            session is None
            or session.revoked_at is not None
            or expires_at is None
            or expires_at <= now
            or absolute_expires_at is None
            or absolute_expires_at <= now
            or session.user is None
            or not session.user.is_active
        ):
            raise V2Error(401, "user_session_expired", "Your session has expired. Please sign in again.")

        session.last_seen_at = now
        session.expires_at = min(
            now + timedelta(days=self.config.user_session_idle_days),
            absolute_expires_at,
        )
        self.db.commit()
        return AuthenticatedUser(session=session, user=session.user)

    def logout(self, authenticated: AuthenticatedUser) -> None:
        authenticated.session.revoked_at = _utc_now()
        self.db.commit()

    def adopt_anonymous_runs(self, user_id: uuid.UUID, anon_cookie: str | None) -> int:
        if not anon_cookie:
            return 0
        from app.public.admission import _parse_cookie

        anon_id = _parse_cookie(anon_cookie, self.config)
        if not anon_id:
            return 0

        digest = keyed_hash("anon:" + anon_id, self.config.session_secret)
        anon_session = self.db.scalar(select(AnonymousSession).where(AnonymousSession.identifier_hash == digest))
        if anon_session is None:
            return 0

        result = self.db.execute(
            update(AnalysisRun)
            .where(
                AnalysisRun.initiator_type == "public",
                AnalysisRun.initiator_id == anon_session.id,
                AnalysisRun.user_id.is_(None),
            )
            .values(user_id=user_id)
        )
        return int(result.rowcount or 0)
