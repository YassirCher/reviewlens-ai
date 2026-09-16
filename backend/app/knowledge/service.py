from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import (
    AnalysisRun,
    ContextEdge,
    ContextNode,
    ContextNodeVersion,
    ProjectionOutbox,
    TaskAttempt,
    TaskRun,
    Workspace,
)
from app.knowledge.contracts import (
    IMMUTABLE_NODE_TYPES,
    NodeDraft,
    NodeType,
    RelationDraft,
    RelationType,
    relation_is_allowed,
)
from app.knowledge.storage import (
    MarkdownValidationError,
    atomic_write,
    body_hash,
    canonical_json_hash,
    parse_markdown,
    render_markdown,
    resolve_body_path,
    utc_iso,
    version_relative_path,
    workspace_root,
)


class KnowledgeGraphError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_workspace(db: Session, run_id: uuid.UUID, *, config: Settings = settings) -> Workspace:
    existing = db.scalar(select(Workspace).where(Workspace.run_id == run_id))
    if existing:
        return existing
    if db.get(AnalysisRun, run_id) is None:
        raise KnowledgeGraphError("analysis run does not exist")
    workspace = Workspace(id=uuid.uuid4(), run_id=run_id, root_path="pending")
    workspace.root_path = str(workspace.id)
    root = workspace_root(workspace.id, config)
    for directory in (root / "nodes", root / "temporary", root / "exports"):
        directory.mkdir(parents=True, exist_ok=True)
    db.add(workspace)
    db.flush()
    return workspace


def _validate_creator(db: Session, workspace: Workspace, draft: NodeDraft) -> None:
    if draft.created_by_attempt_id:
        attempt = db.get(TaskAttempt, draft.created_by_attempt_id)
        task = db.get(TaskRun, attempt.task_run_id) if attempt else None
        if not attempt or not task or task.run_id != workspace.run_id:
            raise KnowledgeGraphError("creating task attempt does not belong to the workspace run")
    if draft.created_by_attempt_id and draft.created_by_admin_id:
        raise KnowledgeGraphError("a node cannot have both a task and admin creator")


def _frontmatter(
    workspace: Workspace,
    run_id: uuid.UUID,
    node_id: uuid.UUID,
    version_number: int,
    draft: NodeDraft,
    *,
    timestamp: datetime,
) -> dict:
    return {
        "id": str(node_id),
        "workspace_id": str(workspace.id),
        "run_id": str(run_id),
        "type": draft.node_type.value,
        "title": draft.title,
        "status": "active",
        "version": version_number,
        "source_uri": draft.source_uri,
        "source_language": draft.source_language,
        "trust_level": draft.trust_level.value,
        "confidence": draft.confidence,
        "tags": list(draft.tags),
        "created_at": utc_iso(timestamp),
        "updated_at": utc_iso(timestamp),
        "content_hash": body_hash(draft.body),
    }


def _queue_projection(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    target: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    aggregate_version: int,
    operation: str,
    payload: dict,
) -> None:
    key = f"{target}:{aggregate_type}:{aggregate_id}:{aggregate_version}:{operation}"
    db.add(
        ProjectionOutbox(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            target=target,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            operation=operation,
            payload=payload,
            idempotency_key=key,
        )
    )


def _write_version_file(
    workspace: Workspace,
    node_id: uuid.UUID,
    version_number: int,
    draft: NodeDraft,
    *,
    timestamp: datetime,
    config: Settings,
) -> tuple[str, str, str]:
    metadata = _frontmatter(
        workspace,
        workspace.run_id,
        node_id,
        version_number,
        draft,
        timestamp=timestamp,
    )
    relative_path = version_relative_path(draft.node_type.value, node_id, version_number)
    rendered = render_markdown(metadata, draft.body)
    root = workspace_root(workspace.id, config)
    atomic_write(root, relative_path, rendered)
    return relative_path.as_posix(), metadata["content_hash"], canonical_json_hash(metadata)


