from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import AuditEvent

_SENSITIVE_KEY_PARTS = (
    "password", "token", "secret", "authorization", "cookie", "prompt", "response",
    "transcript", "comment", "content", "evidence", "raw", "input", "output",
)


def sanitize_audit_metadata(value: dict | None) -> dict:
    def clean(item, *, depth: int = 0):
        if depth > 3:
            return "[truncated]"
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, str):
            return item[:256]
        if isinstance(item, (list, tuple)):
            return [clean(child, depth=depth + 1) for child in item[:25]]
        if isinstance(item, dict):
            result = {}
            for raw_key, child in list(item.items())[:50]:
                key = str(raw_key)[:80]
                if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
                    continue
                result[key] = clean(child, depth=depth + 1)
            return result
        return str(type(item).__name__)[:80]

    sanitized = clean(value or {})
    return sanitized if isinstance(sanitized, dict) else {}


def add_audit_event(
    db: Session,
    *,
    action: str,
    actor_type: str,
    target_type: str,
    request_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    target_id: str | None = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
    safe_metadata: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        before_hash=before_hash,
        after_hash=after_hash,
        request_id=request_id,
        safe_metadata=sanitize_audit_metadata(safe_metadata),
    )
    db.add(event)
    return event
