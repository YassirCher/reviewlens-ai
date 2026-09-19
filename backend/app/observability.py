from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator

from app.config import settings

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
run_id_context: ContextVar[str | None] = ContextVar("run_id", default=None)
task_id_context: ContextVar[str | None] = ContextVar("task_id", default=None)
attempt_id_context: ContextVar[str | None] = ContextVar("attempt_id", default=None)

_AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+")
_COOKIE = re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)[^\r\n]+")
_SECRET_FIELD = re.compile(
    r'(?i)(["\']?(?:password|token|secret|api[_-]?key|prompt|response|transcript|comment|content)["\']?\s*[:=]\s*)'
    r'(["\']?)[^\s,;}]+\2'
)


def redact_log_message(value: str) -> str:
    redacted = _AUTHORIZATION.sub(r"\1[redacted]", value)
    redacted = _COOKIE.sub(r"\1[redacted]", redacted)
    redacted = _SECRET_FIELD.sub(r"\1[redacted]", redacted)
    for secret in (
        settings.openrouter_api_key,
        settings.openrouter_management_key,
        settings.youtube_api_key,
        settings.session_secret,
        settings.public_token_hash_secret,
        settings.rate_limit_hash_secret,
        settings.raw_content_encryption_key,
    ):
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted[:4096]


@contextmanager
def operation_context(*, run_id: object | None = None, task_id: object | None = None,
                      attempt_id: object | None = None) -> Iterator[None]:
    tokens = []
    for context, value in (
        (run_id_context, run_id), (task_id_context, task_id), (attempt_id_context, attempt_id),
    ):
        if value is not None:
            tokens.append((context, context.set(str(value))))
    try:
        yield
    finally:
        for context, token in reversed(tokens):
            context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_log_message(record.getMessage()),
        }
        request_id = request_id_context.get()
        if request_id:
            payload["request_id"] = request_id
        for name, context in (("run_id", run_id_context), ("task_id", task_id_context),
                              ("attempt_id", attempt_id_context)):
            if value := context.get():
                payload[name] = value
        if record.exc_info and record.exc_info[0]:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