def create_node(
    db: Session,
    workspace_id: uuid.UUID,
    draft: NodeDraft,
    *,
    node_id: uuid.UUID | None = None,
    config: Settings = settings,
) -> ContextNodeVersion:
    workspace = db.get(Workspace, workspace_id)
    if not workspace or workspace.status not in {"active", "degraded"}:
        raise KnowledgeGraphError("workspace is not writable")
    _validate_creator(db, workspace, draft)
    stable_id = node_id or uuid.uuid4()
    if db.get(ContextNode, stable_id):
        raise KnowledgeGraphError("context node ID already exists")
    node = ContextNode(
        id=stable_id,
        workspace_id=workspace.id,
        node_type=draft.node_type.value,
        status="active",
        current_version_id=None,
    )
    db.add(node)
    db.flush([node])
    created_at = utc_now()
    path, content_hash, frontmatter_hash = _write_version_file(
        workspace,
        stable_id,
        1,
        draft,
        timestamp=created_at,
        config=config,
    )
    version = ContextNodeVersion(
        id=uuid.uuid4(),
        node_id=stable_id,
        workspace_id=workspace.id,
        version_number=1,
        title=draft.title,
        body_path=path,
        body_hash=content_hash,
        frontmatter_hash=frontmatter_hash,
        trust_level=draft.trust_level.value,
        source_uri=draft.source_uri,
        source_language=draft.source_language,
        confidence=draft.confidence,
        tags=list(draft.tags),
        provenance=draft.provenance,
        public_visibility=draft.public_visibility,
        created_by_attempt_id=draft.created_by_attempt_id,
        created_by_admin_id=draft.created_by_admin_id,
        created_at=created_at,
    )
    db.add(version)
    db.flush([version])
    node.current_version_id = version.id
    for target in ("neo4j", "qdrant"):
        _queue_projection(
            db,
            workspace_id=workspace.id,
            target=target,
            aggregate_type="context_node",
            aggregate_id=node.id,
            aggregate_version=1,
            operation="upsert",
            payload={"node_version_id": str(version.id), "body_hash": version.body_hash},
        )
    db.flush()
    return version


def create_node_version(
    db: Session,
    node_id: uuid.UUID,
    draft: NodeDraft,
    *,
    config: Settings = settings,
) -> ContextNodeVersion:
    node = db.scalar(select(ContextNode).where(ContextNode.id == node_id).with_for_update())
    if not node or node.status != "active" or not node.current_version_id:
        raise KnowledgeGraphError("active context node does not exist")
    node_type = NodeType(node.node_type)
    if node_type in IMMUTABLE_NODE_TYPES:
        raise KnowledgeGraphError(f"{node_type.value} nodes are immutable")
    if draft.node_type is not node_type:
        raise KnowledgeGraphError("a node version cannot change node type")
    workspace = db.get(Workspace, node.workspace_id)
    if not workspace:
        raise KnowledgeGraphError("workspace does not exist")
    _validate_creator(db, workspace, draft)
    previous = db.get(ContextNodeVersion, node.current_version_id)
    if not previous:
        raise KnowledgeGraphError("current node version is missing")
    next_number = previous.version_number + 1
    created_at = utc_now()
    path, content_hash, frontmatter_hash = _write_version_file(
        workspace,
        node.id,
        next_number,
        draft,
        timestamp=created_at,
        config=config,
    )
    version = ContextNodeVersion(
        id=uuid.uuid4(),
        node_id=node.id,
        workspace_id=workspace.id,
        version_number=next_number,
        title=draft.title,
        body_path=path,
        body_hash=content_hash,
        frontmatter_hash=frontmatter_hash,
        trust_level=draft.trust_level.value,
        source_uri=draft.source_uri,
        source_language=draft.source_language,
        confidence=draft.confidence,
        tags=list(draft.tags),
        provenance=draft.provenance,
        public_visibility=draft.public_visibility,
        created_by_attempt_id=draft.created_by_attempt_id,
        created_by_admin_id=draft.created_by_admin_id,
        created_at=created_at,
    )
    db.add(version)
    db.flush([version])
    node.current_version_id = version.id
    lineage_key = canonical_json_hash(
        {"source": str(version.id), "target": str(previous.id), "relation": "NEXT_VERSION_OF"}
    )
    db.add(
        ContextEdge(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            source_version_id=version.id,
            target_version_id=previous.id,
            relation_type=RelationType.NEXT_VERSION_OF.value,
            properties={},
            confidence=100,
            created_by_attempt_id=draft.created_by_attempt_id,
            created_by_admin_id=draft.created_by_admin_id,
            idempotency_key=lineage_key,
        )
    )
    for target in ("neo4j", "qdrant"):
        _queue_projection(
            db,
            workspace_id=workspace.id,
            target=target,
            aggregate_type="context_node",
            aggregate_id=node.id,
            aggregate_version=next_number,
            operation="upsert",
            payload={"node_version_id": str(version.id), "body_hash": version.body_hash},
        )
    _queue_projection(
        db,
        workspace_id=workspace.id,
        target="neo4j",
        aggregate_type="context_edge",
        aggregate_id=version.id,
        aggregate_version=next_number,
        operation="upsert",
        payload={"relation_type": "NEXT_VERSION_OF"},
    )
    db.flush()
    return version


