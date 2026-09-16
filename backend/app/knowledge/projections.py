from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from neo4j import GraphDatabase
from qdrant_client import QdrantClient, models as qdrant_models
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    ContextEdge,
    ContextNode,
    ContextNodeVersion,
    EmbeddingPolicyVersion,
    ProjectionOutbox,
    Workspace,
)
from app.knowledge.service import KnowledgeGraphError, read_version_body, utc_now

Vectorizer = Callable[[tuple[str, ...]], Awaitable[tuple[tuple[float, ...], ...]] | tuple[tuple[float, ...], ...]]


def _versions(db: Session, workspace_id: uuid.UUID) -> list[tuple[ContextNode, ContextNodeVersion]]:
    nodes = list(
        db.scalars(
            select(ContextNode)
            .where(ContextNode.workspace_id == workspace_id, ContextNode.status == "active")
            .order_by(ContextNode.id)
        )
    )
    result: list[tuple[ContextNode, ContextNodeVersion]] = []
    for node in nodes:
        if not node.current_version_id:
            continue
        version = db.get(ContextNodeVersion, node.current_version_id)
        if version:
            result.append((node, version))
    return result


def _mark_outbox(db: Session, workspace_id: uuid.UUID, target: str, *, succeeded: bool, code: str | None = None) -> None:
    rows = list(
        db.scalars(
            select(ProjectionOutbox).where(
                ProjectionOutbox.workspace_id == workspace_id,
                ProjectionOutbox.target == target,
                ProjectionOutbox.status.in_(("pending", "processing", "failed")),
            )
        )
    )
    now = utc_now()
    for item in rows:
        item.attempts += 1
        item.lease_expires_at = None
        if succeeded:
            item.status = "published"
            item.published_at = now
            item.error_category = None
            item.error_code = None
            item.next_attempt_at = None
        else:
            item.status = "failed"
            item.error_category = "projection_unavailable"
            item.error_code = code
            item.next_attempt_at = now + timedelta(seconds=min(300, 2 ** min(item.attempts, 8)))


def rebuild_neo4j_projection(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    config: Settings = settings,
    driver: Any | None = None,
) -> dict[str, int]:
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise KnowledgeGraphError("workspace does not exist")
    workspace.neo4j_status = "rebuilding"
    db.flush()
    owns_driver = driver is None
    graph = driver or GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_username, config.neo4j_password),
        connection_timeout=3,
    )
    try:
        versions = list(
            db.scalars(
                select(ContextNodeVersion)
                .where(ContextNodeVersion.workspace_id == workspace.id)
                .order_by(ContextNodeVersion.id)
            )
        )
        node_by_id = {
            item.id: item
            for item in db.scalars(select(ContextNode).where(ContextNode.workspace_id == workspace.id))
        }
        rows = [
            {
                "version_id": str(version.id),
                "node_id": str(version.node_id),
                "workspace_id": str(workspace.id),
                "type": node_by_id[version.node_id].node_type,
                "title": version.title,
                "body_hash": version.body_hash,
                "trust_level": version.trust_level,
                "status": node_by_id[version.node_id].status,
                "current": node_by_id[version.node_id].current_version_id == version.id,
                "version": version.version_number,
            }
            for version in versions
            if version.node_id in node_by_id
        ]
        edges = [
            {
                "edge_id": str(edge.id),
                "source": str(edge.source_version_id),
                "target": str(edge.target_version_id),
                "kind": edge.relation_type,
                "confidence": edge.confidence,
                "status": edge.status,
            }
            for edge in db.scalars(
                select(ContextEdge)
                .where(ContextEdge.workspace_id == workspace.id)
                .order_by(ContextEdge.id)
            )
        ]
        with graph.session() as session:
            session.run(
                "MATCH (n:ContextNodeVersion {workspace_id: $workspace_id}) DETACH DELETE n",
                workspace_id=str(workspace.id),
            ).consume()
            if rows:
                session.run(
                    "UNWIND $rows AS row "
                    "MERGE (n:ContextNodeVersion {version_id: row.version_id}) "
                    "SET n.node_id=row.node_id, n.workspace_id=row.workspace_id, n.type=row.type, "
                    "n.title=row.title, n.body_hash=row.body_hash, n.trust_level=row.trust_level, "
                    "n.status=row.status, n.current=row.current, n.version=row.version",
                    rows=rows,
                ).consume()
            for edge in edges:
                session.run(
                    "MATCH (source:ContextNodeVersion {version_id: $source}), "
                    "(target:ContextNodeVersion {version_id: $target}) "
                    "MERGE (source)-[relation:CONTEXT_RELATION {edge_id: $edge_id}]->(target) "
                    "SET relation.kind=$kind, relation.confidence=$confidence, relation.status=$status",
                    **edge,
                ).consume()
        workspace.neo4j_status = "ready"
        workspace.projection_error_code = None
        _mark_outbox(db, workspace.id, "neo4j", succeeded=True)
        return {"nodes": len(rows), "edges": len(edges)}
    except Exception as exc:
        workspace.neo4j_status = "degraded"
        workspace.status = "degraded"
        workspace.projection_error_code = "neo4j_rebuild_failed"
        _mark_outbox(db, workspace.id, "neo4j", succeeded=False, code=type(exc).__name__)
        raise KnowledgeGraphError("Neo4j projection rebuild failed") from exc
    finally:
        if owns_driver:
            graph.close()


