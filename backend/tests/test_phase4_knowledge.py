from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.db import models as db_models  # noqa: F401
from app.db.base import Base
from app.knowledge.contracts import (
    NodeDraft,
    NodeType,
    RelationType,
    RetrievalPolicy,
    TrustLevel,
    relation_is_allowed,
)
from app.knowledge.embedding import OpenRouterVectorizer
from app.knowledge.retrieval import estimate_tokens
from app.knowledge.storage import (
    MarkdownValidationError,
    atomic_write,
    body_hash,
    parse_markdown,
    render_markdown,
    resolve_body_path,
    version_relative_path,
)
from app.llmops.contracts import (
    EmbeddingPolicyDocument,
    EmbeddingResult,
    InvocationContext,
    NormalizedUsage,
)


def _frontmatter(node_id: uuid.UUID, workspace_id: uuid.UUID) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    body = "Evidence body\n"
    return {
        "id": str(node_id),
        "workspace_id": str(workspace_id),
        "run_id": str(uuid.uuid4()),
        "type": "evidence",
        "title": "Battery evidence",
        "status": "active",
        "version": 1,
        "source_uri": "https://www.youtube.com/watch?v=fixture",
        "source_language": "en",
        "trust_level": "primary_source",
        "confidence": 90,
        "tags": ["battery"],
        "created_at": now,
        "updated_at": now,
        "content_hash": body_hash(body),
    }


def test_phase4_tables_are_registered() -> None:
    assert {
        "workspaces",
        "context_nodes",
        "context_node_versions",
        "context_edges",
        "context_manifests",
        "context_manifest_items",
        "projection_outbox",
    } <= set(Base.metadata.tables)


def test_markdown_round_trip_requires_complete_hash_valid_frontmatter() -> None:
    node_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    rendered = render_markdown(_frontmatter(node_id, workspace_id), "Evidence body")
    metadata, body = parse_markdown(rendered)
    assert metadata["id"] == str(node_id)
    assert metadata["workspace_id"] == str(workspace_id)
    assert body == "Evidence body\n"
    with pytest.raises(MarkdownValidationError, match="content_hash"):
        parse_markdown(rendered.replace("Evidence body", "Tampered body"))
    incomplete = _frontmatter(node_id, workspace_id)
    incomplete.pop("trust_level")
    with pytest.raises(MarkdownValidationError, match="trust_level"):
        render_markdown(incomplete, "Evidence body")


def test_atomic_paths_never_escape_workspace(tmp_path) -> None:
    relative = version_relative_path("evidence", uuid.uuid4(), 1)
    destination = atomic_write(tmp_path, relative, "safe")
    assert destination.read_text(encoding="utf-8") == "safe"
    with pytest.raises(MarkdownValidationError, match="workspace-relative"):
        resolve_body_path(tmp_path, "../outside.md")
    with pytest.raises(MarkdownValidationError, match="node type"):
        version_relative_path("../evidence", uuid.uuid4(), 1)


def test_node_contract_requires_provenance_and_source_identity() -> None:
    with pytest.raises(ValidationError, match="creating task attempt"):
        NodeDraft(
            node_type=NodeType.CLAIM,
            title="Claim",
            body="Body",
            trust_level=TrustLevel.DERIVED,
        )
    with pytest.raises(ValidationError, match="source_uri"):
        NodeDraft(
            node_type=NodeType.EVIDENCE,
            title="Evidence",
            body="Body",
            trust_level=TrustLevel.PRIMARY,
            created_by_attempt_id=uuid.uuid4(),
        )


def test_relation_schema_rejects_invalid_type_pairs() -> None:
    assert relation_is_allowed(NodeType.EVIDENCE, NodeType.CLAIM, RelationType.SUPPORTS)
    assert relation_is_allowed(NodeType.SOURCE, NodeType.PRODUCT, RelationType.ABOUT)
    assert not relation_is_allowed(NodeType.PRODUCT, NodeType.SOURCE, RelationType.ABOUT)
    assert not relation_is_allowed(NodeType.REPORT, NodeType.PRODUCT, RelationType.CONTAINS)


def test_retrieval_policy_requires_explicit_operational_memory_permission() -> None:
    with pytest.raises(ValidationError, match="agent_memory"):
        RetrievalPolicy(
            allowed_node_types=frozenset({NodeType.AGENT_MEMORY}),
            allowed_trust_levels=frozenset({TrustLevel.OPERATIONAL}),
            input_token_budget=100,
        )


def test_token_estimation_is_deterministic_and_content_sensitive() -> None:
    assert estimate_tokens("battery: nine hours") == 4
    assert estimate_tokens("battery: nine hours") == estimate_tokens("battery: nine hours")
    assert estimate_tokens("battery: nine hours!") > estimate_tokens("battery nine hours")


def test_openrouter_vectorizer_builds_metered_document_and_query_calls() -> None:
    calls = []

    class GatewayStub:
        async def embeddings(self, invocation):
            calls.append(invocation)
            return EmbeddingResult(
                vectors=tuple((1.0, float(index), 0.0) for index, _ in enumerate(invocation.inputs)),
                generation_id="fixture",
                actual_model="fixture/embedding",
                actual_provider="fixture",
                usage=NormalizedUsage(total_tokens=invocation.estimated_tokens),
                latency_ms=1,
            )

    context = InvocationContext(
        run_id=uuid.uuid4(),
        task_run_id=uuid.uuid4(),
        task_attempt_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        workflow_version_id=uuid.uuid4(),
        model_policy_version_id=uuid.uuid4(),
        embedding_policy_version_id=uuid.uuid4(),
        call_key="context.embedding",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        initiator_type="test",
    )
    vectorizer = OpenRouterVectorizer(
        gateway=GatewayStub(),  # type: ignore[arg-type]
        context=context,
        policy=EmbeddingPolicyDocument(model="fixture/embedding", dimensions=3),
        estimated_microusd_per_thousand_tokens=100,
    )

    async def run() -> None:
        documents = await vectorizer.embed_documents(("one document", "two document"))
        query = await vectorizer.embed_query("battery")
        assert len(documents) == 2
        assert len(query) == 3

    asyncio.run(run())
    assert [call.operation for call in calls] == ["document_embedding", "query_embedding"]
    assert [call.policy.input_type for call in calls] == ["search_document", "search_query"]
    assert all(call.estimated_cost_microusd >= 0 for call in calls)
