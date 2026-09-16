from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NodeType(StrEnum):
    PRODUCT = "product"
    SOURCE = "source"
    TRANSCRIPT = "transcript"
    TRANSCRIPT_CHUNK = "transcript_chunk"
    COMMENT_SET = "comment_set"
    SOURCE_ANALYSIS = "source_analysis"
    AUDIENCE_SIGNAL = "audience_signal"
    EVIDENCE = "evidence"
    CLAIM = "claim"
    FINDING = "finding"
    COMPARISON = "comparison"
    VERDICT = "verdict"
    REPORT = "report"
    AGENT_MEMORY = "agent_memory"
    RUN_SUMMARY = "run_summary"


class TrustLevel(StrEnum):
    PRIMARY = "primary_source"
    SECONDARY = "secondary_source"
    DERIVED = "derived"
    OPERATIONAL = "operational"


class RelationType(StrEnum):
    ABOUT = "ABOUT"
    DERIVED_FROM = "DERIVED_FROM"
    CONTAINS = "CONTAINS"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    AGREES_WITH = "AGREES_WITH"
    MENTIONS = "MENTIONS"
    SUMMARIZES = "SUMMARIZES"
    GENERATED_IN = "GENERATED_IN"
    EVALUATED_BY = "EVALUATED_BY"
    USED_AS_CONTEXT = "USED_AS_CONTEXT"
    NEXT_VERSION_OF = "NEXT_VERSION_OF"


IMMUTABLE_NODE_TYPES = frozenset(
    {
        NodeType.TRANSCRIPT,
        NodeType.TRANSCRIPT_CHUNK,
        NodeType.COMMENT_SET,
        NodeType.SOURCE_ANALYSIS,
        NodeType.AUDIENCE_SIGNAL,
        NodeType.EVIDENCE,
        NodeType.VERDICT,
        NodeType.REPORT,
        NodeType.RUN_SUMMARY,
    }
)


class NodeDraft(StrictModel):
    node_type: NodeType
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=2_000_000)
    trust_level: TrustLevel
    source_uri: str | None = Field(default=None, max_length=2000)
    source_language: str | None = Field(default=None, max_length=40)
    confidence: int = Field(default=100, ge=0, le=100)
    tags: tuple[str, ...] = Field(default=(), max_length=50)
    provenance: dict[str, Any] = Field(default_factory=dict)
    public_visibility: str = Field(default="admin", pattern="^(public|admin|private)$")
    created_by_attempt_id: uuid.UUID | None = None
    created_by_admin_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip().lower() for item in value if item.strip()}))
        if any(len(item) > 80 for item in normalized):
            raise ValueError("tags must not exceed 80 characters")
        return normalized

    @model_validator(mode="after")
    def validate_provenance(self) -> "NodeDraft":
        if self.trust_level is TrustLevel.DERIVED and not self.created_by_attempt_id:
            raise ValueError("derived nodes require a creating task attempt")
        if self.node_type in {NodeType.SOURCE, NodeType.TRANSCRIPT, NodeType.TRANSCRIPT_CHUNK, NodeType.EVIDENCE}:
            if not self.source_uri:
                raise ValueError(f"{self.node_type.value} nodes require source_uri")
        return self


class RelationDraft(StrictModel):
    source_version_id: uuid.UUID
    target_version_id: uuid.UUID
    relation_type: RelationType
    confidence: int = Field(default=100, ge=0, le=100)
    properties: dict[str, Any] = Field(default_factory=dict)
    created_by_attempt_id: uuid.UUID | None = None
    created_by_admin_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=16, max_length=64, pattern=r"^[a-f0-9]+$")

    @model_validator(mode="after")
    def reject_self_edge(self) -> "RelationDraft":
        if self.source_version_id == self.target_version_id:
            raise ValueError("context relations cannot be self-referential")
        return self


