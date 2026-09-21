from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator

from app.config import Settings, settings
from app.llmops.accounting import (
    BudgetRejected,
    finalize_failed_request,
    finalize_successful_request,
    reserve_request,
    update_account_state,
)
from app.llmops.client import OpenRouterClient
from app.llmops.contracts import (
    ChatInvocation,
    ChatResult,
    EmbeddingInvocation,
    EmbeddingResult,
    OpenRouterError,
    OpenRouterErrorCategory,
)
from app.observability import operation_context

logger = logging.getLogger(__name__)


def _remaining_seconds(deadline: datetime) -> float:
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return (deadline - datetime.now(timezone.utc)).total_seconds()


class OpenRouterGateway:
    def __init__(
        self,
        config: Settings = settings,
        *,
        client: OpenRouterClient | None = None,
        sleeper: Any = asyncio.sleep,
    ) -> None:
        self.config = config
        self.client = client or OpenRouterClient(config)
        self.sleeper = sleeper

    def _delay(self, retry_number: int, error: OpenRouterError) -> float:
        calculated = min(
            self.config.openrouter_retry_max_seconds,
            self.config.openrouter_retry_base_seconds * (2 ** (retry_number - 1)),
        )
        if error.retry_after_seconds is not None:
            return min(self.config.openrouter_retry_max_seconds, max(calculated, error.retry_after_seconds))
        return calculated

    async def chat(self, invocation: ChatInvocation) -> ChatResult:
        with operation_context(run_id=invocation.context.run_id,
                               task_id=invocation.context.task_run_id,
                               attempt_id=invocation.context.task_attempt_id):
            return await self._chat(invocation)

    async def _chat(self, invocation: ChatInvocation) -> ChatResult:
        Draft202012Validator.check_schema(invocation.response_schema)
        last_error: OpenRouterError | None = None
        estimated_tokens = invocation.estimated_prompt_tokens + invocation.policy.max_completion_tokens
        max_attempts = invocation.max_network_attempts or self.config.openrouter_max_attempts
        for retry_number in range(1, max_attempts + 1):
            if _remaining_seconds(invocation.context.deadline_at) <= 0:
                raise BudgetRejected("paid_call_deadline_elapsed")
            reservation_id, request_id = reserve_request(
                invocation.context,
                operation="chat",
                retry_number=retry_number,
                estimated_tokens=estimated_tokens,
                estimated_cost_microusd=invocation.estimated_cost_microusd,
                requested_models=list(invocation.policy.models),
                config=self.config,
            )
            try:
                result = await self.client.chat(invocation, request_id)
            except OpenRouterError as exc:
                finalize_failed_request(
                    reservation_id,
                    category=exc.category,
                    error_code=exc.provider_code,
                )
                update_account_state(category=exc.category, error_code=exc.provider_code, config=self.config)
                last_error = exc
                if not exc.retryable or retry_number >= max_attempts:
                    raise
                delay = self._delay(retry_number, exc)
                if delay >= _remaining_seconds(invocation.context.deadline_at):
                    raise
                await self.sleeper(delay)
                continue
            validation = Draft202012Validator(invocation.response_schema)
            schema_errors = list(validation.iter_errors(result.content))
            valid = not schema_errors
            finalize_successful_request(
                reservation_id,
                generation_id=result.generation_id,
                actual_model=result.actual_model,
                actual_provider=result.actual_provider,
                usage_value=result.usage,
                latency_ms=result.latency_ms,
                finish_reason=result.finish_reason,
                service_tier=result.service_tier,
                result_valid=valid,
                config=self.config,
            )
            try:
                from app.admin.retention import retain_chat_content
                retain_chat_content(reservation_id, [item.model_dump() for item in invocation.messages], result.content)
            except Exception as exc:
                logger.error("Encrypted raw content retention failed: %s", type(exc).__name__)
            update_account_state(config=self.config)
            if not valid:
                formatted_errors = [f"{list(err.path)}: {err.message}" for err in schema_errors[:10]]
                logger.error(
                    "Schema validation failed for call_key=%s (task=%s): %s",
                    invocation.context.call_key,
                    invocation.context.task_run_id,
                    formatted_errors,
                )
                raise OpenRouterError(
                    OpenRouterErrorCategory.UNKNOWN,
                    provider_code="schema_validation_failed",
                )
            return result
        assert last_error is not None
        raise last_error

    async def embeddings(self, invocation: EmbeddingInvocation) -> EmbeddingResult:
        with operation_context(run_id=invocation.context.run_id,
                               task_id=invocation.context.task_run_id,
                               attempt_id=invocation.context.task_attempt_id):
            return await self._embeddings(invocation)

    async def _embeddings(self, invocation: EmbeddingInvocation) -> EmbeddingResult:
        last_error: OpenRouterError | None = None
        for retry_number in range(1, self.config.openrouter_max_attempts + 1):
            if _remaining_seconds(invocation.context.deadline_at) <= 0:
                raise BudgetRejected("paid_call_deadline_elapsed")
            reservation_id, request_id = reserve_request(
                invocation.context,
                operation=invocation.operation,
                retry_number=retry_number,
                estimated_tokens=invocation.estimated_tokens,
                estimated_cost_microusd=invocation.estimated_cost_microusd,
                requested_models=[invocation.policy.model],
                config=self.config,
            )
            try:
                result = await self.client.embeddings(invocation, request_id)
            except OpenRouterError as exc:
                finalize_failed_request(reservation_id, category=exc.category, error_code=exc.provider_code)
                update_account_state(category=exc.category, error_code=exc.provider_code, config=self.config)
                last_error = exc
                if not exc.retryable or retry_number >= self.config.openrouter_max_attempts:
                    raise
                delay = self._delay(retry_number, exc)
                if delay >= _remaining_seconds(invocation.context.deadline_at):
                    raise
                await self.sleeper(delay)
                continue
            finalize_successful_request(
                reservation_id,
                generation_id=result.generation_id,
                actual_model=result.actual_model,
                actual_provider=result.actual_provider,
                usage_value=result.usage,
                latency_ms=result.latency_ms,
                config=self.config,
            )
            update_account_state(config=self.config)
            return result
        assert last_error is not None
        raise last_error
