from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import Settings, settings

ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]


class V2RequestGuardMiddleware:
    """Reject oversized or incorrectly typed V2 request bodies before routing."""

    def __init__(self, app: ASGIApp, config: Settings = settings) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or not (path == "/api/v2" or path.startswith("/api/v2/")):
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        raw_length = headers.get("content-length")
        try:
            content_length = int(raw_length) if raw_length is not None else None
        except ValueError:
            await self._reject(scope, send, 400, "invalid_content_length", "The request is invalid.")
            return
        if content_length is not None and content_length < 0:
            await self._reject(scope, send, 400, "invalid_content_length", "The request is invalid.")
            return
        if content_length is not None and content_length > self.config.v2_max_request_body_bytes:
            await self._reject(scope, send, 413, "request_body_too_large", "The request body is too large.")
            return
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.config.v2_max_request_body_bytes:
                await self._reject(scope, send, 413, "request_body_too_large", "The request body is too large.")
                return
            chunks.append(body)
            more = bool(message.get("more_body", False))
        buffered = b"".join(chunks)
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if buffered and content_type != "application/json":
            await self._reject(scope, send, 415, "unsupported_media_type", "Use application/json for this request.")
            return
        delivered = False

        async def replay() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": buffered, "more_body": False}

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(scope: dict[str, Any], send, status: int, code: str, message: str) -> None:
        request_id = str(scope.get("state", {}).get("request_id") or uuid.uuid4())
        body = json.dumps({"error": {"code": code, "message": message, "retryable": False,
                                     "request_id": request_id, "details": None}},
                          separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii"))]})
        await send({"type": "http.response.body", "body": body})