def create_relation(db: Session, workspace_id: uuid.UUID, draft: RelationDraft) -> ContextEdge:
    workspace = db.get(Workspace, workspace_id)
    source = db.get(ContextNodeVersion, draft.source_version_id)
    target = db.get(ContextNodeVersion, draft.target_version_id)
    if not workspace or not source or not target:
        raise KnowledgeGraphError("workspace or relation endpoint does not exist")
    if source.workspace_id != workspace.id or target.workspace_id != workspace.id:
        raise KnowledgeGraphError("relation endpoints must belong to the same workspace")
    source_node = db.get(ContextNode, source.node_id)
    target_node = db.get(ContextNode, target.node_id)
    if not source_node or not target_node:
        raise KnowledgeGraphError("relation endpoint node is missing")
    if not relation_is_allowed(
        NodeType(source_node.node_type), NodeType(target_node.node_type), draft.relation_type
    ):
        raise KnowledgeGraphError(
            f"invalid {draft.relation_type.value} relation: {source_node.node_type} -> {target_node.node_type}"
        )
    if draft.relation_type is not RelationType.NEXT_VERSION_OF and (
        source_node.current_version_id != source.id or target_node.current_version_id != target.id
    ):
        raise KnowledgeGraphError("new relations must use current node versions")
    existing = db.scalar(
        select(ContextEdge).where(
            ContextEdge.idempotency_key == draft.idempotency_key,
            ContextEdge.status == "active",
        )
    )
    if existing:
        if (
            existing.source_version_id,
            existing.target_version_id,
            existing.relation_type,
        ) != (draft.source_version_id, draft.target_version_id, draft.relation_type.value):
            raise KnowledgeGraphError("relation idempotency key conflicts with another edge")
        return existing
    edge = ContextEdge(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        source_version_id=draft.source_version_id,
        target_version_id=draft.target_version_id,
        relation_type=draft.relation_type.value,
        properties=draft.properties,
        confidence=draft.confidence,
        created_by_attempt_id=draft.created_by_attempt_id,
        created_by_admin_id=draft.created_by_admin_id,
        idempotency_key=draft.idempotency_key,
    )
    db.add(edge)
    _queue_projection(
        db,
        workspace_id=workspace.id,
        target="neo4j",
        aggregate_type="context_edge",
        aggregate_id=edge.id,
        aggregate_version=1,
        operation="upsert",
        payload={"relation_type": draft.relation_type.value},
    )
    db.flush()
    return edge


def soft_delete_node(db: Session, node_id: uuid.UUID) -> None:
    node = db.scalar(select(ContextNode).where(ContextNode.id == node_id).with_for_update())
    if not node or node.status == "deleted":
        return
    node.status = "deleted"
    node.deleted_at = utc_now()
    current = db.get(ContextNodeVersion, node.current_version_id) if node.current_version_id else None
    version_number = current.version_number if current else 1
    for target in ("neo4j", "qdrant"):
        _queue_projection(
            db,
            workspace_id=node.workspace_id,
            target=target,
            aggregate_type="context_node",
            aggregate_id=node.id,
            aggregate_version=version_number,
            operation="delete",
            payload={},
        )


