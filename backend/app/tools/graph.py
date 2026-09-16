from __future__ import annotations

import math
import uuid
from collections.abc import Awaitable, Callable
from datetime import timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    AgentVersion,
    AnalysisRun,
    ConfigurationSnapshot,
    ContextEdge,
    ContextNode,
    ContextNodeVersion,
    EmbeddingPolicyVersion,
    OpenRouterModelSnapshot,
    ProjectionOutbox,
    TaskRun,
    Workspace,
)
from app.knowledge.contracts import NodeDraft, NodeType, RelationDraft, RelationType, TrustLevel
from app.knowledge.embedding import OpenRouterVectorizer
from app.knowledge.projections import query_qdrant_projection
from app.knowledge.service import create_node, create_relation, read_version_body
from app.llmops.contracts import EmbeddingPolicyDocument, InvocationContext
from app.llmops.gateway import OpenRouterGateway
from app.runtime.contracts import canonical_json_hash
from app.tools.contracts import (
    GraphCreateEdgesInput,
    GraphCreateEdgesOutput,
    GraphCreateNodesInput,
    GraphCreateNodesOutput,
    GraphGetNodesInput,
    GraphGetNodesOutput,
    GraphNodeOutput,
    GraphQueryRelationsInput,
    GraphQueryRelationsOutput,
    GraphRelationOutput,
    ToolExecutionContext,
    VectorRequestUpsertInput,
    VectorRequestUpsertOutput,
    VectorSearchHit,
    VectorSearchInput,
    VectorSearchOutput,
)
from app.tools.errors import ToolAuthorizationError, ToolExecutionError


def _workspace(db: Session, context: ToolExecutionContext) -> Workspace:
    if context.workspace_id is None:
        raise ToolAuthorizationError("workspace_required")
    workspace = db.get(Workspace, context.workspace_id)
    if workspace is None or workspace.run_id != context.run_id or workspace.status == "deleted":
        raise ToolAuthorizationError("workspace_not_authorized")
    return workspace


def get_nodes(
    db: Session,
    context: ToolExecutionContext,
    request: GraphGetNodesInput,
    *,
    config: Settings = settings,
) -> GraphGetNodesOutput:
    workspace = _workspace(db, context)
    nodes = list(
        db.scalars(
            select(ContextNode).where(
                ContextNode.id.in_(request.node_ids),
                ContextNode.workspace_id == workspace.id,
                ContextNode.status == "active",
            )
        )
    )
    output: list[GraphNodeOutput] = []
    for node in sorted(nodes, key=lambda item: str(item.id)):
        if not node.current_version_id:
            continue
        version = db.get(ContextNodeVersion, node.current_version_id)
        if version is None:
            continue
        _, body = read_version_body(workspace, version, config=config)
        output.append(
            GraphNodeOutput(
                node_id=node.id,
                node_version_id=version.id,
                node_type=node.node_type,
                title=version.title,
                body_hash=version.body_hash,
                trust_level=version.trust_level,
                source_uri=version.source_uri,
                source_language=version.source_language,
                content=body,
            )
        )
    return GraphGetNodesOutput(nodes=tuple(output))


