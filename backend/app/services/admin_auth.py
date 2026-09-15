from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, settings
from app.db.models import AdminSession, AdminUser
from app.errors import V2Error
from app.security import (
    DUMMY_PASSWORD_HASH,
    generate_opaque_token,
    keyed_hash,
    normalize_admin_identifier,
    secure_hash_matches,
    verify_password,
)
from app.services.audit_service import add_audit_event

_RATE_SCRIPT = """
local value = redis.call('INCR', KEYS[1])
if value == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {value, redis.call('TTL', KEYS[1])}
"""


@dataclass(frozen=True)
class SessionTokens:
    session_token: str
    csrf_token: str
    session: AdminSession
    admin: AdminUser


@dataclass(frozen=True)
class AuthenticatedAdmin:
    session: AdminSession
    admin: AdminUser


class AdminAuthService:
    def __init__(self, db: Session, redis_client: Redis | None = None, config: Settings = settings) -> None:
        self.db = db
        self.redis = redis_client
        self.config = config

    def _rate_key(self, category: str, value: str) -> str:
        digest = keyed_hash(value, self.config.rate_limit_hash_secret)
        return f"reviewlens:auth:{category}:{digest}"

    def _rate_state(self, key: str) -> tuple[int, int]:
        if self.redis is None:
            raise V2Error(503, "auth_throttle_unavailable", "Sign-in is temporarily unavailable.", retryable=True)
        try:
            raw = self.redis.eval(
                "return {tonumber(redis.call('GET', KEYS[1]) or '0'), redis.call('TTL', KEYS[1])}",
                1,
                key,
            )
            return int(raw[0]), max(int(raw[1]), 0)
        except RedisError as exc:
            raise V2Error(
                503, "auth_throttle_unavailable", "Sign-in is temporarily unavailable.", retryable=True
            ) from exc

    def _record_failure(self, keys: tuple[str, str]) -> tuple[int, int]:
        if self.redis is None:
            raise V2Error(503, "auth_throttle_unavailable", "Sign-in is temporarily unavailable.", retryable=True)
        window = self.config.admin_login_window_minutes * 60
        try:
            account = self.redis.eval(_RATE_SCRIPT, 1, keys[0], window)
            ip = self.redis.eval(_RATE_SCRIPT, 1, keys[1], window)
        except RedisError as exc:
            raise V2Error(
                503, "auth_throttle_unavailable", "Sign-in is temporarily unavailable.", retryable=True
            ) from exc
        return max(int(account[0]), int(ip[0])), max(int(account[1]), int(ip[1]), 1)

    def _clear_rate_state(self, account_key: str) -> None:
        if self.redis is None:
            return
        try:
            self.redis.delete(account_key)
        except RedisError:
            # Authentication has already succeeded; a stale counter is safer than failing open.
            pass

    def login(
        self,
        *,
        identifier: str,
        password: str,
        client_ip: str,
        user_agent: str,
        request_id: uuid.UUID,
        prior_session_token: str | None = None,
    ) -> SessionTokens:
        try:
            normalized = normalize_admin_identifier(identifier)
        except ValueError:
            normalized = identifier.strip().casefold()[:320]
        account_key = self._rate_key("account", normalized)
        ip_key = self._rate_key("ip", client_ip)

        for key in (account_key, ip_key):
            count, ttl = self._rate_state(key)
            if count >= self.config.admin_login_attempts:
                raise V2Error(
                    429,
                    "admin_login_throttled",
                    "Sign-in failed. Try again later.",
                    retryable=True,
                    headers={"Retry-After": str(max(ttl, 1))},
                )

        admin = self.db.scalar(select(AdminUser).where(AdminUser.identifier == normalized))
        valid = verify_password(password, admin.password_hash if admin else DUMMY_PASSWORD_HASH)
        now = datetime.now(timezone.utc)
        if admin and admin.locked_until and admin.locked_until > now:
            valid = False
        if not admin or not admin.is_active or not valid:
            count, ttl = self._record_failure((account_key, ip_key))
            if admin:
                admin.failed_login_count += 1
                if count >= self.config.admin_login_attempts:
                    admin.locked_until = now + timedelta(seconds=ttl)
            add_audit_event(
                self.db,
                action="admin.login_failed",
                actor_type="anonymous",
                target_type="admin_user",
                target_id=keyed_hash(normalized, self.config.rate_limit_hash_secret),
                request_id=request_id,
                safe_metadata={"threshold_reached": count >= self.config.admin_login_attempts},
            )
            self.db.commit()
            raise V2Error(401, "invalid_admin_credentials", "Sign-in failed.")

        if prior_session_token:
            prior_hash = keyed_hash(prior_session_token, self.config.session_secret)
            prior = self.db.scalar(
                select(AdminSession).where(
                    AdminSession.token_hash == prior_hash,
                    AdminSession.revoked_at.is_(None),
                )
            )
            if prior:
                prior.revoked_at = now
                add_audit_event(
                    self.db,
                    action="admin.session_rotated",
                    actor_type="admin",
                    actor_id=admin.id,
                    target_type="admin_session",
                    target_id=str(prior.id),
                    request_id=request_id,
                )

        session_token = generate_opaque_token()
        csrf_token = generate_opaque_token()
        absolute_expires = now + timedelta(hours=self.config.session_absolute_hours)
        idle_expires = min(now + timedelta(minutes=self.config.session_idle_minutes), absolute_expires)
        session = AdminSession(
            token_hash=keyed_hash(session_token, self.config.session_secret),
            csrf_secret_hash=keyed_hash(csrf_token, self.config.session_secret),
            admin_id=admin.id,
            expires_at=idle_expires,
            absolute_expires_at=absolute_expires,
            last_seen_at=now,
            ip_hash=keyed_hash(client_ip, self.config.rate_limit_hash_secret),
            user_agent_hash=keyed_hash(user_agent, self.config.rate_limit_hash_secret),
        )
        self.db.add(session)
        self.db.flush()
        admin.failed_login_count = 0
        admin.locked_until = None
        admin.last_login_at = now
        add_audit_event(
            self.db,
            action="admin.login_succeeded",
            actor_type="admin",
            actor_id=admin.id,
            target_type="admin_session",
            target_id=str(session.id),
            request_id=request_id,
        )
        self.db.commit()
        self.db.refresh(session)
        self._clear_rate_state(account_key)
        return SessionTokens(session_token=session_token, csrf_token=csrf_token, session=session, admin=admin)

    def authenticate(
        self, session_token: str | None, request_id: uuid.UUID | None = None
    ) -> AuthenticatedAdmin:
        if not session_token:
            raise V2Error(401, "admin_authentication_required", "Authentication is required.")
        token_hash = keyed_hash(session_token, self.config.session_secret)
        record = self.db.scalar(
            select(AdminSession)
            .options(joinedload(AdminSession.admin))
            .where(AdminSession.token_hash == token_hash)
        )
        now = datetime.now(timezone.utc)
        if (
            record is None
            or record.revoked_at is not None
            or record.expires_at <= now
            or record.absolute_expires_at <= now
            or not record.admin.is_active
        ):
            if record and record.revoked_at is None:
                record.revoked_at = now
                add_audit_event(
                    self.db,
                    action="admin.session_revoked",
                    actor_type="system",
                    target_type="admin_session",
                    target_id=str(record.id),
                    request_id=request_id or uuid.uuid4(),
                    safe_metadata={"reason": "expired_or_inactive"},
                )
                self.db.commit()
            raise V2Error(401, "admin_authentication_required", "Authentication is required.")

        record.last_seen_at = now
        record.expires_at = min(
            now + timedelta(minutes=self.config.session_idle_minutes), record.absolute_expires_at
        )
        self.db.commit()
        return AuthenticatedAdmin(session=record, admin=record.admin)

    def rotate_csrf(self, authenticated: AuthenticatedAdmin) -> str:
        token = generate_opaque_token()
        authenticated.session.csrf_secret_hash = keyed_hash(token, self.config.session_secret)
        self.db.commit()
        return token

    def require_csrf(self, authenticated: AuthenticatedAdmin, csrf_token: str | None) -> None:
        if not csrf_token or not secure_hash_matches(
            csrf_token, authenticated.session.csrf_secret_hash, self.config.session_secret
        ):
            raise V2Error(403, "csrf_validation_failed", "The request could not be verified.")

    def logout(
        self, authenticated: AuthenticatedAdmin, csrf_token: str | None, request_id: uuid.UUID
    ) -> None:
        self.require_csrf(authenticated, csrf_token)
        authenticated.session.revoked_at = datetime.now(timezone.utc)
        add_audit_event(
            self.db,
            action="admin.logout",
            actor_type="admin",
            actor_id=authenticated.admin.id,
            target_type="admin_session",
            target_id=str(authenticated.session.id),
            request_id=request_id,
        )
        self.db.commit()


def revoke_expired_sessions(db: Session) -> int:
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(AdminSession)
        .where(
            AdminSession.revoked_at.is_(None),
            (AdminSession.expires_at <= now) | (AdminSession.absolute_expires_at <= now),
        )
        .values(revoked_at=now)
    )
    return int(result.rowcount or 0)