def read_version_body(
    workspace: Workspace,
    version: ContextNodeVersion,
    *,
    config: Settings = settings,
) -> tuple[dict, str]:
    root = workspace_root(workspace.id, config)
    path = resolve_body_path(root, version.body_path)
    try:
        metadata, body = parse_markdown(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KnowledgeGraphError("Markdown node body is unavailable") from exc
    if metadata["content_hash"] != version.body_hash:
        raise KnowledgeGraphError("Markdown node body hash does not match PostgreSQL")
    if str(metadata["id"]) != str(version.node_id) or int(metadata["version"]) != version.version_number:
        raise KnowledgeGraphError("Markdown node identity does not match PostgreSQL")
    return metadata, body


def reconcile_workspace(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    config: Settings = settings,
) -> dict[str, int]:
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise KnowledgeGraphError("workspace does not exist")
    root = workspace_root(workspace.id, config)
    versions = list(
        db.scalars(select(ContextNodeVersion).where(ContextNodeVersion.workspace_id == workspace.id))
    )
    expected = {Path(item.body_path).as_posix(): item for item in versions}
    missing = 0
    mismatched = 0
    valid = 0
    for relative, version in expected.items():
        node = db.get(ContextNode, version.node_id)
        try:
            read_version_body(workspace, version, config=config)
        except (KnowledgeGraphError, MarkdownValidationError):
            if node:
                node.status = "quarantined"
            if resolve_body_path(root, relative).exists():
                mismatched += 1
            else:
                missing += 1
        else:
            valid += 1
    orphaned = 0
    nodes_root = root / "nodes"
    quarantine_root = Path(config.node_quarantine_root).resolve() / str(workspace.id)
    for file_path in nodes_root.rglob("*.md") if nodes_root.exists() else ():
        relative = file_path.relative_to(root).as_posix()
        if relative in expected:
            continue
        quarantine_path = (quarantine_root / relative).resolve()
        try:
            quarantine_path.relative_to(quarantine_root)
        except ValueError as exc:
            raise KnowledgeGraphError("quarantine path escaped NODE_QUARANTINE_ROOT") from exc
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(file_path), str(quarantine_path))
        orphaned += 1
    if missing or mismatched:
        workspace.status = "degraded"
        workspace.projection_error_code = "markdown_reconciliation_failed"
    elif workspace.status != "deleted":
        workspace.status = "active"
        workspace.projection_error_code = None
    workspace.last_reconciled_at = utc_now()
    return {"valid": valid, "missing": missing, "mismatched": mismatched, "orphaned": orphaned}


def export_workspace(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    config: Settings = settings,
) -> Path:
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise KnowledgeGraphError("workspace does not exist")
    root = workspace_root(workspace.id, config)
    versions = list(
        db.scalars(
            select(ContextNodeVersion)
            .where(ContextNodeVersion.workspace_id == workspace.id)
            .order_by(ContextNodeVersion.node_id, ContextNodeVersion.version_number)
        )
    )
    edges = list(
        db.scalars(
            select(ContextEdge)
            .where(ContextEdge.workspace_id == workspace.id)
            .order_by(ContextEdge.created_at, ContextEdge.id)
        )
    )
    files: list[tuple[str, bytes]] = []
    hashes: dict[str, str] = {}
    for version in versions:
        path = resolve_body_path(root, version.body_path)
        content = path.read_bytes()
        files.append((version.body_path, content))
        hashes[version.body_path] = version.body_hash
    relations = "".join(
        json.dumps(
            {
                "id": str(edge.id),
                "source_version_id": str(edge.source_version_id),
                "target_version_id": str(edge.target_version_id),
                "relation_type": edge.relation_type,
                "properties": edge.properties,
                "confidence": edge.confidence,
                "status": edge.status,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for edge in edges
    ).encode("utf-8")
    manifest = json.dumps(
        {
            "schema_version": workspace.schema_version,
            "workspace_id": str(workspace.id),
            "run_id": str(workspace.run_id),
            "files": hashes,
            "relations_hash": canonical_json_hash([str(edge.id) for edge in edges]),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    destination = exports / f"{workspace.id}.zip"
    file_descriptor, temporary_name = tempfile.mkstemp(prefix="export-", suffix=".tmp", dir=exports)
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files + [("relations.jsonl", relations), ("manifest.json", manifest)]:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