def query_relations(
    db: Session,
    context: ToolExecutionContext,
    request: GraphQueryRelationsInput,
) -> GraphQueryRelationsOutput:
    workspace = _workspace(db, context)
    seeds = list(
        db.scalars(
            select(ContextNode).where(
                ContextNode.id.in_(request.seed_node_ids),
                ContextNode.workspace_id == workspace.id,
                ContextNode.status == "active",
            )
        )
    )
    if len(seeds) != len(set(request.seed_node_ids)):
        raise ToolAuthorizationError("relation_seed_not_authorized")
    frontier = {item.current_version_id for item in seeds if item.current_version_id}
    visited = set(frontier)
    results: dict[uuid.UUID, ContextEdge] = {}
    for _ in range(request.maximum_hops):
        if not frontier or len(results) >= request.limit:
            break
        conditions = []
        if request.direction in {"outgoing", "both"}:
            conditions.append(ContextEdge.source_version_id.in_(frontier))
        if request.direction in {"incoming", "both"}:
            conditions.append(ContextEdge.target_version_id.in_(frontier))
        statement = select(ContextEdge).where(
            ContextEdge.workspace_id == workspace.id,
            ContextEdge.status == "active",
            or_(*conditions),
        )
        if request.relation_types:
            statement = statement.where(ContextEdge.relation_type.in_(request.relation_types))
        edges = list(db.scalars(statement.order_by(ContextEdge.created_at, ContextEdge.id)))
        next_frontier: set[uuid.UUID] = set()
        for edge in edges:
            results[edge.id] = edge
            for version_id in (edge.source_version_id, edge.target_version_id):
                if version_id not in visited:
                    next_frontier.add(version_id)
                    visited.add(version_id)
            if len(results) >= request.limit:
                break
        frontier = next_frontier
    version_ids = {
        version_id
        for edge in results.values()
        for version_id in (edge.source_version_id, edge.target_version_id)
    }
    versions = {item.id: item for item in db.scalars(select(ContextNodeVersion).where(ContextNodeVersion.id.in_(version_ids)))}
    return GraphQueryRelationsOutput(
        relations=tuple(
            GraphRelationOutput(
                edge_id=edge.id,
                source_node_id=versions[edge.source_version_id].node_id,
                target_node_id=versions[edge.target_version_id].node_id,
                relation_type=edge.relation_type,
                confidence=edge.confidence,
            )
            for edge in results.values()
        )
    )


def create_nodes(
    db: Session,
    context: ToolExecutionContext,
    request: GraphCreateNodesInput,
    *,
    config: Settings = settings,
) -> GraphCreateNodesOutput:
    workspace = _workspace(db, context)
    created = []
    for item in request.nodes:
        version = create_node(
            db,
            workspace.id,
            NodeDraft(
                node_type=NodeType(item.node_type),
                title=item.title,
                body=item.body,
                trust_level=TrustLevel(item.trust_level),
                source_uri=item.source_uri,
                source_language=item.source_language,
                confidence=item.confidence,
                tags=item.tags,
                provenance=item.provenance,
                public_visibility=item.public_visibility,
                created_by_attempt_id=context.task_attempt_id,
            ),
            config=config,
        )
        created.append(version.id)
    return GraphCreateNodesOutput(node_version_ids=tuple(created))


def create_edges(
    db: Session,
    context: ToolExecutionContext,
    request: GraphCreateEdgesInput,
) -> GraphCreateEdgesOutput:
    workspace = _workspace(db, context)
    created = []
    for item in request.edges:
        edge = create_relation(
            db,
            workspace.id,
            RelationDraft(
                source_version_id=item.source_version_id,
                target_version_id=item.target_version_id,
                relation_type=RelationType(item.relation_type),
                confidence=item.confidence,
                properties=item.properties,
                created_by_attempt_id=context.task_attempt_id,
                idempotency_key=item.idempotency_key,
            ),
        )
        created.append(edge.id)
    return GraphCreateEdgesOutput(edge_ids=tuple(created))


def request_vector_upsert(
    db: Session,
    context: ToolExecutionContext,
    request: VectorRequestUpsertInput,
) -> VectorRequestUpsertOutput:
    workspace = _workspace(db, context)
    nodes = list(
        db.scalars(
            select(ContextNode).where(
                ContextNode.id.in_(request.node_ids),
                ContextNode.workspace_id == workspace.id,
                ContextNode.status == "active",
            )
        )
    )
    if len(nodes) != len(set(request.node_ids)):
        raise ToolAuthorizationError("projection_node_not_authorized")
    queued = 0
    for node in nodes:
        if not node.current_version_id:
            continue
        version = db.get(ContextNodeVersion, node.current_version_id)
        assert version is not None
        key = f"qdrant:context_node:{node.id}:{version.version_number}:upsert"
        existing = db.scalar(select(ProjectionOutbox.id).where(ProjectionOutbox.idempotency_key == key))
        if existing:
            continue
        db.add(
            ProjectionOutbox(
                workspace_id=workspace.id,
                target="qdrant",
                aggregate_type="context_node",
                aggregate_id=node.id,
                aggregate_version=version.version_number,
                operation="upsert",
                payload={"node_version_id": str(version.id), "body_hash": version.body_hash},
                idempotency_key=key,
            )
        )
        queued += 1
    return VectorRequestUpsertOutput(queued=queued)