class RetrievalPolicy(StrictModel):
    allowed_node_types: frozenset[NodeType]
    allowed_trust_levels: frozenset[TrustLevel]
    required_seed_node_types: frozenset[NodeType] = frozenset()
    allowed_relation_types: frozenset[RelationType] = frozenset(RelationType)
    relation_direction: str = Field(default="both", pattern="^(outgoing|incoming|both)$")
    maximum_graph_hops: int = Field(default=1, ge=0, le=2)
    vector_top_k: int = Field(default=12, ge=0, le=100)
    minimum_similarity: float = Field(default=0.25, ge=-1, le=1)
    lexical_candidate_limit: int = Field(default=20, ge=0, le=200)
    maximum_nodes_per_source: int = Field(default=4, ge=1, le=50)
    input_token_budget: int = Field(ge=64, le=1_000_000)
    reserved_output_tokens: int = Field(default=0, ge=0)
    allow_operational_memory: bool = False

    @model_validator(mode="after")
    def validate_memory_policy(self) -> "RetrievalPolicy":
        if NodeType.AGENT_MEMORY in self.allowed_node_types and not self.allow_operational_memory:
            raise ValueError("agent_memory requires allow_operational_memory=true")
        return self


class RetrievalRequest(StrictModel):
    workspace_id: uuid.UUID
    task_attempt_id: uuid.UUID
    query: str = Field(min_length=1, max_length=20_000)
    seed_node_ids: tuple[uuid.UUID, ...] = ()
    policy: RetrievalPolicy
    embedding_policy_version_id: uuid.UUID | None = None


class PacketItem(StrictModel):
    node_version_id: uuid.UUID
    node_id: uuid.UUID
    node_type: NodeType
    body_hash: str
    selection_reason: str
    estimated_tokens: int


class ContextPacket(StrictModel):
    manifest_id: uuid.UUID
    retrieval_mode: str
    rendered: str
    estimated_tokens: int
    items: tuple[PacketItem, ...]


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def relation_is_allowed(source: NodeType, target: NodeType, relation: RelationType) -> bool:
    if relation is RelationType.NEXT_VERSION_OF:
        return source is target
    if relation is RelationType.ABOUT:
        return target is NodeType.PRODUCT and source is not NodeType.PRODUCT
    if relation is RelationType.CONTAINS:
        return source in {NodeType.TRANSCRIPT, NodeType.COMMENT_SET, NodeType.REPORT} and target in {
            NodeType.TRANSCRIPT_CHUNK,
            NodeType.EVIDENCE,
            NodeType.CLAIM,
            NodeType.FINDING,
        }
    if relation in {RelationType.SUPPORTS, RelationType.CONTRADICTS, RelationType.AGREES_WITH}:
        return source in {NodeType.EVIDENCE, NodeType.CLAIM, NodeType.FINDING} and target in {
            NodeType.CLAIM,
            NodeType.FINDING,
            NodeType.COMPARISON,
            NodeType.VERDICT,
        }
    if relation is RelationType.DERIVED_FROM:
        return source in {
            NodeType.TRANSCRIPT,
            NodeType.TRANSCRIPT_CHUNK,
            NodeType.COMMENT_SET,
            NodeType.SOURCE_ANALYSIS,
            NodeType.AUDIENCE_SIGNAL,
            NodeType.EVIDENCE,
            NodeType.CLAIM,
            NodeType.FINDING,
            NodeType.COMPARISON,
            NodeType.VERDICT,
            NodeType.REPORT,
            NodeType.RUN_SUMMARY,
        } and target not in {NodeType.AGENT_MEMORY, NodeType.RUN_SUMMARY}
    if relation is RelationType.SUMMARIZES:
        return source in {NodeType.SOURCE_ANALYSIS, NodeType.FINDING, NodeType.COMPARISON, NodeType.RUN_SUMMARY} \
            and target is not NodeType.PRODUCT
    if relation in {RelationType.GENERATED_IN, RelationType.EVALUATED_BY, RelationType.USED_AS_CONTEXT}:
        return target is NodeType.RUN_SUMMARY
    return relation is RelationType.MENTIONS
