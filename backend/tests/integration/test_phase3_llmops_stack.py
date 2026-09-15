from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.cache import get_redis
from app.config import settings
from app.db.models import (
    AuditEvent,
    BudgetReservation,
    OpenRouterCatalogRefresh,
    OpenRouterModelSnapshot,
    RunBudgetState,
    UsageEvent,
)
from app.db.session import session_scope
from app.llmops.accounting import BudgetRejected, reserve_request, reset_account_block
from app.llmops.catalog import (
    current_endpoints,
    refresh_catalogs,
    refresh_model_catalog,
    refresh_model_endpoints,
    search_models,
)
from app.llmops.client import OpenRouterClient
from app.llmops.contracts import (
    ChatInvocation,
    ChatMessage,
    EmbeddingInvocation,
    ModelPolicyDocument,
    OpenRouterError,
    OpenRouterErrorCategory,
    ProviderRouting,
)
from app.llmops.fixtures import create_llmops_fixture_attempt
from app.llmops.gateway import OpenRouterGateway
from app.llmops.operations import llmops_health, reconcile_pending_usage
from app.llmops.policies import PolicyCompatibilityError, validate_model_policy
from app.runtime.service import utc_now

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1",
    reason="set REVIEWLENS_RUN_INTEGRATION=1 inside the isolated Compose test stack",
)


class CatalogStub:
    def __init__(self, *, fail_chat: bool = False) -> None:
        self.fail_chat = fail_chat

    async def list_models(self, kind: str):
        if kind == "chat" and self.fail_chat:
            raise OpenRouterError(OpenRouterErrorCategory.UPSTREAM_FAILURE, status_code=500)
        if kind == "chat":
            return [
                {
                    "id": "fixture/chat-model",
                    "canonical_slug": "fixture/chat-model",
                    "name": "Fixture Chat",
                    "description": "mocked",
                    "context_length": 4096,
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "supported_parameters": ["response_format", "temperature"],
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                    "top_provider": {"max_completion_tokens": 1024},
                }
            ]
        return [
            {
                "id": "fixture/embedding-model",
                "canonical_slug": "fixture/embedding-model",
                "name": "Fixture Embedding",
                "description": "mocked",
                "context_length": 8192,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["embeddings"]},
                "supported_parameters": ["dimensions"],
                "pricing": {"prompt": "0.0000001"},
                "top_provider": {},
            }
        ]

    async def list_providers(self):
        return [{"slug": "fixture", "name": "Fixture Provider", "privacy": {"data_collection": "deny"}}]

    async def list_endpoints(self, model_slug: str):
        return [
            {
                "id": f"fixture/{model_slug}",
                "provider_slug": "fixture",
                "provider_name": "Fixture Provider",
                "context_length": 4096,
                "max_completion_tokens": 1024,
                "quantization": "fp16",
                "supported_parameters": ["response_format", "temperature"],
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "privacy": {"data_collection": "deny"},
                "status": "available",
            }
        ]


def _chat_invocation(context, policy, *, estimated_cost: int = 100) -> ChatInvocation:
    return ChatInvocation(
        context=context,
        policy=policy,
        messages=(ChatMessage(role="user", content="Return fixture JSON"),),
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        schema_name="fixture",
        estimated_prompt_tokens=10,
        estimated_cost_microusd=estimated_cost,
    )


def _success_payload(generation_id: str, *, cost: float = 0.0002, usage: bool = True) -> dict:
    payload = {
        "id": generation_id,
        "model": "fixture/chat-fallback",
        "provider": "fixture",
        "choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\":true}"}}],
        "service_tier": "default",
    }
    if usage:
        payload["usage"] = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": cost,
            "prompt_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 1},
            "completion_tokens_details": {"reasoning_tokens": 3},
        }
    return payload


