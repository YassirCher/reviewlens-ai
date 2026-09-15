from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Any
from urllib.parse import urlparse

from app.config import Settings, settings
from app.llmops.client import OpenRouterClient
from app.llmops.contracts import (
    ChatInvocation,
    ChatMessage,
    EmbeddingInvocation,
    EmbeddingPolicyDocument,
    InvocationContext,
    ModelPolicyDocument,
    ProviderRouting,
)

CHAT_PROMPT_TOKEN_CEILING = 1_024
CHAT_COMPLETION_TOKEN_CEILING = 8
EMBEDDING_TOKEN_CEILING = 128


def _decimal_price(pricing: dict[str, Any], key: str, *, required: bool) -> Decimal:
    raw = pricing.get(key)
    if raw is None:
        if required:
            raise ValueError(f"selected smoke model has no {key} price")
        return Decimal(0)
    try:
        value = Decimal(str(raw))
    except Exception as exc:
        raise ValueError(f"selected smoke model has an invalid {key} price") from exc
    if not value.is_finite() or value < 0:
        raise ValueError(f"selected smoke model has an invalid {key} price")
    return value


def _model_by_slug(rows: list[dict[str, Any]], slug: str, kind: str) -> dict[str, Any]:
    row = next((item for item in rows if item.get("id") == slug), None)
    if row is None:
        raise ValueError(f"configured {kind} smoke model is not in the current catalog")
    return row


def build_live_smoke_plan(
    chat_models: list[dict[str, Any]],
    embedding_models: list[dict[str, Any]],
    config: Settings = settings,
) -> tuple[ModelPolicyDocument, EmbeddingPolicyDocument, int]:
    chat = _model_by_slug(chat_models, config.openrouter_smoke_chat_model, "chat")
    embedding = _model_by_slug(
        embedding_models,
        config.openrouter_smoke_embedding_model,
        "embedding",
    )
    chat_pricing = chat.get("pricing")
    embedding_pricing = embedding.get("pricing")
    if not isinstance(chat_pricing, dict) or not isinstance(embedding_pricing, dict):
        raise ValueError("selected smoke models must publish pricing")

    chat_prompt = _decimal_price(chat_pricing, "prompt", required=True)
    chat_completion = _decimal_price(chat_pricing, "completion", required=True)
    chat_request = _decimal_price(chat_pricing, "request", required=False)
    embedding_prompt = _decimal_price(embedding_pricing, "prompt", required=True)
    embedding_request = _decimal_price(embedding_pricing, "request", required=False)
    maximum_dollars = (
        chat_request
        + chat_prompt * CHAT_PROMPT_TOKEN_CEILING
        + chat_completion * CHAT_COMPLETION_TOKEN_CEILING
        + embedding_request
        + embedding_prompt * EMBEDDING_TOKEN_CEILING
    )
    maximum_microusd = int(
        (maximum_dollars * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_CEILING)
    )
    if maximum_microusd > config.openrouter_smoke_max_cost_microusd:
        raise ValueError(
            "selected smoke models exceed OPENROUTER_SMOKE_MAX_COST_MICROUSD under the "
            "conservative token ceiling"
        )

    chat_routing = ProviderRouting(
        allow_fallbacks=False,
        require_parameters=True,
        data_collection="deny",
        max_price={
            "prompt": chat_prompt * Decimal(1_000_000),
            "completion": chat_completion * Decimal(1_000_000),
        },
    )
    embedding_routing = ProviderRouting(
        allow_fallbacks=False,
        require_parameters=True,
        data_collection="deny",
        max_price={"prompt": embedding_prompt * Decimal(1_000_000)},
    )
    return (
        ModelPolicyDocument(
            name="explicit live smoke",
            purpose="Bounded operator verification",
            models=(config.openrouter_smoke_chat_model,),
            provider=chat_routing,
            temperature=0,
            max_completion_tokens=CHAT_COMPLETION_TOKEN_CEILING,
            compatibility_mode="strict",
        ),
        EmbeddingPolicyDocument(
            model=config.openrouter_smoke_embedding_model,
            provider=embedding_routing,
            input_type="search_query",
        ),
        maximum_microusd,
    )


def _smoke_context(call_key: str, config: Settings) -> InvocationContext:
    return InvocationContext(
        run_id=uuid.uuid4(),
        task_run_id=uuid.uuid4(),
        task_attempt_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        workflow_version_id=uuid.uuid4(),
        model_policy_version_id=uuid.uuid4(),
        embedding_policy_version_id=uuid.uuid4(),
        call_key=call_key,
        deadline_at=datetime.now(UTC) + timedelta(seconds=config.openrouter_request_timeout_seconds),
        initiator_type="operator_live_smoke",
    )


async def run_live_smoke(config: Settings = settings) -> dict[str, Any]:
    if not config.openrouter_live_smoke_enabled:
        raise ValueError("OPENROUTER_LIVE_SMOKE_ENABLED must be true")
    if not config.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY is required")
    if not config.openrouter_smoke_chat_model or not config.openrouter_smoke_embedding_model:
        raise ValueError("both OpenRouter smoke model slugs must be configured")
    parsed = urlparse(config.openrouter_base_url)
    if parsed.scheme != "https" or parsed.hostname != "openrouter.ai":
        raise ValueError("live smoke requires the official HTTPS OpenRouter origin")

    client = OpenRouterClient(config)
    chat_models = await client.list_models("chat")
    embedding_models = await client.list_models("embedding")
    chat_policy, embedding_policy, maximum_microusd = build_live_smoke_plan(
        chat_models,
        embedding_models,
        config,
    )
    chat = await client.chat(
        ChatInvocation(
            context=_smoke_context("live_smoke.chat", config),
            policy=chat_policy,
            messages=(ChatMessage(role="user", content="Return an object whose ok field is true."),),
            response_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            schema_name="reviewlens_live_smoke",
            estimated_prompt_tokens=CHAT_PROMPT_TOKEN_CEILING,
            estimated_cost_microusd=maximum_microusd,
        ),
        request_id=str(uuid.uuid4()),
    )
    embedding = await client.embeddings(
        EmbeddingInvocation(
            context=_smoke_context("live_smoke.embedding", config),
            policy=embedding_policy,
            inputs=("reviewlens",),
            operation="query_embedding",
            estimated_tokens=EMBEDDING_TOKEN_CEILING,
            estimated_cost_microusd=maximum_microusd,
        ),
        request_id=str(uuid.uuid4()),
    )
    if chat.usage is None or embedding.usage is None:
        raise RuntimeError("live smoke response did not include complete usage")
    actual_microusd = chat.usage.total_cost_microusd + embedding.usage.total_cost_microusd
    if actual_microusd > config.openrouter_smoke_max_cost_microusd:
        raise RuntimeError("live smoke reported cost above its configured ceiling")
    return {
        "status": "succeeded",
        "chat_model": chat.actual_model,
        "embedding_model": embedding.actual_model,
        "vector_count": len(embedding.vectors),
        "actual_cost_microusd": actual_microusd,
        "preflight_max_cost_microusd": maximum_microusd,
    }
