from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db import models as db_models  # noqa: F401
from app.db.base import Base
from app.llmops.client import OpenRouterClient, _decode_chat_content
from app.llmops.contracts import (
    ChatInvocation,
    ChatMessage,
    EmbeddingInvocation,
    EmbeddingPolicyDocument,
    InvocationContext,
    ModelPolicyDocument,
    OpenRouterError,
    OpenRouterErrorCategory,
    ProviderRouting,
    dollars_to_microusd,
    strictify_json_schema,
)
from app.llmops.live_smoke import build_live_smoke_plan, run_live_smoke


def _config(**updates) -> Settings:
    base = Settings(
        _env_file=None,
        app_env="test",
        openrouter_api_key="fixture-key",
        openrouter_management_key="fixture-management-key",
        openrouter_base_url="http://openrouter.test/api/v1",
        openrouter_app_url="http://reviewlens.test",
        openrouter_app_name="ReviewLens Test",
        session_secret="s" * 32,
        public_token_hash_secret="p" * 32,
        rate_limit_hash_secret="r" * 32,
        database_url="postgresql+psycopg://unused",
        redis_url="redis://unused",
        backend_cors_origins="http://localhost:3000",
    )
    return base.model_copy(update=updates)


def _context() -> InvocationContext:
    return InvocationContext(
        run_id=uuid.uuid4(),
        task_run_id=uuid.uuid4(),
        task_attempt_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        workflow_version_id=uuid.uuid4(),
        model_policy_version_id=uuid.uuid4(),
        embedding_policy_version_id=uuid.uuid4(),
        call_key="test.call",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        initiator_type="system_fixture",
    )


def test_provider_routing_translates_all_compatible_and_restricted_modes() -> None:
    all_routes = ProviderRouting(sort="price", data_collection="deny")
    assert "only" not in all_routes.to_openrouter()
    assert all_routes.to_openrouter()["require_parameters"] is True
    assert all_routes.to_openrouter()["data_collection"] == "deny"

    restricted = ProviderRouting(
        mode="restricted",
        only=("anthropic", "openai"),
        order=("anthropic",),
        max_price={"prompt": "0.00001"},
    )
    assert restricted.to_openrouter()["only"] == ["anthropic", "openai"]
    assert restricted.to_openrouter()["order"] == ["anthropic"]
    assert restricted.to_openrouter()["max_price"] == {"prompt": "0.00001"}


def test_restricted_routing_rejects_missing_or_display_name_providers() -> None:
    with pytest.raises(ValidationError):
        ProviderRouting(mode="restricted")
    with pytest.raises(ValidationError):
        ProviderRouting(mode="restricted", only=("Google AI Studio",))
    with pytest.raises(ValidationError):
        ProviderRouting(mode="restricted", only=("openai",), order=("anthropic",))


def test_strict_schema_is_recursive_and_money_uses_integer_microusd() -> None:
    schema = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
            }
        },
    }
    strict = strictify_json_schema(schema)
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["answer"]
    assert strict["properties"]["answer"]["additionalProperties"] is False
    assert dollars_to_microusd("0.0012345") == 1235


def test_v2_timeout_and_https_configuration_validation() -> None:
    config = _config(
        app_env="production",
        openrouter_base_url="http://unsafe.example/api/v1",
        openrouter_retry_base_seconds=10,
        openrouter_retry_max_seconds=1,
    )
    errors = config.v2_configuration_errors("api")
    assert "OPENROUTER_BASE_URL must use HTTPS outside local development and tests" in errors
    assert any("OPENROUTER_RETRY_MAX_SECONDS" in item for item in errors)