def test_catalog_refresh_search_stale_fallback_and_policy_validation() -> None:
    cache = get_redis()
    cache.delete("reviewlens:openrouter:manual-refresh")
    result = asyncio.run(refresh_catalogs(client=CatalogStub(), redis_client=cache, manual=True))
    assert result["status"] == "succeeded"
    asyncio.run(refresh_model_endpoints("fixture/chat-model", client=CatalogStub(), redis_client=cache))

    with session_scope() as db:
        catalog = search_models(db, model_kind="chat", query="Fixture", capability="response_format")
        endpoints = current_endpoints(db, "fixture/chat-model")
        validation = validate_model_policy(
            db,
            ModelPolicyDocument(
                name="restricted",
                purpose="fixture validation",
                models=("fixture/chat-model",),
                provider=ProviderRouting(mode="restricted", only=("fixture",)),
                max_completion_tokens=128,
            ),
        )
    assert catalog["status"] == "ok"
    assert [item["slug"] for item in catalog["models"]] == ["fixture/chat-model"]
    assert endpoints["endpoints"][0]["provider_slug"] == "fixture"
    assert validation["eligible_routes"] == {"fixture/chat-model": ["fixture"]}
    assert json.loads(cache.get("reviewlens:openrouter:catalog:chat_models"))[0]["slug"] == "fixture/chat-model"

    with pytest.raises(OpenRouterError):
        asyncio.run(refresh_model_catalog("chat", client=CatalogStub(fail_chat=True), redis_client=cache))
    with session_scope() as db:
        stale_catalog = search_models(db, model_kind="chat")
        assert stale_catalog["models"][0]["slug"] == "fixture/chat-model"
        assert stale_catalog["status"] == "stale"
        assert stale_catalog["last_error_category"] == "openrouter_upstream_failure"
        filtered = search_models(
            db,
            model_kind="chat",
            provider="fixture",
            maximum_prompt_price=Decimal("0.000001"),
        )
        assert [item["slug"] for item in filtered["models"]] == ["fixture/chat-model"]
        assert db.scalar(
            select(func.count()).select_from(OpenRouterCatalogRefresh).where(OpenRouterCatalogRefresh.status == "failed")
        ) >= 1
    with pytest.raises(RuntimeError, match="refresh_throttled"):
        asyncio.run(refresh_catalogs(client=CatalogStub(), redis_client=cache, manual=True))


def test_policy_validation_rejects_ineligible_or_missing_routes() -> None:
    with session_scope() as db:
        with pytest.raises(PolicyCompatibilityError):
            validate_model_policy(
                db,
                ModelPolicyDocument(
                    name="bad route",
                    purpose="fixture validation",
                    models=("fixture/chat-model",),
                    provider=ProviderRouting(mode="restricted", only=("not-present",)),
                    max_completion_tokens=128,
                ),
            )
        with pytest.raises(PolicyCompatibilityError):
            validate_model_policy(
                db,
                ModelPolicyDocument(
                    name="insufficient context",
                    purpose="fixture validation",
                    models=("fixture/chat-model",),
                    provider=ProviderRouting(mode="restricted", only=("fixture",)),
                    minimum_context_tokens=4_000,
                    max_completion_tokens=128,
                ),
                acknowledge_stale=True,
            )
        with pytest.raises(PolicyCompatibilityError):
            validate_model_policy(
                db,
                ModelPolicyDocument(
                    name="price ceiling",
                    purpose="fixture validation",
                    models=("fixture/chat-model",),
                    provider=ProviderRouting(
                        mode="restricted",
                        only=("fixture",),
                        max_price={"prompt": Decimal("0.5")},
                    ),
                    max_completion_tokens=128,
                ),
                acknowledge_stale=True,
            )


