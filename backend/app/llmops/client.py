from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings, settings
from app.llmops.contracts import (
    ChatInvocation,
    ChatResult,
    EmbeddingInvocation,
    EmbeddingResult,
    NormalizedUsage,
    OpenRouterError,
    OpenRouterErrorCategory,
    dollars_to_microusd,
    strictify_json_schema,
)


def _safe_error(response: httpx.Response) -> OpenRouterError:
    status = response.status_code
    provider_code: str | None = None
    message = ""
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        provider_code = str(error.get("code"))[:120] if error.get("code") is not None else None
        message = str(error.get("message", ""))[:500].lower()
    except (ValueError, TypeError):
        pass
    if status == 401:
        category = OpenRouterErrorCategory.AUTHENTICATION
    elif status == 402:
        category = OpenRouterErrorCategory.PAYMENT_REQUIRED
    elif status == 429:
        category = OpenRouterErrorCategory.RATE_LIMITED
    elif status in {408, 524}:
        category = OpenRouterErrorCategory.TIMEOUT
    elif status == 404:
        category = OpenRouterErrorCategory.MODEL_UNAVAILABLE
    elif status in {400, 422} and any(term in message for term in ("response_format", "schema", "structured")):
        category = OpenRouterErrorCategory.SCHEMA_UNSUPPORTED
    elif status in {400, 413, 422}:
        category = OpenRouterErrorCategory.INVALID_REQUEST
    elif status in {502, 503, 529}:
        category = OpenRouterErrorCategory.PROVIDER_UNAVAILABLE
    elif status >= 500:
        category = OpenRouterErrorCategory.UPSTREAM_FAILURE
    else:
        category = OpenRouterErrorCategory.UNKNOWN
    retry_after: float | None = None
    try:
        retry_after = float(response.headers["Retry-After"])
    except (KeyError, TypeError, ValueError):
        pass
    return OpenRouterError(
        category,
        status_code=status,
        provider_code=provider_code,
        correlation_id=response.headers.get("x-request-id"),
        retry_after_seconds=retry_after,
    )


def normalize_usage(payload: Any) -> NormalizedUsage | None:
    if not isinstance(payload, dict):
        return None
    prompt_details = payload.get("prompt_tokens_details") or {}
    completion_details = payload.get("completion_tokens_details") or {}
    cost_details = payload.get("cost_details") or {}
    if payload.get("total_tokens") is None and payload.get("cost") is None:
        return None
    upstream = cost_details.get("upstream_inference_cost")
    if upstream is None:
        prompt_cost = cost_details.get("upstream_inference_prompt_cost")
        completion_cost = cost_details.get("upstream_inference_completions_cost")
        if prompt_cost is not None or completion_cost is not None:
            upstream = Decimal(str(prompt_cost or 0)) + Decimal(str(completion_cost or 0))
    return NormalizedUsage(
        prompt_tokens=int(payload.get("prompt_tokens") or 0),
        completion_tokens=int(payload.get("completion_tokens") or 0),
        reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
        cached_tokens=int(prompt_details.get("cached_tokens") or 0),
        cache_write_tokens=int(prompt_details.get("cache_write_tokens") or 0),
        audio_tokens=int((payload.get("audio_tokens") or 0)),
        total_tokens=int(payload.get("total_tokens") or 0),
        total_cost_microusd=dollars_to_microusd(payload.get("cost")) or 0,
        upstream_cost_microusd=dollars_to_microusd(upstream),
    )


