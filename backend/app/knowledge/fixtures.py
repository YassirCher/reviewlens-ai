from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import EmbeddingPolicyVersion
from app.knowledge.contracts import (
    NodeDraft,
    NodeType,
    RelationDraft,
    RelationType,
    RetrievalPolicy,
    RetrievalRequest,
    TrustLevel,
)
from app.knowledge.retrieval import build_context_packet
from app.knowledge.service import create_node, create_node_version, create_relation, create_workspace
from app.knowledge.storage import canonical_json_hash
from app.llmops.fixtures import create_llmops_fixture_attempt


@dataclass(frozen=True)
class ContextFixture:
    workspace_id: uuid.UUID
    task_attempt_id: uuid.UUID
    embedding_policy_version_id: uuid.UUID
    node_count: int
    edge_count: int
    manifest_id: uuid.UUID
    retrieval_mode: str


def _edge_key(source: uuid.UUID, target: uuid.UUID, relation: RelationType) -> str:
    return canonical_json_hash(
        {"source": str(source), "target": str(target), "relation": relation.value}
    )


def create_context_fixture(db: Session) -> ContextFixture:
    invocation, _, _ = create_llmops_fixture_attempt(db)
    base_embedding = db.get(EmbeddingPolicyVersion, invocation.embedding_policy_version_id)
    assert base_embedding is not None
    embedding = EmbeddingPolicyVersion(
        definition_id=base_embedding.definition_id,
        version_number=2,
        lifecycle="published",
        content_hash=canonical_json_hash({"fixture": "phase4", "dimensions": 3}),
        change_note="Phase 4 deterministic projection fixture",
        published_at=datetime.now(timezone.utc),
        model_slug=base_embedding.model_slug,
        provider_policy=base_embedding.provider_policy,
        dimensions=3,
        chunking_version="phase4-fixture-v1",
        eligible_node_types=["product", "source", "evidence", "claim"],
        active_collection=f"reviewlens_phase4_{invocation.run_id.hex}",
    )
    db.add(embedding)
    db.flush()
    workspace = create_workspace(db, invocation.run_id)
    creator = invocation.task_attempt_id
    source_uri = "https://www.youtube.com/watch?v=phase4fixture"
    product_v1 = create_node(
        db,
        workspace.id,
        NodeDraft(
            node_type=NodeType.PRODUCT,
            title="Fixture Product",
            body="# Fixture Product\n\nCanonical research scope.",
            trust_level=TrustLevel.DERIVED,
            confidence=100,
            tags=("fixture", "product"),
            provenance={"fixture": True},
            created_by_attempt_id=creator,
        ),
    )
    product_v2 = create_node_version(
        db,
        product_v1.node_id,
        NodeDraft(
            node_type=NodeType.PRODUCT,
            title="Fixture Product",
            body="# Fixture Product\n\nCanonical research scope, version two.",
            trust_level=TrustLevel.DERIVED,
            confidence=100,
            tags=("fixture", "product"),
            provenance={"fixture": True, "revision": 2},
            created_by_attempt_id=creator,
        ),
    )
    source = create_node(
        db,
        workspace.id,
        NodeDraft(
            node_type=NodeType.SOURCE,
            title="Fixture Review Video",
            body="# Fixture Review Video\n\nSelected primary review source.",
            trust_level=TrustLevel.PRIMARY,
            source_uri=source_uri,
            source_language="en",
            confidence=95,
            tags=("fixture", "battery"),
            created_by_attempt_id=creator,
        ),
    )
    evidence = create_node(
        db,
        workspace.id,
        NodeDraft(
            node_type=NodeType.EVIDENCE,
            title="Battery evidence",
            body="Battery lasted nine hours in the reviewer's repeatable test. Timestamp: 04:12.",
            trust_level=TrustLevel.PRIMARY,
            source_uri=source_uri,
            source_language="en",
            confidence=91,
            tags=("battery", "endurance"),
            provenance={"timestamp_seconds": 252},
            created_by_attempt_id=creator,
            public_visibility="public",
        ),
    )
    claim = create_node(
        db,
        workspace.id,
        NodeDraft(
            node_type=NodeType.CLAIM,
            title="Battery lasts a working day",
            body="The tested battery duration covers a typical working day under the stated workload.",
            trust_level=TrustLevel.DERIVED,
            source_uri=source_uri,
            source_language="en",
            confidence=86,
            tags=("battery", "claim"),
            provenance={"source_node_ids": [str(evidence.node_id)]},
            created_by_attempt_id=creator,
            public_visibility="public",
        ),
    )
    relation_drafts = (
        RelationDraft(
            source_version_id=source.id,
            target_version_id=product_v2.id,
            relation_type=RelationType.ABOUT,
            created_by_attempt_id=creator,
            idempotency_key=_edge_key(source.id, product_v2.id, RelationType.ABOUT),
        ),
        RelationDraft(
            source_version_id=evidence.id,
            target_version_id=source.id,
            relation_type=RelationType.DERIVED_FROM,
            created_by_attempt_id=creator,
            idempotency_key=_edge_key(evidence.id, source.id, RelationType.DERIVED_FROM),
        ),
        RelationDraft(
            source_version_id=evidence.id,
            target_version_id=claim.id,
            relation_type=RelationType.SUPPORTS,
            created_by_attempt_id=creator,
            idempotency_key=_edge_key(evidence.id, claim.id, RelationType.SUPPORTS),
        ),
    )
    for relation in relation_drafts:
        create_relation(db, workspace.id, relation)
    packet = build_context_packet(
        db,
        RetrievalRequest(
            workspace_id=workspace.id,
            task_attempt_id=creator,
            query="battery endurance working day",
            seed_node_ids=(evidence.node_id,),
            embedding_policy_version_id=embedding.id,
            policy=RetrievalPolicy(
                allowed_node_types=frozenset(
                    {NodeType.PRODUCT, NodeType.SOURCE, NodeType.EVIDENCE, NodeType.CLAIM}
                ),
                allowed_trust_levels=frozenset({TrustLevel.PRIMARY, TrustLevel.DERIVED}),
                required_seed_node_types=frozenset({NodeType.EVIDENCE}),
                maximum_graph_hops=2,
                input_token_budget=1200,
            ),
        ),
    )
    return ContextFixture(
        workspace_id=workspace.id,
        task_attempt_id=creator,
        embedding_policy_version_id=embedding.id,
        node_count=4,
        edge_count=4,
        manifest_id=packet.manifest_id,
        retrieval_mode=packet.retrieval_mode,
    )
