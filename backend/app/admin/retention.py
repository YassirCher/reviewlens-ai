from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.db.models import ActiveConfiguration, RetainedLLMContent, SystemSettingsVersion, UsageEvent
from app.db.session import session_scope


def validate_encryption_key(key: str) -> bool:
    try:
        Fernet(key.encode("ascii"))
        return True
    except (ValueError, UnicodeError):
        return False


def retain_chat_content(reservation_id: uuid.UUID, messages: list[dict], response: dict) -> None:
    if not settings.raw_content_encryption_key:
        return
    with session_scope() as db:
        active = db.get(ActiveConfiguration, 1)
        version = db.get(SystemSettingsVersion, active.system_settings_version_id) if active and active.system_settings_version_id else None
        if not version or not version.raw_content_retention:
            return
        event = db.scalar(select(UsageEvent).where(UsageEvent.reservation_id == reservation_id))
        if not event:
            return
        ciphertext = Fernet(settings.raw_content_encryption_key.encode("ascii")).encrypt(
            json.dumps({"messages": messages, "response": response}, separators=(",", ":")).encode("utf-8"))
        db.add(RetainedLLMContent(usage_event_id=event.id, ciphertext=ciphertext,
                                  expires_at=datetime.now(timezone.utc) + timedelta(hours=version.raw_content_ttl_hours)))
