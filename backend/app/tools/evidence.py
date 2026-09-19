from __future__ import annotations


from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import ContextEdge, ContextNode, ContextNodeVersion
from app.knowledge.service import read_version_body
from app.tools.contracts import EvidenceValidateInput, EvidenceValidateOutput, ToolExecutionContext
from app.tools.errors import ToolAuthorizationError
from app.tools.graph import _workspace


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def validate_evidence(
    db: Session,
    context: ToolExecutionContext,
    request: EvidenceValidateInput,
    *,
    config: Settings = settings,
) -> EvidenceValidateOutput:
    workspace = _workspace(db, context)
    evidence = db.get(ContextNode, request.evidence_node_id)
    source = db.get(ContextNode, request.source_node_id)
    if (
        not evidence
        or not source
        or evidence.workspace_id != workspace.id
        or source.workspace_id != workspace.id
        or evidence.status != "active"
        or source.status != "active"
    ):
        raise ToolAuthorizationError("evidence_nodes_not_authorized")
    errors: list[str] = []
    if evidence.node_type != "evidence":
        errors.append("invalid_evidence_node_type")
    if source.node_type not in {"source", "comment_set"}:
        errors.append("invalid_source_node_type")
    frontier = {evidence.current_version_id} if evidence.current_version_id else set()
    visited = set(frontier)
    for _ in range(4):
        if not frontier:
            break
        edges = list(
            db.scalars(
                select(ContextEdge).where(
                    ContextEdge.workspace_id == workspace.id,
                    ContextEdge.status == "active",
                    ContextEdge.relation_type.in_(("DERIVED_FROM", "CONTAINS")),
                    or_(
                        ContextEdge.source_version_id.in_(frontier),
                        ContextEdge.target_version_id.in_(frontier),
                    ),
                )
            )
        )
        next_frontier = {
            version_id
            for edge in edges
            for version_id in (edge.source_version_id, edge.target_version_id)
            if version_id not in visited
        }
        visited.update(next_frontier)
        frontier = next_frontier
    versions = list(db.scalars(select(ContextNodeVersion).where(ContextNodeVersion.id.in_(visited))))
    lineage_node_ids = {version.node_id for version in versions}
    if source.id not in lineage_node_ids:
        errors.append("source_lineage_missing")

    needle = _normalize(request.evidence_text)
    traceable = False
    max_duration: float | None = None
    for version in versions:
        node = db.get(ContextNode, version.node_id)
        if not node or node.node_type not in {"transcript", "transcript_chunk", "comment_set", "evidence"}:
            continue
        _, body = read_version_body(workspace, version, config=config)
        if needle and needle in _normalize(body):
            traceable = True
        raw_duration = version.provenance.get("duration_seconds")
        if isinstance(raw_duration, (int, float)):
            max_duration = max(max_duration or 0, float(raw_duration))
    if not traceable:
        errors.append("evidence_text_not_traceable")
    if (
        max_duration is not None
        and (
            (request.timestamp_start_seconds is not None and request.timestamp_start_seconds > max_duration)
            or (request.timestamp_end_seconds is not None and request.timestamp_end_seconds > max_duration)
        )
    ):
        errors.append("timestamp_out_of_range")
    if request.central_claim and errors:
        errors.append("central_claim_evidence_invalid")
    return EvidenceValidateOutput(
        valid=not errors,
        error_codes=tuple(dict.fromkeys(errors)),
        lineage_node_ids=tuple(sorted(lineage_node_ids, key=str)),
    )
