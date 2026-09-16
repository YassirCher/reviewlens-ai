from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ToolDefinition, ToolVersion
from app.runtime.contracts import canonical_json_hash
from app.tools.contracts import (
    EvidenceValidateInput,
    EvidenceValidateOutput,
    GraphCreateEdgesInput,
    GraphCreateEdgesOutput,
    GraphCreateNodesInput,
    GraphCreateNodesOutput,
    GraphGetNodesInput,
    GraphGetNodesOutput,
    GraphQueryRelationsInput,
    GraphQueryRelationsOutput,
    ScoringPreviewInput,
    ScoringPreviewOutput,
    ToolRisk,
    VectorRequestUpsertInput,
    VectorRequestUpsertOutput,
    VectorSearchInput,
    VectorSearchOutput,
    YouTubeCommentsInput,
    YouTubeCommentsOutput,
    YouTubeSearchInput,
    YouTubeSearchOutput,
    YouTubeTranscriptInput,
    YouTubeTranscriptOutput,
    YouTubeVideoDetailsInput,
    YouTubeVideoDetailsOutput,
)


@dataclass(frozen=True)
class ToolSpec:
    key: str
    name: str
    purpose: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk: ToolRisk
    capabilities: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    timeout_seconds: int
    max_attempts: int
    max_concurrency: int
    max_output_bytes: int
    idempotency: str
    handler: str
    semantic_version: str = "1.0.0"

    def persisted_payload(self) -> dict:
        return {
            "semantic_version": self.semantic_version,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "capability_metadata": {
                "purpose": self.purpose,
                "capabilities": list(self.capabilities),
                "allowed_roles": list(self.allowed_roles),
                "handler": self.handler,
                "fixed_implementation": True,
                "sanitization": "hashes_and_safe_counts_only",
            },
            "limits": {
                "timeout_seconds": self.timeout_seconds,
                "max_attempts": self.max_attempts,
                "max_concurrency": self.max_concurrency,
                "max_output_bytes": self.max_output_bytes,
                "idempotency": self.idempotency,
            },
            "risk_class": self.risk.value,
        }

    @property
    def content_hash(self) -> str:
        return canonical_json_hash(self.persisted_payload())


def _spec(
    key: str,
    name: str,
    purpose: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    risk: ToolRisk,
    capabilities: tuple[str, ...],
    allowed_roles: tuple[str, ...],
    timeout_seconds: int,
    max_attempts: int,
    max_output_bytes: int,
    idempotency: str,
) -> ToolSpec:
    return ToolSpec(
        key=key,
        name=name,
        purpose=purpose,
        input_model=input_model,
        output_model=output_model,
        risk=risk,
        capabilities=capabilities,
        allowed_roles=allowed_roles,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        max_concurrency=(
            1
            if risk == ToolRisk.GRAPH_WRITE
            else 4
            if risk == ToolRisk.NETWORK_READ
            else 8
        ),
        max_output_bytes=max_output_bytes,
        idempotency=idempotency,
        handler=f"tool.{key}",
    )


