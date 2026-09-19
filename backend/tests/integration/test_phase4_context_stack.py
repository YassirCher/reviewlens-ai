from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    ContextEdge,
    ContextManifest,
    ContextManifestItem,
    ContextNode,
    ContextNodeVersion,
    ProjectionOutbox,
    Workspace,
)
from app.db.session import get_session_factory, session_scope
from app.knowledge.contracts import (
    NodeDraft,
    NodeType,
    RetrievalPolicy,
    RetrievalRequest,
    TrustLevel,
)
from app.knowledge.fixtures import create_context_fixture
from app.knowledge.health import knowledge_health
from app.knowledge.projections import (
    query_qdrant_projection,
    rebuild_neo4j_projection,
    rebuild_qdrant_projection,
)
from app.knowledge.retrieval import ContextBudgetExceeded, build_context_packet
from app.knowledge.service import (
    KnowledgeGraphError,
    create_node,
    export_workspace,
    reconcile_workspace,
)
from app.knowledge.storage import resolve_body_path, workspace_root

pytestmark = pytest.mark.skipif(
    os.getenv("REVIEWLENS_RUN_INTEGRATION") != "1",
    reason="set REVIEWLENS_RUN_INTEGRATION=1 inside the isolated Compose test stack",
)


async def _vectors(values: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    return tuple((1.0, float(index + 1), float(len(value) % 17)) for index, value in enumerate(values))


def _request(fixture, evidence_node_id: uuid.UUID, *, budget: int = 1200) -> RetrievalRequest:
    return RetrievalRequest(
        workspace_id=fixture.workspace_id,
        task_attempt_id=fixture.task_attempt_id,
        query="battery endurance working day",
        seed_node_ids=(evidence_node_id,),
        embedding_policy_version_id=fixture.embedding_policy_version_id,
        policy=RetrievalPolicy(
            allowed_node_types=frozenset(
                {NodeType.PRODUCT, NodeType.SOURCE, NodeType.EVIDENCE, NodeType.CLAIM}
            ),
            allowed_trust_levels=frozenset({TrustLevel.PRIMARY, TrustLevel.DERIVED}),
            required_seed_node_types=frozenset({NodeType.EVIDENCE}),
            maximum_graph_hops=2,
            input_token_budget=budget,
            minimum_similarity=0,
        ),
    )


def _evidence_node_id(workspace_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as db:
        node_id = db.scalar(
            select(ContextNode.id).where(
                ContextNode.workspace_id == workspace_id,
                ContextNode.node_type == "evidence",
            )
        )
    assert node_id is not None
    return node_id


def test_atomic_node_version_relation_manifest_export_and_reconciliation() -> None:
    with session_scope() as db:
        fixture = create_context_fixture(db)
    with session_scope() as db:
        assert db.scalar(
            select(func.count()).select_from(ContextNode).where(ContextNode.workspace_id == fixture.workspace_id)
        ) == 4
        assert db.scalar(
            select(func.count()).select_from(ContextNodeVersion).where(
                ContextNodeVersion.workspace_id == fixture.workspace_id
            )
        ) == 5
        assert db.scalar(
            select(func.count()).select_from(ContextEdge).where(ContextEdge.workspace_id == fixture.workspace_id)
        ) == 4
        manifest = db.get(ContextManifest, fixture.manifest_id)
        assert manifest and manifest.retrieval_mode == "postgres_only"
        assert db.scalar(
            select(func.count()).select_from(ContextManifestItem).where(
                ContextManifestItem.manifest_id == fixture.manifest_id
            )
        ) >= 1
        result = reconcile_workspace(db, fixture.workspace_id)
        exported = export_workspace(db, fixture.workspace_id)
    assert result == {"valid": 5, "missing": 0, "mismatched": 0, "orphaned": 0}
    assert exported.exists() and exported.suffix == ".zip"


def test_context_versions_and_manifests_are_database_immutable() -> None:
    with session_scope() as db:
        fixture = create_context_fixture(db)
    with pytest.raises(DBAPIError):
        with session_scope() as db:
            version = db.scalar(
                select(ContextNodeVersion).where(ContextNodeVersion.workspace_id == fixture.workspace_id)
            )
            assert version
            version.title = "forbidden overwrite"
    with pytest.raises(DBAPIError):
        with session_scope() as db:
            manifest = db.get(ContextManifest, fixture.manifest_id)
            assert manifest
            db.delete(manifest)


def test_current_version_pointer_cannot_cross_node_identity() -> None:
    with session_scope() as db:
        fixture = create_context_fixture(db)
    with pytest.raises(DBAPIError):
        with session_scope() as db:
            nodes = list(
                db.scalars(
                    select(ContextNode)
                    .where(ContextNode.workspace_id == fixture.workspace_id)
                    .order_by(ContextNode.id)
                    .limit(2)
                )
            )
            assert len(nodes) == 2 and nodes[1].current_version_id
            nodes[0].current_version_id = nodes[1].current_version_id


def test_rolled_back_file_is_reconciled_to_quarantine_without_partial_database_state() -> None:
    with session_scope() as db:
        fixture = create_context_fixture(db)
    session = get_session_factory()()
    try:
        version = create_node(
            session,
            fixture.workspace_id,
            NodeDraft(
                node_type=NodeType.EVIDENCE,
                title="Rolled back evidence",
                body="This body must not become authoritative.",
                trust_level=TrustLevel.PRIMARY,
                source_uri="https://www.youtube.com/watch?v=rollbackfixture",
                source_language="en",
                created_by_attempt_id=fixture.task_attempt_id,
            ),
        )
        relative_path = version.body_path
        session.rollback()
    finally:
        session.close()
    with session_scope() as db:
        assert db.get(ContextNodeVersion, version.id) is None
        result = reconcile_workspace(db, fixture.workspace_id)
    assert result["orphaned"] == 1
    root = workspace_root(fixture.workspace_id)
    assert not resolve_body_path(root, relative_path).exists()


def test_real_neo4j_and_qdrant_rebuild_and_deterministic_hybrid_retrieval() -> None:
    with session_scope() as db:
        fixture = create_context_fixture(db)
    with session_scope() as db:
        neo4j_result = rebuild_neo4j_projection(db, fixture.workspace_id)
        qdrant_result = asyncio.run(
            rebuild_qdrant_projection(
                db,
                fixture.workspace_id,
                fixture.embedding_policy_version_id,
                _vectors,
            )
        )
    assert neo4j_result == {"nodes": 5, "edges": 4}
    assert qdrant_result["points"] == 4
    scores = query_qdrant_projection(
        fixture.workspace_id,
        str(qdrant_result["collection"]),
        (1.0, 1.0, 1.0),
        allowed_node_types={"product", "source", "evidence", "claim"},
        limit=10,
        minimum_similarity=0,
    )
    assert scores
    evidence_node_id = _evidence_node_id(fixture.workspace_id)
    with session_scope() as db:
        first = build_context_packet(db, _request(fixture, evidence_node_id), vector_scores=scores)
    with session_scope() as db:
        second = build_context_packet(db, _request(fixture, evidence_node_id), vector_scores=scores)
    assert first.retrieval_mode == "hybrid"
    assert first.rendered == second.rendered
    assert [item.node_version_id for item in first.items] == [item.node_version_id for item in second.items]
    assert "untrusted-data=\"yes\"" in first.rendered
    with session_scope() as db:
        assert db.scalar(
            select(func.count()).select_from(ProjectionOutbox).where(
                ProjectionOutbox.workspace_id == fixture.workspace_id,
                ProjectionOutbox.status != "published",
            )
        ) == 0


def test_qdrant_failure_is_truthful_and_postgres_retrieval_continues() -> None:
    with session_scope() as db:
        fixture = create_context_fixture(db)

    async def unavailable(_: tuple[str, ...]):
        raise ConnectionError("fixture projection unavailable")

    with session_scope() as db:
        with pytest.raises(KnowledgeGraphError, match="Qdrant"):
            asyncio.run(
                rebuild_qdrant_projection(
                    db,
                    fixture.workspace_id,
                    fixture.embedding_policy_version_id,
                    unavailable,
                )
            )
    evidence_node_id = _evidence_node_id(fixture.workspace_id)
    with session_scope() as db:
        workspace = db.get(Workspace, fixture.workspace_id)
        assert workspace and workspace.qdrant_status == "degraded" and workspace.status == "degraded"
        packet = build_context_packet(db, _request(fixture, evidence_node_id))
    assert packet.retrieval_mode == "postgres_only"
    assert packet.items
    assert knowledge_health()["degraded_workspaces"] >= 1


def test_required_evidence_never_truncates_silently_when_budget_is_too_small() -> None:
    with session_scope() as db:
        fixture = create_context_fixture(db)
    evidence_node_id = _evidence_node_id(fixture.workspace_id)
    with session_scope() as db:
        with pytest.raises(ContextBudgetExceeded, match="required evidence"):
            build_context_packet(db, _request(fixture, evidence_node_id, budget=64))
