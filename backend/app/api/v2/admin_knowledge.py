from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.common import StrictModel, not_found, page_rows, require_admin_mutation
from app.admin.jobs import create_job
from app.api.v2.dependencies import get_v2_db, require_admin
from app.db.models import ContextEdge, ContextNode, ContextNodeVersion, Workspace
from app.errors import V2Error
from app.knowledge.contracts import NodeDraft, NodeType, RelationDraft, RelationType, TrustLevel
from app.knowledge.service import (
    KnowledgeGraphError, create_node, create_node_version, create_relation, read_version_body,
)
from app.services.admin_auth import AuthenticatedAdmin
from app.services.audit_service import add_audit_event
from app.worker import execute_admin_job_task

router = APIRouter(prefix="/admin", tags=["admin-knowledge"])


def _workspace(db: Session, workspace_id: uuid.UUID) -> Workspace:
    row = db.get(Workspace, workspace_id)
    if row is None or row.status == "deleted":
        raise not_found("workspace")
    return row


def _node(row: ContextNode, version: ContextNodeVersion | None) -> dict:
    return {"id": str(row.id), "workspace_id": str(row.workspace_id), "node_type": row.node_type,
            "status": row.status, "current_version_id": str(row.current_version_id) if row.current_version_id else None,
            "title": version.title if version else None, "body_hash": version.body_hash if version else None,
            "trust_level": version.trust_level if version else None,
            "confidence": version.confidence if version else None,
            "tags": version.tags if version else [], "version_number": version.version_number if version else None,
            "created_at": row.created_at.isoformat()}


def _edge(row: ContextEdge) -> dict:
    return {"id": str(row.id), "source_version_id": str(row.source_version_id),
            "target_version_id": str(row.target_version_id), "relation_type": row.relation_type,
            "confidence": row.confidence, "status": row.status, "properties": row.properties,
            "created_at": row.created_at.isoformat()}


def _audit_hash(body_hash: str | None) -> str | None:
    """Audit hash columns store the 64-character digest without its algorithm prefix."""
    return body_hash.removeprefix("sha256:") if body_hash else None


