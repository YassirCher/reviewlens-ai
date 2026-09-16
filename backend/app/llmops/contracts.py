from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*(?:/[a-zA-Z0-9][a-zA-Z0-9._:-]*)+$")
PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
CALL_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenRouterErrorCategory(StrEnum):
    AUTHENTICATION = "openrouter_authentication"
    PAYMENT_REQUIRED = "openrouter_payment_required"
    RATE_LIMITED = "openrouter_rate_limited"
    INVALID_REQUEST = "openrouter_invalid_request"
    MODEL_UNAVAILABLE = "openrouter_model_unavailable"
    PROVIDER_UNAVAILABLE = "openrouter_provider_unavailable"
    SCHEMA_UNSUPPORTED = "openrouter_schema_unsupported"
    TIMEOUT = "openrouter_timeout"
    UPSTREAM_FAILURE = "openrouter_upstream_failure"
    UNKNOWN = "openrouter_unknown"


RETRYABLE_OPENROUTER_CATEGORIES = frozenset(
    {
        OpenRouterErrorCategory.RATE_LIMITED,
        OpenRouterErrorCategory.TIMEOUT,
        OpenRouterErrorCategory.PROVIDER_UNAVAILABLE,
        OpenRouterErrorCategory.UPSTREAM_FAILURE,
    }
)


class OpenRouterError(RuntimeError):
    def __init__(
        self,
        category: OpenRouterErrorCategory,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        correlation_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(category.value)
        self.category = category
        self.status_code = status_code
        self.provider_code = provider_code
        self.correlation_id = correlation_id
        self.retry_after_seconds = retry_after_seconds

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE_OPENROUTER_CATEGORIES


class ProviderRouting(StrictModel):
    mode: Literal["all_compatible", "restricted"] = "all_compatible"
    only: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    allow_fallbacks: bool = True
    require_parameters: bool = True
    data_collection: Literal["deny", "allow"] = "deny"
    zdr: bool | None = None
    sort: Literal["price", "throughput", "latency"] | None = None
    preferred_min_throughput: float | None = Field(default=None, ge=0)
    preferred_max_latency: float | None = Field(default=None, ge=0)
    quantizations: tuple[str, ...] = ()
    max_price: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_routing(self) -> "ProviderRouting":
        if self.mode == "restricted" and not self.only:
            raise ValueError("restricted provider mode requires at least one provider slug")
        if self.mode == "all_compatible" and self.only:
            raise ValueError("all-compatible provider mode cannot define provider.only")
        if len(set(self.only)) != len(self.only) or len(set(self.order)) != len(self.order):
            raise ValueError("provider slugs must be unique")
        if any(not PROVIDER_PATTERN.fullmatch(item) for item in (*self.only, *self.order)):
            raise ValueError("provider routing requires canonical lowercase slugs")
        if self.order and (self.mode != "restricted" or not set(self.order) <= set(self.only)):
            raise ValueError("provider.order must be a subset of provider.only")
        if any(value < 0 for value in self.max_price.values()):
            raise ValueError("maximum prices cannot be negative")
        return self

    def to_openrouter(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "allow_fallbacks": self.allow_fallbacks,
            "require_parameters": self.require_parameters,
            "data_collection": self.data_collection,
        }
        if self.mode == "restricted":
            payload["only"] = list(self.only)
            if self.order:
                payload["order"] = list(self.order)
        if self.zdr is not None:
            payload["zdr"] = self.zdr
        if self.sort:
            payload["sort"] = self.sort
        if self.preferred_min_throughput is not None:
            payload["preferred_min_throughput"] = self.preferred_min_throughput
        if self.preferred_max_latency is not None:
            payload["preferred_max_latency"] = self.preferred_max_latency
        if self.quantizations:
            payload["quantizations"] = list(self.quantizations)
        if self.max_price:
            payload["max_price"] = {key: str(value) for key, value in self.max_price.items()}
        return payload


class ModelPolicyDocument(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)
    models: tuple[str, ...] = Field(min_length=1, max_length=8)
    provider: ProviderRouting = Field(default_factory=ProviderRouting)
    temperature: float = Field(default=0.1, ge=0, le=2)
    minimum_context_tokens: int = Field(default=0, ge=0, le=10_000_000)
    max_completion_tokens: int = Field(ge=1, le=100_000)
    reasoning: dict[str, Any] = Field(default_factory=dict)
    compatibility_mode: Literal["strict", "json_object"] = "strict"

    @model_validator(mode="after")
    def validate_models(self) -> "ModelPolicyDocument":
        if len(set(self.models)) != len(self.models):
            raise ValueError("model fallback slugs must be unique")
        if any(not SLUG_PATTERN.fullmatch(item) for item in self.models):
            raise ValueError("model policies require canonical model slugs")
        return self


class EmbeddingPolicyDocument(StrictModel):
    schema_version: Literal[1] = 1
    model: str
    provider: ProviderRouting = Field(default_factory=ProviderRouting)
    dimensions: int | None = Field(default=None, ge=1)
    input_type: Literal["search_document", "search_query"] | None = None

    @model_validator(mode="after")
    def validate_model(self) -> "EmbeddingPolicyDocument":
        if not SLUG_PATTERN.fullmatch(self.model):
            raise ValueError("embedding policies require a canonical model slug")
        return self


class ChatMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str


class InvocationContext(StrictModel):
    run_id: uuid.UUID
    task_run_id: uuid.UUID
    task_attempt_id: uuid.UUID
    agent_version_id: uuid.UUID
    workflow_version_id: uuid.UUID
    model_policy_version_id: uuid.UUID
    embedding_policy_version_id: uuid.UUID | None = None
    call_key: str
    deadline_at: datetime
    initiator_type: str

    @model_validator(mode="after")
    def validate_call_key(self) -> "InvocationContext":
        if not CALL_KEY_PATTERN.fullmatch(self.call_key):
            raise ValueError("call_key must be a lowercase stable identifier")
        return self


class ChatInvocation(StrictModel):
    context: InvocationContext
    policy: ModelPolicyDocument
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    response_schema: dict[str, Any]
    schema_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    estimated_prompt_tokens: int = Field(ge=0)
    estimated_cost_microusd: int = Field(ge=0)
    max_network_attempts: int | None = Field(default=None, ge=1, le=5)


class EmbeddingInvocation(StrictModel):
    context: InvocationContext
    policy: EmbeddingPolicyDocument
    inputs: tuple[str, ...] = Field(min_length=1, max_length=256)
    operation: Literal["document_embedding", "query_embedding"]
    estimated_tokens: int = Field(ge=0)
    estimated_cost_microusd: int = Field(ge=0)


class NormalizedUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    audio_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_cost_microusd: int = Field(default=0, ge=0)
    upstream_cost_microusd: int | None = Field(default=None, ge=0)


class ChatResult(StrictModel):
    content: dict[str, Any]
    generation_id: str | None
    actual_model: str | None
    actual_provider: str | None
    finish_reason: str | None
    service_tier: str | None
    usage: NormalizedUsage | None
    latency_ms: int = Field(ge=0)


class EmbeddingResult(StrictModel):
    vectors: tuple[tuple[float, ...], ...]
    generation_id: str | None
    actual_model: str | None
    actual_provider: str | None
    usage: NormalizedUsage | None
    latency_ms: int = Field(ge=0)


def dollars_to_microusd(value: Decimal | str | float | int | None) -> int | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    if amount < 0:
        raise ValueError("cost cannot be negative")
    return int((amount * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def strictify_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                properties = node.get("properties", {})
                node["additionalProperties"] = False
                if properties:
                    node["required"] = list(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(result)
    return result
