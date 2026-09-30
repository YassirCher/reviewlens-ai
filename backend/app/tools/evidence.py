from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import ContextEdge, ContextNode, ContextNodeVersion
from app.knowledge.service import read_version_body
from app.tools.contracts import EvidenceValidateInput, EvidenceValidateOutput, ToolExecutionContext
from app.tools.errors import ToolAuthorizationError
from app.tools.graph import _workspace


_SEGMENT = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]\s*(.+)$")
_COMMENT = re.compile(r"^- \[[^\]]+\] likes=\d+ published=.*?: (.+)$")


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _speech_words(value: str) -> list[str]:
    """Ignore caption punctuation while preserving every spoken word and number."""
    return re.findall(r"\w+", value.casefold())


def transcript_excerpt_matches(
    body: str,
    excerpt: str,
    start_seconds: float | None,
    end_seconds: float | None,
    *,
    minimum_words: int = 3,
) -> bool:
    """Match spoken words near their timestamp within a bounded caption span."""
    if start_seconds is None:
        return False
    needle = _speech_words(excerpt)
    if len(needle) < minimum_words:
        return False
    words: list[tuple[str, float, float]] = []
    for line in body.splitlines():
        match = _SEGMENT.match(line)
        if match:
            start, end = float(match[1]), float(match[2])
            words.extend((word, start, end) for word in _speech_words(match[3]))
    for index, (word, start, end) in enumerate(words):
        if word != needle[0] or not start - 5 <= start_seconds <= max(start, end) + 5:
            continue
        tail = words[index : index + len(needle)]
        if len(tail) != len(needle) or [item[0] for item in tail] != needle:
            continue
        last_start, last_end = tail[-1][1:]
        # Caption times describe whole segments. An analyst may cite the start
        # of the last quoted segment rather than its end, so accept any end
        # inside the matched speech span while still rejecting distant times.
        if 0 <= last_end - start <= 45 and (
            end_seconds is None or last_start - 5 <= end_seconds <= last_end + 5
        ):
            return True
    return False


def comment_excerpt_matches(body: str, excerpt: str) -> bool:
    needle = _normalize(excerpt)
    return bool(needle) and any(
        needle in _normalize(match[1])
        for line in body.splitlines()
        if (match := _COMMENT.match(line))
    )


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
        not evidence or not source
        or evidence.workspace_id != workspace.id or source.workspace_id != workspace.id
        or evidence.status != "active" or source.status != "active"
    ):
        raise ToolAuthorizationError("evidence_nodes_not_authorized")
    errors: list[str] = []
    if evidence.node_type != "evidence":
        errors.append("invalid_evidence_node_type")
    if source.node_type not in {"source", "comment_set"}:
        errors.append("invalid_source_node_type")

    # DERIVED_FROM points from the derived node toward its original material.
    # Traversing CONTAINS or reversing an edge can reach a different video.
    frontier = {evidence.current_version_id} if evidence.current_version_id else set()
    visited = set(frontier)
    for _ in range(3):
        if not frontier:
            break
        edges = db.scalars(
            select(ContextEdge).where(
                ContextEdge.workspace_id == workspace.id,
                ContextEdge.status == "active",
                ContextEdge.relation_type == "DERIVED_FROM",
                ContextEdge.source_version_id.in_(frontier),
            )
        )
        frontier = {edge.target_version_id for edge in edges if edge.target_version_id not in visited}
        visited.update(frontier)
    versions = list(db.scalars(select(ContextNodeVersion).where(ContextNodeVersion.id.in_(visited))))
    lineage_node_ids = {version.node_id for version in versions}
    if source.id not in lineage_node_ids:
        errors.append("source_lineage_missing")

    source_version = db.get(ContextNodeVersion, source.current_version_id) if source.current_version_id else None
    expected_video_id = source_version.provenance.get("video_id") if source_version else None
    if source.node_type == "source" and not expected_video_id:
        errors.append("source_video_identity_missing")
    traceable = False
    for version in versions:
        node = db.get(ContextNode, version.node_id)
        if not node or node.id == evidence.id:
            continue
        if source.node_type == "source":
            if not expected_video_id or node.node_type != "transcript" or version.provenance.get("video_id") != expected_video_id:
                continue
            _, body = read_version_body(workspace, version, config=config)
            traceable = transcript_excerpt_matches(
                body, request.evidence_text,
                request.timestamp_start_seconds, request.timestamp_end_seconds,
            )
        elif node.id == source.id and node.node_type == "comment_set":
            _, body = read_version_body(workspace, version, config=config)
            traceable = request.timestamp_start_seconds is None and comment_excerpt_matches(body, request.evidence_text)
        if traceable:
            break
    if not traceable:
        errors.append("evidence_text_or_timestamp_not_traceable")
    if request.central_claim and errors:
        errors.append("central_claim_evidence_invalid")
    return EvidenceValidateOutput(
        valid=not errors,
        error_codes=tuple(dict.fromkeys(errors)),
        lineage_node_ids=tuple(sorted(lineage_node_ids, key=str)),
    )