class OpenRouterClient:
    def __init__(
        self,
        config: Settings = settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.openrouter_base_url.rstrip("/")
        self._client = client

    def _headers(self, *, management: bool = False, request_id: str | None = None) -> dict[str, str]:
        key = self.config.openrouter_management_key if management else self.config.openrouter_api_key
        if not key:
            category = (
                OpenRouterErrorCategory.AUTHENTICATION
                if not management
                else OpenRouterErrorCategory.INVALID_REQUEST
            )
            raise OpenRouterError(category, provider_code="credential_missing")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.config.openrouter_app_url,
            "X-Title": self.config.openrouter_app_name,
        }
        if request_id:
            headers["X-Request-ID"] = request_id
        return headers

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.config.openrouter_connect_timeout_seconds,
            read=float(self.config.openrouter_request_timeout_seconds),
            write=self.config.openrouter_write_timeout_seconds,
            pool=self.config.openrouter_pool_timeout_seconds,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        management: bool = False,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout(), follow_redirects=False)
        try:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json_body,
                headers=self._headers(management=management, request_id=request_id),
            )
        except httpx.TimeoutException as exc:
            raise OpenRouterError(OpenRouterErrorCategory.TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise OpenRouterError(OpenRouterErrorCategory.UPSTREAM_FAILURE) from exc
        finally:
            if owns_client:
                await client.aclose()
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        if response.is_redirect:
            raise OpenRouterError(
                OpenRouterErrorCategory.INVALID_REQUEST,
                status_code=response.status_code,
                provider_code="redirect_rejected",
            )
        if response.status_code >= 400:
            raise _safe_error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenRouterError(
                OpenRouterErrorCategory.UNKNOWN,
                status_code=response.status_code,
                provider_code="invalid_json",
            ) from exc
        if not isinstance(payload, dict):
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_shape")
        return payload, latency_ms

    async def list_models(self, model_kind: str) -> list[dict[str, Any]]:
        if model_kind == "embedding":
            payload, _ = await self._request("GET", "/embeddings/models")
            data = payload.get("data")
            return data if isinstance(data, list) else []
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload, _ = await self._request(
                "GET",
                "/models",
                params={"offset": offset, "limit": 1000, "output_modalities": "text"},
            )
            page = payload.get("data")
            if not isinstance(page, list):
                raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_models_shape")
            rows.extend(item for item in page if isinstance(item, dict))
            if len(page) < 1000:
                return rows
            offset += len(page)

    async def list_providers(self) -> list[dict[str, Any]]:
        payload, _ = await self._request("GET", "/providers")
        data = payload.get("data")
        if not isinstance(data, list):
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_providers_shape")
        return [item for item in data if isinstance(item, dict)]

    async def list_endpoints(self, model_slug: str) -> list[dict[str, Any]]:
        try:
            author, slug = model_slug.split("/", 1)
        except ValueError as exc:
            raise ValueError("model slug must include an author") from exc
        payload, _ = await self._request(
            "GET",
            f"/models/{quote(author, safe='')}/{quote(slug, safe=':._-')}/endpoints",
        )
        data = payload.get("data")
        endpoints = data.get("endpoints") if isinstance(data, dict) else None
        if not isinstance(endpoints, list):
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_endpoints_shape")
        return [item for item in endpoints if isinstance(item, dict)]

    async def credits(self) -> dict[str, Any]:
        payload, _ = await self._request("GET", "/credits", management=True)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_credits_shape")
        return data

    async def generation(self, generation_id: str) -> dict[str, Any]:
        payload, _ = await self._request("GET", "/generation", params={"id": generation_id})
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_generation_shape")
        return data

    async def chat(self, invocation: ChatInvocation, request_id: str) -> ChatResult:
        policy = invocation.policy
        body: dict[str, Any] = {
            "models": list(policy.models),
            "messages": [item.model_dump(mode="json") for item in invocation.messages],
            "temperature": policy.temperature,
            "max_completion_tokens": policy.max_completion_tokens,
            "provider": policy.provider.to_openrouter(),
            "user": f"run-{invocation.context.run_id}",
            "metadata": {
                "trace_id": str(invocation.context.run_id),
                "span_name": invocation.context.call_key,
            },
        }
        if policy.reasoning:
            body["reasoning"] = policy.reasoning
        schema = strictify_json_schema(invocation.response_schema)
        if policy.compatibility_mode == "strict":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": invocation.schema_name, "strict": True, "schema": schema},
            }
        else:
            body["response_format"] = {"type": "json_object"}
        payload, latency_ms = await self._request(
            "POST",
            "/chat/completions",
            json_body=body,
            request_id=request_id,
        )
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            decoded = json.loads(content) if isinstance(content, str) else content
            if not isinstance(decoded, dict):
                raise TypeError
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_chat_shape") from exc
        metadata = payload.get("openrouter_metadata") or {}
        endpoints = metadata.get("endpoints") if isinstance(metadata, dict) else {}
        available = endpoints.get("available") if isinstance(endpoints, dict) else []
        selected = next((item for item in available if isinstance(item, dict) and item.get("selected")), {})
        return ChatResult(
            content=decoded,
            generation_id=str(payload.get("id")) if payload.get("id") else None,
            actual_model=str(payload.get("model")) if payload.get("model") else None,
            actual_provider=(
                str(payload.get("provider"))
                if payload.get("provider")
                else str(selected.get("provider")) if selected.get("provider") else None
            ),
            finish_reason=str(choice.get("finish_reason")) if choice.get("finish_reason") else None,
            service_tier=str(payload.get("service_tier")) if payload.get("service_tier") else None,
            usage=normalize_usage(payload.get("usage")),
            latency_ms=latency_ms,
        )

    async def embeddings(self, invocation: EmbeddingInvocation, request_id: str) -> EmbeddingResult:
        body: dict[str, Any] = {
            "model": invocation.policy.model,
            "input": list(invocation.inputs),
            "provider": invocation.policy.provider.to_openrouter(),
            "user": f"run-{invocation.context.run_id}",
        }
        if invocation.policy.dimensions is not None:
            body["dimensions"] = invocation.policy.dimensions
        if invocation.policy.input_type is not None:
            body["input_type"] = invocation.policy.input_type
        payload, latency_ms = await self._request(
            "POST",
            "/embeddings",
            json_body=body,
            request_id=request_id,
        )
        try:
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors = tuple(tuple(float(value) for value in item["embedding"]) for item in ordered)
        except (KeyError, TypeError, ValueError) as exc:
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="invalid_embedding_shape") from exc
        if len(vectors) != len(invocation.inputs):
            raise OpenRouterError(OpenRouterErrorCategory.UNKNOWN, provider_code="embedding_count_mismatch")
        return EmbeddingResult(
            vectors=vectors,
            generation_id=str(payload.get("id")) if payload.get("id") else None,
            actual_model=str(payload.get("model")) if payload.get("model") else None,
            actual_provider=str(payload.get("provider")) if payload.get("provider") else None,
            usage=normalize_usage(payload.get("usage")),
            latency_ms=latency_ms,
        )