TOOL_SPECS: tuple[ToolSpec, ...] = (
    _spec("youtube.search", "YouTube search", "Discover bounded YouTube review candidates.", YouTubeSearchInput, YouTubeSearchOutput, ToolRisk.NETWORK_READ, ("network", "read"), ("deterministic", "research_coordinator"), 20, 3, 250_000, "request_hash"),
    _spec("youtube.video_details", "YouTube video details", "Load bounded source metadata in batches.", YouTubeVideoDetailsInput, YouTubeVideoDetailsOutput, ToolRisk.NETWORK_READ, ("network", "read"), ("deterministic", "source_curator"), 20, 3, 1_000_000, "request_hash"),
    _spec("youtube.transcript", "YouTube transcript", "Fetch normalized caption tracks with provenance.", YouTubeTranscriptInput, YouTubeTranscriptOutput, ToolRisk.NETWORK_READ, ("network", "read"), ("deterministic", "review_analyst"), 45, 2, 2_000_000, "video_language"),
    _spec("youtube.comments", "YouTube comments", "Fetch opt-in bounded top-level audience comments.", YouTubeCommentsInput, YouTubeCommentsOutput, ToolRisk.NETWORK_READ, ("network", "read"), ("deterministic", "audience_analyst"), 20, 3, 500_000, "video_sampling_policy"),
    _spec("graph.get_nodes", "Graph node reader", "Read authorized current context nodes.", GraphGetNodesInput, GraphGetNodesOutput, ToolRisk.READ_ONLY, ("graph", "read"), ("deterministic", "research_coordinator", "source_curator", "review_analyst", "audience_analyst", "knowledge_curator", "consensus_analyst", "quality_auditor"), 10, 1, 2_000_000, "request_hash"),
    _spec("graph.query_relations", "Graph relation query", "Traverse authorized PostgreSQL relations.", GraphQueryRelationsInput, GraphQueryRelationsOutput, ToolRisk.READ_ONLY, ("graph", "read"), ("deterministic", "source_curator", "knowledge_curator", "consensus_analyst", "quality_auditor"), 10, 1, 1_000_000, "request_hash"),
    _spec("graph.create_nodes", "Graph node writer", "Create validated context nodes through the storage service.", GraphCreateNodesInput, GraphCreateNodesOutput, ToolRisk.GRAPH_WRITE, ("graph", "write"), ("deterministic", "knowledge_curator"), 30, 1, 250_000, "caller_key"),
    _spec("graph.create_edges", "Graph edge writer", "Create schema-allowed typed relations.", GraphCreateEdgesInput, GraphCreateEdgesOutput, ToolRisk.GRAPH_WRITE, ("graph", "write"), ("deterministic", "knowledge_curator"), 30, 1, 250_000, "edge_keys"),
    _spec("vector.search", "Vector search", "Search the active compatible workspace projection.", VectorSearchInput, VectorSearchOutput, ToolRisk.READ_ONLY, ("vector", "embedding", "read"), ("research_coordinator", "source_curator", "review_analyst", "audience_analyst", "knowledge_curator", "consensus_analyst", "quality_auditor"), 120, 1, 250_000, "request_hash"),
    _spec("vector.request_upsert", "Vector upsert request", "Queue projection work without accepting vectors or collection names.", VectorRequestUpsertInput, VectorRequestUpsertOutput, ToolRisk.GRAPH_WRITE, ("vector", "write"), ("deterministic", "knowledge_curator"), 10, 1, 100_000, "node_version"),
    _spec("evidence.validate", "Evidence validator", "Validate evidence identity, lineage, timestamps, and traceability.", EvidenceValidateInput, EvidenceValidateOutput, ToolRisk.PURE, ("graph", "read", "validation"), ("deterministic", "knowledge_curator", "quality_auditor"), 10, 1, 100_000, "request_hash"),
    _spec("scoring.preview", "Scoring preview", "Calculate deterministic score, verdict, and confidence boundaries.", ScoringPreviewInput, ScoringPreviewOutput, ToolRisk.PURE, ("compute",), ("deterministic", "consensus_analyst", "quality_auditor"), 5, 1, 100_000, "request_hash"),
)

TOOL_SUCCESSOR_SPECS: tuple[ToolSpec, ...] = (
    replace(
        next(item for item in TOOL_SPECS if item.key == "evidence.validate"),
        semantic_version="1.1.0",
        allowed_roles=("deterministic", "review_analyst", "knowledge_curator", "quality_auditor"),
    ),
    replace(
        next(item for item in TOOL_SPECS if item.key == "scoring.preview"),
        semantic_version="1.1.0",
        allowed_roles=("deterministic", "review_analyst", "consensus_analyst", "quality_auditor"),
    ),
)
ALL_TOOL_SPECS = (*TOOL_SPECS, *TOOL_SUCCESSOR_SPECS)
TOOL_REGISTRY = {item.key: item for item in ALL_TOOL_SPECS}
HANDLER_REGISTRY = {item.handler: item.key for item in TOOL_SPECS}


class ToolRegistryConflict(RuntimeError):
    pass


def seed_tool_registry(db: Session) -> dict[str, int]:
    created_definitions = 0
    created_versions = 0
    for spec in ALL_TOOL_SPECS:
        definition = db.scalar(select(ToolDefinition).where(ToolDefinition.key == spec.key))
        if definition is None:
            definition = ToolDefinition(
                key=spec.key,
                name=spec.name,
                description=spec.purpose,
            )
            db.add(definition)
            db.flush()
            created_definitions += 1
        elif definition.name != spec.name or definition.description != spec.purpose:
            raise ToolRegistryConflict(f"tool definition {spec.key} conflicts with checked-in registry")

        version = db.scalar(
            select(ToolVersion).where(
                ToolVersion.definition_id == definition.id,
                ToolVersion.semantic_version == spec.semantic_version,
            )
        )
        payload = spec.persisted_payload()
        if version is None:
            existing_count = len(list(db.scalars(select(ToolVersion.id).where(ToolVersion.definition_id == definition.id))))
            version = ToolVersion(
                definition_id=definition.id,
                version_number=existing_count + 1,
                semantic_version=spec.semantic_version,
                lifecycle="published",
                content_hash=spec.content_hash,
                change_note=(
                    "Phase 5 curated typed tool registry"
                    if spec.semantic_version == "1.0.0"
                    else "Phase 6 compatible role-allowlist successor"
                ),
                published_at=datetime.now(timezone.utc),
                input_schema=payload["input_schema"],
                output_schema=payload["output_schema"],
                capability_metadata=payload["capability_metadata"],
                limits=payload["limits"],
                risk_class=payload["risk_class"],
            )
            db.add(version)
            db.flush()
            created_versions += 1
        elif version.content_hash != spec.content_hash or version.lifecycle != "published":
            raise ToolRegistryConflict(f"tool version {spec.key}@{spec.semantic_version} conflicts with checked-in registry")
    db.flush()
    return {"definitions_created": created_definitions, "versions_created": created_versions}