def test_gateway_records_exact_usage_and_actual_fallback_route() -> None:
    reset_account_block()
    with session_scope() as db:
        context, policy, embedding_policy = create_llmops_fixture_attempt(db)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "id": f"emb-{uuid.uuid4().hex}",
                    "model": "fixture/embedding-model",
                    "provider": "fixture",
                    "data": [{"index": 0, "embedding": [1, 0, 0]}],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4, "cost": 0.000004},
                },
            )
        return httpx.Response(200, json=_success_payload(f"gen-{uuid.uuid4().hex}"))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
            gateway = OpenRouterGateway(settings, client=OpenRouterClient(settings, client=raw))
            result = await gateway.chat(_chat_invocation(context, policy))
            assert result.content == {"ok": True}
            embedding_context = context.model_copy(update={"call_key": "fixture.embedding"})
            vectors = await gateway.embeddings(
                EmbeddingInvocation(
                    context=embedding_context,
                    policy=embedding_policy.model_copy(update={"input_type": "search_query"}),
                    inputs=("fixture",),
                    operation="query_embedding",
                    estimated_tokens=4,
                    estimated_cost_microusd=10,
                )
            )
            assert vectors.vectors == ((1.0, 0.0, 0.0),)

    asyncio.run(run())
    assert len(requests) == 2
    with session_scope() as db:
        events = list(db.scalars(select(UsageEvent).where(UsageEvent.run_id == context.run_id)))
        budget = db.get(RunBudgetState, context.run_id)
    assert len(events) == 2
    chat = next(item for item in events if item.operation == "chat")
    assert chat.requested_models == ["fixture/chat-model", "fixture/chat-fallback"]
    assert chat.actual_model == "fixture/chat-fallback"
    assert chat.actual_provider == "fixture"
    assert (chat.prompt_tokens, chat.completion_tokens, chat.reasoning_tokens) == (10, 5, 3)
    assert (chat.cached_tokens, chat.cache_write_tokens, chat.total_tokens) == (2, 1, 15)
    assert chat.total_cost_microusd == 200
    assert budget and budget.reserved_tokens == 0 and budget.consumed_tokens == 19


def test_retry_creates_distinct_usage_attempts_and_releases_failed_reservation() -> None:
    reset_account_block()
    with session_scope() as db:
        context, policy, _ = create_llmops_fixture_attempt(db)
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": 429, "message": "rate"}})
        return httpx.Response(200, json=_success_payload(f"gen-{uuid.uuid4().hex}"))

    async def no_sleep(_: float) -> None:
        return None

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
            gateway = OpenRouterGateway(
                settings,
                client=OpenRouterClient(settings, client=raw),
                sleeper=no_sleep,
            )
            await gateway.chat(_chat_invocation(context, policy))

    asyncio.run(run())
    with session_scope() as db:
        events = list(
            db.scalars(select(UsageEvent).where(UsageEvent.run_id == context.run_id).order_by(UsageEvent.retry_number))
        )
        reservations = list(
            db.scalars(select(BudgetReservation).where(BudgetReservation.run_id == context.run_id).order_by(BudgetReservation.retry_number))
        )
    assert [(item.retry_number, item.status, item.error_category) for item in events] == [
        (1, "failed", "openrouter_rate_limited"),
        (2, "succeeded", None),
    ]
    assert [item.status for item in reservations] == ["released", "reconciled"]


def test_missing_usage_reconciles_by_generation_id() -> None:
    reset_account_block()
    generation_id = f"gen-pending-{uuid.uuid4().hex}"
    with session_scope() as db:
        context, policy, _ = create_llmops_fixture_attempt(db)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generation"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": generation_id,
                        "model": "fixture/chat-fallback",
                        "provider_name": "fixture",
                        "tokens_prompt": 11,
                        "tokens_completion": 7,
                        "tokens": 18,
                        "total_cost": 0.00025,
                        "latency": 12,
                    }
                },
            )
        return httpx.Response(200, json=_success_payload(generation_id, usage=False))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
            client = OpenRouterClient(settings, client=raw)
            await OpenRouterGateway(settings, client=client).chat(_chat_invocation(context, policy))
            with session_scope() as db:
                event = db.scalar(select(UsageEvent).where(UsageEvent.run_id == context.run_id))
                assert event and event.usage_status == "pending"
                event.next_reconciliation_at = utc_now()
            result = await reconcile_pending_usage(client=client)
            assert result["reconciled"] == 1

    asyncio.run(run())
    with session_scope() as db:
        event = db.scalar(select(UsageEvent).where(UsageEvent.run_id == context.run_id))
        reservation = db.get(BudgetReservation, event.reservation_id) if event else None
    assert event and event.usage_status == "reconciled" and event.total_tokens == 18
    assert event.total_cost_microusd == 250
    assert reservation and reservation.status == "reconciled"


def test_actual_overage_is_not_clamped_and_exhausts_run_budget() -> None:
    reset_account_block()
    with session_scope() as db:
        context, policy, _ = create_llmops_fixture_attempt(
            db,
            run_token_cap=500,
            run_cost_cap_microusd=100,
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=_success_payload(f"gen-{uuid.uuid4().hex}", cost=0.0002))
            )
        ) as raw:
            gateway = OpenRouterGateway(settings, client=OpenRouterClient(settings, client=raw))
            await gateway.chat(_chat_invocation(context, policy, estimated_cost=50))

    asyncio.run(run())
    with session_scope() as db:
        budget = db.get(RunBudgetState, context.run_id)
    assert budget and budget.consumed_cost_microusd == 200 and budget.status == "exhausted"