@router.get("/workspaces")
def workspaces(cursor: str | None = None, limit: int = Query(default=25, ge=1, le=100),
               db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    page = page_rows(db, Workspace, select(Workspace).where(Workspace.status != "deleted"),
                     cursor=cursor, limit=limit, filters={})
    return {"items": [{"id": str(w.id), "run_id": str(w.run_id), "status": w.status,
                       "neo4j_status": w.neo4j_status, "qdrant_status": w.qdrant_status,
                       "projection_error_code": w.projection_error_code,
                       "created_at": w.created_at.isoformat()} for w in page["rows"]],
            "next_cursor": page["next_cursor"]}


@router.get("/workspaces/{workspace_id}")
def read_workspace(workspace_id: uuid.UUID, db: Session = Depends(get_v2_db),
                   _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    row = _workspace(db, workspace_id)
    return {"id": str(row.id), "run_id": str(row.run_id), "status": row.status,
            "neo4j_status": row.neo4j_status, "qdrant_status": row.qdrant_status,
            "projection_error_code": row.projection_error_code,
            "last_reconciled_at": row.last_reconciled_at.isoformat() if row.last_reconciled_at else None}


@router.get("/workspaces/{workspace_id}/nodes")
def list_nodes(workspace_id: uuid.UUID, node_type: NodeType | None = None,
               q: str | None = Query(default=None, max_length=200), cursor: str | None = None,
               limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_v2_db),
               _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    _workspace(db, workspace_id)
    filters = {"workspace_id": str(workspace_id), "node_type": node_type, "q": q}
    statement = select(ContextNode).where(ContextNode.workspace_id == workspace_id, ContextNode.status != "deleted")
    if node_type:
        statement = statement.where(ContextNode.node_type == node_type.value)
    if q:
        needle = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.join(ContextNodeVersion, ContextNode.current_version_id == ContextNodeVersion.id).where(
            ContextNodeVersion.title.ilike(f"%{needle}%", escape="\\"))
    page = page_rows(db, ContextNode, statement, limit=limit, cursor=cursor, filters=filters)
    return {"items": [_node(row, db.get(ContextNodeVersion, row.current_version_id)) for row in page["rows"]],
            "next_cursor": page["next_cursor"]}


@router.get("/workspaces/{workspace_id}/nodes/{node_id}")
def read_node(workspace_id: uuid.UUID, node_id: uuid.UUID, db: Session = Depends(get_v2_db),
              _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    workspace = _workspace(db, workspace_id)
    node = db.get(ContextNode, node_id)
    if node is None or node.workspace_id != workspace.id:
        raise not_found("node")
    versions = list(db.scalars(select(ContextNodeVersion).where(
        ContextNodeVersion.node_id == node.id, ContextNodeVersion.workspace_id == workspace.id,
    ).order_by(ContextNodeVersion.version_number.desc())))
    details = _node(node, db.get(ContextNodeVersion, node.current_version_id))
    details["versions"] = [{"id": str(v.id), "number": v.version_number, "title": v.title,
                            "body_hash": v.body_hash, "trust_level": v.trust_level,
                            "source_uri": v.source_uri, "source_language": v.source_language,
                            "confidence": v.confidence, "tags": v.tags,
                            "public_visibility": v.public_visibility,
                            "created_at": v.created_at.isoformat()} for v in versions]
    return details


@router.get("/workspaces/{workspace_id}/nodes/{node_id}/versions/{version_id}/body")
def read_node_body(workspace_id: uuid.UUID, node_id: uuid.UUID, version_id: uuid.UUID,
                   db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    workspace = _workspace(db, workspace_id)
    version = db.get(ContextNodeVersion, version_id)
    if not version or version.node_id != node_id or version.workspace_id != workspace.id:
        raise not_found("node version")
    try:
        _, body = read_version_body(workspace, version)
    except KnowledgeGraphError as exc:
        raise V2Error(503, "node_body_unavailable", str(exc)) from exc
    return {"node_id": str(node_id), "version_id": str(version_id), "body": body,
            "body_hash": version.body_hash}


@router.get("/workspaces/{workspace_id}/edges")
def list_edges(workspace_id: uuid.UUID, relation_type: RelationType | None = None,
               cursor: str | None = None, limit: int = Query(default=25, ge=1, le=100),
               db: Session = Depends(get_v2_db), _: AuthenticatedAdmin = Depends(require_admin)) -> dict:
    _workspace(db, workspace_id)
    statement = select(ContextEdge).where(ContextEdge.workspace_id == workspace_id, ContextEdge.status == "active")
    if relation_type:
        statement = statement.where(ContextEdge.relation_type == relation_type.value)
    page = page_rows(db, ContextEdge, statement, cursor=cursor, limit=limit,
                     filters={"workspace_id": str(workspace_id), "relation_type": relation_type})
    return {"items": [_edge(row) for row in page["rows"]], "next_cursor": page["next_cursor"]}


class NodeRequest(StrictModel):
    node_type: NodeType
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=2_000_000)
    trust_level: TrustLevel
    source_uri: str | None = None
    source_language: str | None = None
    confidence: int = Field(default=100, ge=0, le=100)
    tags: tuple[str, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    public_visibility: str = "admin"


@router.post("/workspaces/{workspace_id}/nodes", status_code=201)
def add_node(workspace_id: uuid.UUID, payload: NodeRequest, request: Request,
             db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    _workspace(db, workspace_id)
    try:
        draft = NodeDraft.model_validate(payload.model_dump() | {"created_by_admin_id": admin.admin.id})
        version = create_node(db, workspace_id, draft)
    except KnowledgeGraphError as exc:
        raise V2Error(422, "invalid_node", str(exc)) from exc
    add_audit_event(db, action="knowledge.node_created", actor_type="admin", actor_id=admin.admin.id,
                    target_type="context_node", target_id=str(version.node_id),
                    after_hash=_audit_hash(version.body_hash), request_id=request.state.request_id)
    db.commit()
    return {"node_id": str(version.node_id), "version_id": str(version.id)}


@router.post("/workspaces/{workspace_id}/nodes/{node_id}/versions", status_code=201)
def add_node_version(workspace_id: uuid.UUID, node_id: uuid.UUID, payload: NodeRequest,
                     request: Request, db: Session = Depends(get_v2_db),
                     admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    _workspace(db, workspace_id)
    node = db.get(ContextNode, node_id)
    if node is None or node.workspace_id != workspace_id:
        raise not_found("node")
    prior = db.get(ContextNodeVersion, node.current_version_id)
    try:
        draft = NodeDraft.model_validate(payload.model_dump() | {"created_by_admin_id": admin.admin.id})
        version = create_node_version(db, node_id, draft)
    except KnowledgeGraphError as exc:
        raise V2Error(422, "invalid_node", str(exc)) from exc
    add_audit_event(db, action="knowledge.node_version_created", actor_type="admin", actor_id=admin.admin.id,
                    target_type="context_node", target_id=str(node_id),
                    before_hash=_audit_hash(prior.body_hash if prior else None),
                    after_hash=_audit_hash(version.body_hash),
                    request_id=request.state.request_id)
    db.commit()
    return {"node_id": str(node_id), "version_id": str(version.id)}


class EdgeRequest(StrictModel):
    source_version_id: uuid.UUID
    target_version_id: uuid.UUID
    relation_type: RelationType
    confidence: int = Field(default=100, ge=0, le=100)
    properties: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=16, max_length=64)


@router.post("/workspaces/{workspace_id}/edges", status_code=201)
def add_edge(workspace_id: uuid.UUID, payload: EdgeRequest, request: Request,
             db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    _workspace(db, workspace_id)
    try:
        draft = RelationDraft.model_validate(payload.model_dump() | {"created_by_admin_id": admin.admin.id})
        edge = create_relation(db, workspace_id, draft)
    except KnowledgeGraphError as exc:
        raise V2Error(422, "invalid_relation", str(exc)) from exc
    add_audit_event(db, action="knowledge.edge_created", actor_type="admin", actor_id=admin.admin.id,
                    target_type="context_edge", target_id=str(edge.id), request_id=request.state.request_id)
    db.commit()
    return _edge(edge)


@router.post("/workspaces/{workspace_id}/{action}", status_code=202)
def workspace_job(workspace_id: uuid.UUID, action: str, request: Request,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                  db: Session = Depends(get_v2_db), admin: AuthenticatedAdmin = Depends(require_admin_mutation)) -> dict:
    _workspace(db, workspace_id)
    kind = {"export": "workspace_export", "rebuild-neo4j": "neo4j_rebuild"}.get(action)
    if kind is None:
        raise not_found("workspace action")
    if not idempotency_key:
        raise V2Error(422, "idempotency_key_required", "Idempotency-Key is required.")
    try:
        job = create_job(db, actor_id=admin.admin.id, kind=kind,
                         target_id=str(workspace_id), idempotency_key=idempotency_key)
    except ValueError as exc:
        raise V2Error(409, "idempotency_conflict", str(exc)) from exc
    add_audit_event(db, action=f"knowledge.{action}_requested", actor_type="admin", actor_id=admin.admin.id,
                    target_type="workspace", target_id=str(workspace_id), request_id=request.state.request_id,
                    safe_metadata={"job_id": str(job.id)})
    db.commit()
    try:
        execute_admin_job_task.delay(str(job.id))
    except Exception:
        pass
    return {"job_id": str(job.id), "status": job.status}
