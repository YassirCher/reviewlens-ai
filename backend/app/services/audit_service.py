from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


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
        safe_metadata=safe_metadata or {},
    )
    db.add(event)
    return event