async def rebuild_qdrant_projection(
    db: Session,
    workspace_id: uuid.UUID,
    embedding_policy_version_id: uuid.UUID,
    vectorizer: Vectorizer,
    *,
    config: Settings = settings,
    client: QdrantClient | None = None,
) -> dict[str, int | str]:
    workspace = db.get(Workspace, workspace_id)
    policy = db.get(EmbeddingPolicyVersion, embedding_policy_version_id)
    if not workspace or not policy or policy.lifecycle != "published":
        raise KnowledgeGraphError("published embedding policy and workspace are required")
    if not policy.dimensions or not policy.active_collection:
        raise KnowledgeGraphError("embedding policy requires dimensions and an active collection")
    workspace.qdrant_status = "rebuilding"
    db.flush()
    owns_client = client is None
    qdrant = client or QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key or None, timeout=5)
    try:
        eligible = set(policy.eligible_node_types)
        selected = [item for item in _versions(db, workspace.id) if item[0].node_type in eligible]
        bodies: list[str] = []
        for _, version in selected:
            _, body = read_version_body(workspace, version, config=config)
            bodies.append(body)
        vectors_result = vectorizer(tuple(bodies))
        vectors = await vectors_result if inspect.isawaitable(vectors_result) else vectors_result
        if len(vectors) != len(selected):
            raise KnowledgeGraphError("embedding vector count does not match eligible nodes")
        if any(len(vector) != policy.dimensions for vector in vectors):
            raise KnowledgeGraphError("embedding dimensions do not match the published policy")
        collection = policy.active_collection
        if not qdrant.collection_exists(collection):
            qdrant.create_collection(
                collection_name=collection,
                vectors_config=qdrant_models.VectorParams(
                    size=policy.dimensions,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
        qdrant.delete(
            collection_name=collection,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="workspace_id",
                            match=qdrant_models.MatchValue(value=str(workspace.id)),
                        )
                    ]
                )
            ),
            wait=True,
        )
        points = []
        for (node, version), body, vector in zip(selected, bodies, vectors, strict=True):
            points.append(
                qdrant_models.PointStruct(
                    id=str(version.id),
                    vector=list(vector),
                    payload={
                        "workspace_id": str(workspace.id),
                        "node_id": str(node.id),
                        "node_version_id": str(version.id),
                        "node_type": node.node_type,
                        "trust_level": version.trust_level,
                        "body_hash": version.body_hash,
                        "embedding_policy_version_id": str(policy.id),
                        "preview": body[: config.context_node_preview_characters],
                    },
                )
            )
        if points:
            qdrant.upsert(collection_name=collection, points=points, wait=True)
        workspace.qdrant_status = "ready"
        workspace.projection_error_code = None
        _mark_outbox(db, workspace.id, "qdrant", succeeded=True)
        return {"points": len(points), "collection": collection}
    except Exception as exc:
        workspace.qdrant_status = "degraded"
        workspace.status = "degraded"
        workspace.projection_error_code = "qdrant_rebuild_failed"
        _mark_outbox(db, workspace.id, "qdrant", succeeded=False, code=type(exc).__name__)
        raise KnowledgeGraphError("Qdrant projection rebuild failed") from exc
    finally:
        if owns_client:
            qdrant.close()


def query_qdrant_projection(
    workspace_id: uuid.UUID,
    collection: str,
    vector: tuple[float, ...],
    *,
    allowed_node_types: set[str],
    limit: int,
    minimum_similarity: float,
    config: Settings = settings,
    client: QdrantClient | None = None,
) -> dict[uuid.UUID, float]:
    owns_client = client is None
    qdrant = client or QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key or None, timeout=5)
    try:
        conditions: list[Any] = [
            qdrant_models.FieldCondition(
                key="workspace_id",
                match=qdrant_models.MatchValue(value=str(workspace_id)),
            )
        ]
        if allowed_node_types:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="node_type",
                    match=qdrant_models.MatchAny(any=sorted(allowed_node_types)),
                )
            )
        result = qdrant.query_points(
            collection_name=collection,
            query=list(vector),
            query_filter=qdrant_models.Filter(must=conditions),
            limit=limit,
            score_threshold=minimum_similarity,
            with_payload=True,
        )
        scores: dict[uuid.UUID, float] = {}
        for point in result.points:
            payload = point.payload or {}
            version_id = payload.get("node_version_id")
            if version_id:
                scores[uuid.UUID(str(version_id))] = float(point.score)
        return scores
    finally:
        if owns_client:
            qdrant.close()