def test_overdue_pre_network_reservation_becomes_audited_unreconcilable_usage() -> None:
    reset_account_block()
    with session_scope() as db:
        context, policy, _ = create_llmops_fixture_attempt(db)
    reservation_id, _ = reserve_request(
        context,
        operation="chat",
        retry_number=1,
        estimated_tokens=20,
        estimated_cost_microusd=25,
        requested_models=list(policy.models),
    )
    with session_scope() as db:
        event = db.scalar(select(UsageEvent).where(UsageEvent.reservation_id == reservation_id))
        assert event
        event.created_at = utc_now() - timedelta(
            minutes=settings.openrouter_reconciliation_max_age_minutes + 1
        )

    result = asyncio.run(reconcile_pending_usage())
    assert result["unreconcilable"] == 1
    with session_scope() as db:
        reservation = db.get(BudgetReservation, reservation_id)
        event = db.scalar(select(UsageEvent).where(UsageEvent.reservation_id == reservation_id))
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "llmops.usage_unreconcilable",
                AuditEvent.target_id == str(event.id),
            )
        )
    assert reservation and reservation.status == "reconciled"
    assert event and event.usage_status == "unreconcilable"
    assert audit is not None


def test_budget_refusal_happens_before_network_and_401_blocks_followup_calls() -> None:
    reset_account_block()
    with session_scope() as db:
        too_small_context, too_small_policy, _ = create_llmops_fixture_attempt(db, run_token_cap=100)
    called = 0

    def should_not_call(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(200, json=_success_payload(f"gen-{uuid.uuid4().hex}"))

    async def reject_budget() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(should_not_call)) as raw:
            with pytest.raises(BudgetRejected, match="run_token_budget_exceeded"):
                await OpenRouterGateway(settings, client=OpenRouterClient(settings, client=raw)).chat(
                    _chat_invocation(too_small_context, too_small_policy)
                )

    asyncio.run(reject_budget())
    assert called == 0
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.run_id == too_small_context.run_id)) == 0

    with session_scope() as db:
        first_context, first_policy, _ = create_llmops_fixture_attempt(db)
    async def fail_auth() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": {"code": 401, "message": "bad key"}}))
        ) as raw:
            with pytest.raises(OpenRouterError):
                await OpenRouterGateway(settings, client=OpenRouterClient(settings, client=raw)).chat(
                    _chat_invocation(first_context, first_policy)
                )
    asyncio.run(fail_auth())

    with session_scope() as db:
        second_context, second_policy, _ = create_llmops_fixture_attempt(db)
    async def blocked() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(should_not_call)) as raw:
            with pytest.raises(BudgetRejected, match="authentication_blocked"):
                await OpenRouterGateway(settings, client=OpenRouterClient(settings, client=raw)).chat(
                    _chat_invocation(second_context, second_policy)
                )
    asyncio.run(blocked())
    reset_account_block()


def test_final_usage_and_catalog_snapshots_are_database_immutable() -> None:
    reset_account_block()
    with session_scope() as db:
        context, policy, _ = create_llmops_fixture_attempt(db)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=_success_payload(f"gen-{uuid.uuid4().hex}"))
            )
        ) as raw:
            await OpenRouterGateway(settings, client=OpenRouterClient(settings, client=raw)).chat(
                _chat_invocation(context, policy)
            )
    asyncio.run(run())
    with pytest.raises(DBAPIError):
        with session_scope() as db:
            event = db.scalar(select(UsageEvent).where(UsageEvent.run_id == context.run_id))
            assert event
            event.error_code = "forbidden-overwrite"
    with pytest.raises(DBAPIError):
        with session_scope() as db:
            snapshot = db.scalar(select(OpenRouterModelSnapshot).limit(1))
            assert snapshot
            snapshot.name = "forbidden-overwrite"

    health = llmops_health()
    assert health["catalog"]["status"] == "stale"
    assert health["usage_reconciliation"]["overdue"] == 0
