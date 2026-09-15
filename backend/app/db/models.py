from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OptimisticLockMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AdminUser(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_nonnegative"),
    )

    identifier: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sessions: Mapped[list["AdminSession"]] = relationship(back_populates="admin")


class AdminSession(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin: Mapped[AdminUser] = relationship(back_populates="sessions")


Index(
    "ix_admin_sessions_active_expiry",
    AdminSession.expires_at,
    postgresql_where=AdminSession.revoked_at.is_(None),
)


class AnonymousSession(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "anonymous_sessions"

    identifier_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    quota_counters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"

    action: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(320))
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    safe_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_audit_events_action_created_at", AuditEvent.action, AuditEvent.created_at)
Index("ix_audit_events_target", AuditEvent.target_type, AuditEvent.target_id)


class DefinitionMixin(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class AgentDefinition(DefinitionMixin, Base):
    __tablename__ = "agent_definitions"
    versions: Mapped[list["AgentVersion"]] = relationship(back_populates="definition")


class WorkflowDefinition(DefinitionMixin, Base):
    __tablename__ = "workflow_definitions"
    versions: Mapped[list["WorkflowVersion"]] = relationship(back_populates="definition")


class ModelPolicy(DefinitionMixin, Base):
    __tablename__ = "model_policies"
    versions: Mapped[list["ModelPolicyVersion"]] = relationship(back_populates="definition")


class EmbeddingPolicy(DefinitionMixin, Base):
    __tablename__ = "embedding_policies"
    versions: Mapped[list["EmbeddingPolicyVersion"]] = relationship(back_populates="definition")


class BudgetPolicy(DefinitionMixin, Base):
    __tablename__ = "budget_policies"
    versions: Mapped[list["BudgetPolicyVersion"]] = relationship(back_populates="definition")


class ToolDefinition(DefinitionMixin, Base):
    __tablename__ = "tool_definitions"
    versions: Mapped[list["ToolVersion"]] = relationship(back_populates="definition")


class VersionMixin(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin):
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelPolicyVersion(VersionMixin, Base):
    __tablename__ = "model_policy_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_policies.id", ondelete="CASCADE"), nullable=False
    )
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    definition: Mapped[ModelPolicy] = relationship(back_populates="versions")


class EmbeddingPolicyVersion(VersionMixin, Base):
    __tablename__ = "embedding_policy_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
        CheckConstraint("dimensions IS NULL OR dimensions > 0", name="dimensions_positive"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_policies.id", ondelete="CASCADE"), nullable=False
    )
    model_slug: Mapped[str | None] = mapped_column(String(300))
    provider_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    dimensions: Mapped[int | None] = mapped_column(Integer)
    chunking_version: Mapped[str] = mapped_column(String(80), nullable=False)
    eligible_node_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    active_collection: Mapped[str | None] = mapped_column(String(200))
    definition: Mapped[EmbeddingPolicy] = relationship(back_populates="versions")


class BudgetPolicyVersion(VersionMixin, Base):
    __tablename__ = "budget_policy_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
        CheckConstraint("public_runs_per_hour >= 0", name="hourly_nonnegative"),
        CheckConstraint("public_runs_per_day >= 0", name="daily_nonnegative"),
        CheckConstraint("public_concurrent_runs >= 0", name="concurrent_nonnegative"),
        CheckConstraint("min_video_count <= default_video_count", name="min_default_order"),
        CheckConstraint("default_video_count <= max_video_count", name="default_max_order"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policies.id", ondelete="CASCADE"), nullable=False
    )
    public_runs_per_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    public_runs_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    public_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    public_run_cost_cap_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    public_daily_cost_cap_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    min_video_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    default_video_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_video_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comments_enabled_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    token_limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    definition: Mapped[BudgetPolicy] = relationship(back_populates="versions")


class ToolVersion(VersionMixin, Base):
    __tablename__ = "tool_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_definitions.id", ondelete="CASCADE"), nullable=False
    )
    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    capability_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    risk_class: Mapped[str] = mapped_column(String(40), nullable=False)
    definition: Mapped[ToolDefinition] = relationship(back_populates="versions")


class AgentVersion(VersionMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    retrieval_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    model_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_policy_versions.id", ondelete="RESTRICT")
    )
    budget_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policy_versions.id", ondelete="RESTRICT")
    )
    definition: Mapped[AgentDefinition] = relationship(back_populates="versions")
    tools: Mapped[list[ToolVersion]] = relationship(secondary="agent_version_tools")


class AgentVersionTool(Base):
    __tablename__ = "agent_version_tools"

    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="CASCADE"), primary_key=True
    )
    tool_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_versions.id", ondelete="RESTRICT"), primary_key=True
    )


