from __future__ import annotations

import math
from dataclasses import dataclass

from app.llmops.contracts import (
    EmbeddingInvocation,
    EmbeddingPolicyDocument,
    InvocationContext,
)
from app.llmops.gateway import OpenRouterGateway
from app.knowledge.retrieval import estimate_tokens


@dataclass(frozen=True)
class OpenRouterVectorizer:
    """Metered OpenRouter embedding seam for projection and query vectors."""

    gateway: OpenRouterGateway
    context: InvocationContext
    policy: EmbeddingPolicyDocument
    estimated_microusd_per_thousand_tokens: int

    async def embed_documents(self, values: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return await self._embed(values, operation="document_embedding", input_type="search_document")

    async def embed_query(self, value: str) -> tuple[float, ...]:
        vectors = await self._embed((value,), operation="query_embedding", input_type="search_query")
        return vectors[0]

    async def _embed(
        self,
        values: tuple[str, ...],
        *,
        operation: str,
        input_type: str,
    ) -> tuple[tuple[float, ...], ...]:
        tokens = sum(estimate_tokens(item) for item in values)
        estimated_cost = math.ceil(tokens * self.estimated_microusd_per_thousand_tokens / 1000)
        result = await self.gateway.embeddings(
            EmbeddingInvocation(
                context=self.context,
                policy=self.policy.model_copy(update={"input_type": input_type}),
                inputs=values,
                operation=operation,
                estimated_tokens=tokens,
                estimated_cost_microusd=estimated_cost,
            )
        )
        return result.vectors
