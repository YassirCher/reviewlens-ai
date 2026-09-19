from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.api.v2.dependencies import get_v2_db, require_admin
from app.config import settings
from app.errors import V2Error
from app.services.admin_auth import AdminAuthService, AuthenticatedAdmin


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageQuery(StrictModel):
    cursor: str | None = None
    limit: int = Field(default=25, ge=1, le=100)


def require_admin_mutation(
    authenticated: AuthenticatedAdmin = Depends(require_admin),
    db: Session = Depends(get_v2_db),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthenticatedAdmin:
    AdminAuthService(db).require_csrf(authenticated, csrf_token)
    return authenticated


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _filter_hash(filters: dict[str, Any]) -> str:
    body = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(body).hexdigest()


def encode_list_cursor(position: str, filters: dict[str, Any]) -> str:
    body = json.dumps({"position": position, "filter": _filter_hash(filters)}, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(settings.session_secret.encode(), b"admin-list-v1:" + body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(signature)}"


def decode_list_cursor(value: str | None, filters: dict[str, Any]) -> str | None:
    if value is None:
        return None
    try:
        encoded, mac = value.split(".", 1)
        if len(value) > 1000:
            raise ValueError("cursor too long")
        body = _unb64(encoded)
        expected = hmac.new(settings.session_secret.encode(), b"admin-list-v1:" + body, hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(mac), expected):
            raise ValueError("cursor signature invalid")
        data = json.loads(body)
        if data["filter"] != _filter_hash(filters) or not isinstance(data["position"], str):
            raise ValueError("cursor filters changed")
        return data["position"]
    except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise V2Error(422, "invalid_cursor", "The list cursor is invalid.") from exc


def encode_cursor(created_at: datetime, identifier: uuid.UUID, filters: dict[str, Any]) -> str:
    body = json.dumps(
        {"at": created_at.isoformat(), "id": str(identifier), "filter": _filter_hash(filters)},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    signature = hmac.new(settings.session_secret.encode(), b"admin-cursor-v1:" + body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(signature)}"


def decode_cursor(value: str | None, filters: dict[str, Any]) -> tuple[datetime, uuid.UUID] | None:
    if value is None:
        return None
    try:
        encoded, mac = value.split(".", 1)
        if len(value) > 1000:
            raise ValueError("cursor too long")
        body = _unb64(encoded)
        signature = _unb64(mac)
        expected = hmac.new(settings.session_secret.encode(), b"admin-cursor-v1:" + body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("cursor signature invalid")
        data = json.loads(body)
        if data["filter"] != _filter_hash(filters):
            raise ValueError("cursor filters changed")
        return datetime.fromisoformat(data["at"]), uuid.UUID(data["id"])
    except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise V2Error(422, "invalid_cursor", "The list cursor is invalid.") from exc


def page_rows(
    db: Session,
    model: type,
    statement: Select,
    *,
    limit: int,
    cursor: str | None,
    filters: dict[str, Any],
) -> dict[str, Any]:
    position = decode_cursor(cursor, filters)
    if position:
        created_at, identifier = position
        statement = statement.where(
            or_(model.created_at < created_at, and_(model.created_at == created_at, model.id < identifier))
        )
    rows = list(db.scalars(statement.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "rows": rows,
        "next_cursor": encode_cursor(rows[-1].created_at, rows[-1].id, filters) if rows and has_more else None,
    }


def not_found(label: str = "resource") -> V2Error:
    return V2Error(404, "not_found", f"The requested {label} was not found.")