class WorkflowVersion(VersionMixin, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False
    )
    dag: Mapped[dict] = mapped_column(JSONB, nullable=False)
    definition: Mapped[WorkflowDefinition] = relationship(back_populates="versions")


class ActiveConfiguration(TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "active_configuration"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    environment: Mapped[str] = mapped_column(String(80), nullable=False)
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_versions.id", ondelete="RESTRICT")
    )
    budget_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policy_versions.id", ondelete="RESTRICT")
    )
    embedding_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_policy_versions.id", ondelete="RESTRICT")
    )
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    public_analysis_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    feature_flags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class ConfigurationSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "configuration_snapshots"

    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    budget_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    embedding_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_policy_versions.id", ondelete="RESTRICT")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_configuration_snapshots_content_hash", ConfigurationSnapshot.content_hash)


class AnalysisRun(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'complete', 'partial', 'failed', 'cancelling', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("progress_sequence >= 0", name="progress_sequence_nonnegative"),
    )

    product_input: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_product: Mapped[str] = mapped_column(String(500), nullable=False)
    initiator_type: Mapped[str] = mapped_column(String(40), nullable=False)
    initiator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    configuration_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("configuration_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    progress_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    warning_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    error_code: Mapped[str | None] = mapped_column(String(120))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_analysis_runs_status_created_at", AnalysisRun.status, AnalysisRun.created_at)
Index(
    "ix_analysis_runs_active",
    AnalysisRun.created_at,
    postgresql_where=AnalysisRun.status.in_(("queued", "running", "cancelling")),
)


class RunBudgetState(TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "run_budget_states"
    __table_args__ = (
        CheckConstraint("max_tokens IS NULL OR max_tokens >= 0", name="max_tokens_nonnegative"),
        CheckConstraint("max_cost_microusd IS NULL OR max_cost_microusd >= 0", name="max_cost_nonnegative"),
        CheckConstraint("reserved_tokens >= 0", name="reserved_tokens_nonnegative"),
        CheckConstraint("consumed_tokens >= 0", name="consumed_tokens_nonnegative"),
        CheckConstraint("reserved_cost_microusd >= 0", name="reserved_cost_nonnegative"),
        CheckConstraint("consumed_cost_microusd >= 0", name="consumed_cost_nonnegative"),
        CheckConstraint("status IN ('active', 'exhausted', 'closed')", name="status_valid"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    budget_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    max_tokens: Mapped[int | None] = mapped_column(BigInteger)
    max_cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    consumed_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    reserved_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    consumed_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")


class TaskRun(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "workflow_task_key", "source_key"),
        CheckConstraint(
            "status IN ('blocked', 'queued', 'running', 'succeeded', 'failed', 'skipped', "
            "'cancelling', 'cancelled', 'timed_out')",
            name="status_valid",
        ),
        CheckConstraint("current_attempt >= 0", name="current_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint("timeout_seconds > 0", name="timeout_seconds_positive"),
        CheckConstraint("weight > 0", name="weight_positive"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_task_key: Mapped[str] = mapped_column(String(120), nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(320))
    executor_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    handler: Mapped[str] = mapped_column(String(160), nullable=False)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="RESTRICT")
    )
    tool_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_versions.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_task_runs_status_created_at", TaskRun.status, TaskRun.created_at)
Index(
    "ix_task_runs_dispatchable",
    TaskRun.priority,
    TaskRun.created_at,
    postgresql_where=TaskRun.status.in_(("queued", "running")),
)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        CheckConstraint("upstream_task_id <> downstream_task_id", name="not_self_referential"),
    )

    upstream_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), primary_key=True
    )
    downstream_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), primary_key=True
    )


class TaskAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_run_id", "attempt_number"),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'timed_out')",
            name="status_valid",
        ),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
    )

    task_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_payload: Mapped[dict | None] = mapped_column(JSONB)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    error_category: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(120))
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    worker_identity: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_task_attempts_status_lease", TaskAttempt.status, TaskAttempt.lease_expires_at)


class ProgressEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "progress_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="SET NULL")
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    public_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    admin_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_progress_events_run_created_at", ProgressEvent.run_id, ProgressEvent.created_at)


class RuntimeOutbox(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "runtime_outbox"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('task.dispatch', 'task.revoke', 'progress.publish')",
            name="kind_valid",
        ),
        CheckConstraint("status IN ('pending', 'processing', 'published')", name="status_valid"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )

    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))


Index(
    "ix_runtime_outbox_pending",
    RuntimeOutbox.next_attempt_at,
    RuntimeOutbox.created_at,
    postgresql_where=RuntimeOutbox.status.in_(("pending", "processing")),
)


VERSION_TABLE_NAMES = (
    "agent_versions",
    "workflow_versions",
    "model_policy_versions",
    "embedding_policy_versions",
    "budget_policy_versions",
    "tool_versions",
)