def _embedding_price_per_thousand(db: Session, model_slug: str) -> int:
    snapshot = db.scalar(
        select(OpenRouterModelSnapshot)
        .where(
            OpenRouterModelSnapshot.model_kind == "embedding",
            OpenRouterModelSnapshot.slug == model_slug,
        )
        .order_by(OpenRouterModelSnapshot.fetched_at.desc())
        .limit(1)
    )
    if snapshot is None:
        raise ToolExecutionError("embedding_catalog_missing", category="configuration")
    try:
        price = Decimal(str(snapshot.pricing["prompt"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ToolExecutionError("embedding_price_missing", category="configuration") from exc
    if not price.is_finite() or price < 0:
        raise ToolExecutionError("embedding_price_invalid", category="configuration")
    return int((price * Decimal(1_000_000_000)).quantize(Decimal("1"), rounding=ROUND_CEILING))


async def _default_query_vector(
    db: Session,
    context: ToolExecutionContext,
    query: str,
) -> tuple[float, ...]:
    task = db.get(TaskRun, context.task_run_id)
    run = db.get(AnalysisRun, context.run_id)
    if not task or not run or not task.agent_version_id:
        raise ToolAuthorizationError("vector_search_requires_agent")
    agent = db.get(AgentVersion, task.agent_version_id)
    snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id)
    if not agent or not agent.model_policy_version_id or not snapshot or not snapshot.embedding_policy_version_id:
        raise ToolExecutionError("vector_policy_missing", category="configuration")
    embedding = db.get(EmbeddingPolicyVersion, snapshot.embedding_policy_version_id)
    if not embedding or embedding.lifecycle != "published" or not embedding.model_slug:
        raise ToolExecutionError("embedding_policy_unpublished", category="configuration")
    deadline = context.deadline_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    invocation_context = InvocationContext(
        run_id=context.run_id,
        task_run_id=context.task_run_id,
        task_attempt_id=context.task_attempt_id,
        agent_version_id=agent.id,
        workflow_version_id=snapshot.workflow_version_id,
        model_policy_version_id=agent.model_policy_version_id,
        embedding_policy_version_id=embedding.id,
        call_key="vector.search",
        deadline_at=deadline,
        initiator_type=run.initiator_type,
    )
    vectorizer = OpenRouterVectorizer(
        gateway=OpenRouterGateway(),
        context=invocation_context,
        policy=EmbeddingPolicyDocument(
            model=embedding.model_slug,
            provider=embedding.provider_policy,
            dimensions=embedding.dimensions,
        ),
        estimated_microusd_per_thousand_tokens=_embedding_price_per_thousand(db, embedding.model_slug),
    )
    return await vectorizer.embed_query(query)


async def vector_search(
    db: Session,
    context: ToolExecutionContext,
    request: VectorSearchInput,
    *,
    vectorizer: Callable[[str], Awaitable[tuple[float, ...]]] | None = None,
    config: Settings = settings,
) -> VectorSearchOutput:
    workspace = _workspace(db, context)
    if workspace.qdrant_status != "ready":
        return VectorSearchOutput(hits=(), degraded=True)
    run = db.get(AnalysisRun, context.run_id)
    snapshot = db.get(ConfigurationSnapshot, run.configuration_snapshot_id) if run else None
    embedding = (
        db.get(EmbeddingPolicyVersion, snapshot.embedding_policy_version_id)
        if snapshot and snapshot.embedding_policy_version_id
        else None
    )
    if not embedding or not embedding.active_collection:
        return VectorSearchOutput(hits=(), degraded=True)
    query_vector = await (vectorizer(request.query) if vectorizer else _default_query_vector(db, context, request.query))
    scores = query_qdrant_projection(
        workspace.id,
        embedding.active_collection,
        query_vector,
        allowed_node_types=set(request.allowed_node_types),
        limit=request.top_k,
        minimum_similarity=request.minimum_similarity,
        config=config,
    )
    return VectorSearchOutput(
        hits=tuple(
            VectorSearchHit(node_version_id=version_id, score=score)
            for version_id, score in sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))
        )
    )