def test_live_smoke_is_opt_in_and_rejects_catalog_cost_above_cap() -> None:
    with pytest.raises(ValueError, match="LIVE_SMOKE_ENABLED"):
        asyncio.run(run_live_smoke(_config()))

    chat = [{"id": "fixture/chat", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]
    embeddings = [{"id": "fixture/embed", "pricing": {"prompt": "0.0000001"}}]
    config = _config(
        openrouter_smoke_chat_model="fixture/chat",
        openrouter_smoke_embedding_model="fixture/embed",
        openrouter_smoke_max_cost_microusd=2_000,
    )
    model_policy, embedding_policy, ceiling = build_live_smoke_plan(chat, embeddings, config)
    assert ceiling == 1_053
    assert model_policy.provider.allow_fallbacks is False
    assert model_policy.provider.max_price == {"prompt": 1, "completion": 2}
    assert embedding_policy.provider.max_price == {"prompt": Decimal("0.1")}

    with pytest.raises(ValueError, match="exceed"):
        build_live_smoke_plan(
            chat,
            embeddings,
            config.model_copy(update={"openrouter_smoke_max_cost_microusd": 100}),
        )


def test_phase3_tables_are_registered() -> None:
    assert {
        "openrouter_catalog_refreshes",
        "openrouter_model_snapshots",
        "openrouter_provider_snapshots",
        "openrouter_endpoint_snapshots",
        "openrouter_account_state",
        "daily_budget_states",
        "budget_reservations",
        "usage_events",
    } <= set(Base.metadata.tables)


def test_chat_client_sends_safe_headers_strict_schema_and_normalizes_usage() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "gen-chat-1",
                "model": "deepseek/deepseek-v4-flash-0731",
                "provider": "Fixture Provider",
                "choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\":true}"}}],
                "service_tier": "default",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost": 0.000015,
                    "prompt_tokens_details": {"cached_tokens": 2},
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
            client = OpenRouterClient(_config(), client=raw)
            invocation = ChatInvocation(
                context=_context(),
                policy=ModelPolicyDocument(
                    name="test",
                    purpose="test structured output",
                    models=("deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-flash-0731"),
                    provider=ProviderRouting(),
                    max_completion_tokens=32,
                ),
                messages=(ChatMessage(role="user", content="Return fixture JSON"),),
                response_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
                schema_name="fixture",
                estimated_prompt_tokens=10,
                estimated_cost_microusd=100,
            )
            result = await client.chat(invocation, "request-123")
            assert result.content == {"ok": True}
            assert result.actual_model == "deepseek/deepseek-v4-flash-0731"
            assert result.actual_provider == "Fixture Provider"
            assert result.usage and result.usage.total_cost_microusd == 15

    asyncio.run(run())
    assert captured["headers"]["authorization"] == "Bearer fixture-key"
    assert captured["headers"]["http-referer"] == "http://reviewlens.test"
    assert captured["headers"]["x-title"] == "ReviewLens Test"
    assert captured["headers"]["x-request-id"] == "request-123"
    assert captured["payload"]["provider"]["require_parameters"] is True
    assert captured["payload"]["response_format"]["json_schema"]["strict"] is True
    assert captured["payload"]["response_format"]["json_schema"]["schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "message",
    [
        {"content": {"ok": True}},
        {"parsed": {"ok": True}, "content": None},
        {"content": "```json\n{\"ok\": true}\n```"},
        {"content": "Here is the requested object:\n{\"ok\": true}"},
        {"content": [{"type": "text", "text": "{\"ok\":"}, {"type": "text", "text": "true}"}]},
    ],
)
def test_chat_content_decoder_accepts_supported_structured_forms(message: dict) -> None:
    assert _decode_chat_content(message) == {"ok": True}


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ({"content": None}, "chat_content_missing"),
        ({"content": ""}, "chat_content_empty"),
        ({"content": "```json\n{broken}\n```"}, "chat_content_invalid_json"),
        ({"content": "[1, 2]"}, "chat_content_not_object"),
        ({"content": [{"type": "image", "image_url": "redacted"}]}, "chat_content_parts_invalid"),
    ],
)
def test_chat_content_decoder_rejects_unsafe_or_incomplete_forms(message: dict, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        _decode_chat_content(message)


def test_chat_client_reports_truncated_structured_output() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": "{\"ok\":"}}]},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
            client = OpenRouterClient(_config(), client=raw)
            invocation = ChatInvocation(
                context=_context(),
                policy=ModelPolicyDocument(
                    name="test",
                    purpose="test truncated structured output",
                    models=("deepseek/deepseek-v4-flash",),
                    provider=ProviderRouting(),
                    max_completion_tokens=32,
                ),
                messages=(ChatMessage(role="user", content="Return fixture JSON"),),
                response_schema={"type": "object"},
                schema_name="fixture",
                estimated_prompt_tokens=10,
                estimated_cost_microusd=100,
            )
            with pytest.raises(OpenRouterError) as raised:
                await client.chat(invocation, "request-truncated")
            assert raised.value.provider_code == "chat_content_truncated"

    asyncio.run(run())


def test_embedding_client_orders_vectors_and_rejects_redirects() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            body = json.loads(request.content)
            assert body["dimensions"] == 3
            assert body["input_type"] == "search_document"
            return httpx.Response(
                200,
                json={
                    "id": "emb-1",
                    "model": "deepseek/deepseek-v4-flash",
                    "provider": "Fixture Provider",
                    "data": [
                        {"index": 1, "embedding": [0, 1, 0]},
                        {"index": 0, "embedding": [1, 0, 0]},
                    ],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4, "cost": 0.000004},
                },
            )
        return httpx.Response(307, headers={"Location": "https://unexpected.example"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
            client = OpenRouterClient(_config(), client=raw)
            invocation = EmbeddingInvocation(
                context=_context(),
                policy=EmbeddingPolicyDocument(
                    model="deepseek/deepseek-v4-flash",
                    dimensions=3,
                    input_type="search_document",
                ),
                inputs=("one", "two"),
                operation="document_embedding",
                estimated_tokens=4,
                estimated_cost_microusd=10,
            )
            result = await client.embeddings(invocation, "request-embed")
            assert result.vectors == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
            with pytest.raises(OpenRouterError) as raised:
                await client.embeddings(invocation, "request-redirect")
            assert raised.value.provider_code == "redirect_rejected"

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "body", "category", "retryable"),
    [
        (401, {"error": {"code": 401, "message": "bad key"}}, OpenRouterErrorCategory.AUTHENTICATION, False),
        (402, {"error": {"code": 402, "message": "credits"}}, OpenRouterErrorCategory.PAYMENT_REQUIRED, False),
        (429, {"error": {"code": 429, "message": "rate"}}, OpenRouterErrorCategory.RATE_LIMITED, True),
        (400, {"error": {"code": 400, "message": "response_format schema unsupported"}}, OpenRouterErrorCategory.SCHEMA_UNSUPPORTED, False),
        (503, {"error": {"code": 503, "message": "provider"}}, OpenRouterErrorCategory.PROVIDER_UNAVAILABLE, True),
    ],
)
def test_openrouter_errors_are_normalized_without_raw_bodies(status, body, category, retryable) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body))
        ) as raw:
            client = OpenRouterClient(_config(), client=raw)
            with pytest.raises(OpenRouterError) as raised:
                await client.list_models("embedding")
            assert raised.value.category == category
            assert raised.value.retryable is retryable
            assert str(raised.value) == category.value

    asyncio.run(run())
