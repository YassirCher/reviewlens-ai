from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
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
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_anonymous_sessions_expiry", AnonymousSession.expires_at)


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
        CheckConstraint("public_queue_capacity >= 0", name="queue_capacity_nonnegative"),
        CheckConstraint("min_video_count <= default_video_count", name="min_default_order"),
        CheckConstraint("default_video_count <= max_video_count", name="default_max_order"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policies.id", ondelete="CASCADE"), nullable=False
    )
    public_runs_per_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    public_runs_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    public_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    public_queue_capacity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("20"))
    public_run_cost_cap_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    public_daily_cost_cap_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    min_video_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    default_video_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_video_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comments_enabled_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    token_limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    definition: Mapped[BudgetPolicy] = relationship(back_populates="versions")


class SystemSettingsVersion(VersionMixin, Base):
    __tablename__ = "system_settings_versions"
    __table_args__ = (
        UniqueConstraint("version_number"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
        CheckConstraint("catalog_refresh_minutes BETWEEN 1 AND 1440", name="catalog_interval_valid"),
        CheckConstraint("raw_content_ttl_hours = 24", name="raw_content_ttl_valid"),
    )

    catalog_refresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_content_retention: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_content_ttl_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )


class ToolVersion(VersionMixin, Base):
    __tablename__ = "tool_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number"),
        UniqueConstraint("definition_id", "semantic_version"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="lifecycle_valid"),
        CheckConstraint(
            "semantic_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="semantic_version_valid",
        ),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_definitions.id", ondelete="CASCADE"), nullable=False
    )
    semantic_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0.0", server_default="1.0.0"
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
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    prohibited_behaviors: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    input_schema: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    retrieval_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    generation_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    execution_limits: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evaluation_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
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


class AgentEvaluationResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_evaluation_results"
    __table_args__ = (
        CheckConstraint("status IN ('passed', 'failed')", name="status_valid"),
    )

    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False
    )
    model_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    suite_version: Mapped[str] = mapped_column(String(80), nullable=False)
    suite_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    issue_codes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index(
    "ix_agent_evaluation_results_version_created",
    AgentEvaluationResult.agent_version_id,
    AgentEvaluationResult.created_at,
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
    system_settings_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("system_settings_versions.id", ondelete="RESTRICT")
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
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="RESTRICT", use_alter=True)
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
        CheckConstraint(
            "dependency_mode IN ('all_succeeded', 'all_terminal_min_success')",
            name="dependency_mode_valid",
        ),
        CheckConstraint("minimum_successes >= 0", name="minimum_successes_nonnegative"),
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
    dependency_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, default="all_succeeded", server_default="all_succeeded"
    )
    minimum_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    optional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
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


class TaskRunTool(Base):
    __tablename__ = "task_run_tools"

    task_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), primary_key=True
    )
    tool_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_versions.id", ondelete="RESTRICT"), primary_key=True
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
        CheckConstraint(
            "attempt_kind IN ('primary', 'retry', 'correction')",
            name="attempt_kind_valid",
        ),
    )

    task_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="primary", server_default="primary"
    )
    correction_of_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="RESTRICT")
    )
    context_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_manifests.id", ondelete="RESTRICT", use_alter=True)
    )
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    invalid_output_hash: Mapped[str | None] = mapped_column(String(64))
    validator_results: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
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


class ToolInvocation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'timed_out')",
            name="status_valid",
        ),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="RESTRICT")
    )
    tool_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_versions.id", ondelete="RESTRICT"), nullable=False
    )
    tool_key: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    safe_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running"
    )
    error_category: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(120))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_tool_invocations_attempt_created", ToolInvocation.task_attempt_id, ToolInvocation.created_at)
Index("ix_tool_invocations_status_started", ToolInvocation.status, ToolInvocation.started_at)


class YouTubeQuotaState(TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "youtube_quota_states"
    __table_args__ = (
        CheckConstraint("bucket IN ('search', 'data_api')", name="bucket_valid"),
        CheckConstraint("limit_units >= 0", name="limit_nonnegative"),
        CheckConstraint("reserved_units >= 0", name="reserved_nonnegative"),
        CheckConstraint("consumed_units >= 0", name="consumed_nonnegative"),
        CheckConstraint("reserved_units + consumed_units <= limit_units", name="within_limit"),
    )

    bucket: Mapped[str] = mapped_column(String(32), primary_key=True)
    quota_date: Mapped[date] = mapped_column(Date, primary_key=True)
    limit_units: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consumed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class YouTubeQuotaReservation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "youtube_quota_reservations"
    __table_args__ = (
        UniqueConstraint("tool_invocation_id", "network_attempt"),
        CheckConstraint("network_attempt > 0", name="attempt_positive"),
        CheckConstraint("bucket IN ('search', 'data_api')", name="bucket_valid"),
        CheckConstraint("units > 0", name="units_positive"),
        CheckConstraint("status IN ('reserved', 'consumed', 'released')", name="status_valid"),
    )

    tool_invocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_invocations.id", ondelete="CASCADE"), nullable=False
    )
    network_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    quota_date: Mapped[date] = mapped_column(Date, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="reserved", server_default="reserved"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = __table_args__ + (
        ForeignKeyConstraint(
            ["bucket", "quota_date"],
            ["youtube_quota_states.bucket", "youtube_quota_states.quota_date"],
            ondelete="RESTRICT",
        ),
    )


class OpenRouterCatalogRefresh(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "openrouter_catalog_refreshes"
    __table_args__ = (
        CheckConstraint(
            "catalog_kind IN ('chat_models', 'embedding_models', 'providers', 'model_endpoints')",
            name="catalog_kind_valid",
        ),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_valid"),
        CheckConstraint("item_count >= 0", name="item_count_nonnegative"),
    )

    catalog_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_slug: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", server_default="running")
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_category: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_openrouter_catalog_refreshes_lookup",
    OpenRouterCatalogRefresh.catalog_kind,
    OpenRouterCatalogRefresh.target_slug,
    OpenRouterCatalogRefresh.started_at,
)


class OpenRouterModelSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "openrouter_model_snapshots"
    __table_args__ = (
        UniqueConstraint("refresh_id", "model_kind", "slug"),
        CheckConstraint("model_kind IN ('chat', 'embedding')", name="model_kind_valid"),
        CheckConstraint("context_length IS NULL OR context_length >= 0", name="context_length_nonnegative"),
        CheckConstraint(
            "max_completion_tokens IS NULL OR max_completion_tokens >= 0",
            name="max_completion_tokens_nonnegative",
        ),
    )

    refresh_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("openrouter_catalog_refreshes.id", ondelete="CASCADE"), nullable=False
    )
    model_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    slug: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_slug: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    expiration_date: Mapped[str | None] = mapped_column(String(40))
    context_length: Mapped[int | None] = mapped_column(Integer)
    max_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    input_modalities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    output_modalities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    architecture: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    supported_parameters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    pricing: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    top_provider: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_openrouter_model_snapshots_slug", OpenRouterModelSnapshot.slug, OpenRouterModelSnapshot.fetched_at)
Index("ix_openrouter_model_snapshots_kind", OpenRouterModelSnapshot.model_kind, OpenRouterModelSnapshot.fetched_at)


class OpenRouterProviderSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "openrouter_provider_snapshots"
    __table_args__ = (UniqueConstraint("refresh_id", "slug"),)

    refresh_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("openrouter_catalog_refreshes.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    privacy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str | None] = mapped_column(String(80))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_openrouter_provider_snapshots_slug", OpenRouterProviderSnapshot.slug, OpenRouterProviderSnapshot.fetched_at)


class OpenRouterEndpointSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "openrouter_endpoint_snapshots"
    __table_args__ = (
        UniqueConstraint("refresh_id", "model_slug", "endpoint_key"),
        CheckConstraint("context_length IS NULL OR context_length >= 0", name="context_length_nonnegative"),
        CheckConstraint(
            "max_completion_tokens IS NULL OR max_completion_tokens >= 0",
            name="max_completion_tokens_nonnegative",
        ),
    )

    refresh_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("openrouter_catalog_refreshes.id", ondelete="CASCADE"), nullable=False
    )
    model_slug: Mapped[str] = mapped_column(String(300), nullable=False)
    endpoint_key: Mapped[str] = mapped_column(String(400), nullable=False)
    provider_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(300), nullable=False)
    context_length: Mapped[int | None] = mapped_column(Integer)
    max_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    quantization: Mapped[str | None] = mapped_column(String(80))
    supported_parameters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    pricing: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    performance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    moderation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    privacy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str | None] = mapped_column(String(80))
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_openrouter_endpoint_snapshots_model", OpenRouterEndpointSnapshot.model_slug, OpenRouterEndpointSnapshot.fetched_at)
Index("ix_openrouter_endpoint_snapshots_provider", OpenRouterEndpointSnapshot.provider_slug, OpenRouterEndpointSnapshot.fetched_at)


class OpenRouterAccountState(TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "openrouter_account_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint(
            "status IN ('unknown', 'healthy', 'authentication_blocked', 'payment_blocked', 'unavailable')",
            name="status_valid",
        ),
        CheckConstraint("total_credits_microusd IS NULL OR total_credits_microusd >= 0", name="credits_nonnegative"),
        CheckConstraint("total_usage_microusd IS NULL OR total_usage_microusd >= 0", name="usage_nonnegative"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    environment: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown", server_default="unknown")
    key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    total_credits_microusd: Mapped[int | None] = mapped_column(BigInteger)
    total_usage_microusd: Mapped[int | None] = mapped_column(BigInteger)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(120))


class DailyBudgetState(TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "daily_budget_states"
    __table_args__ = (
        UniqueConstraint("budget_date", "scope"),
        CheckConstraint("scope IN ('public')", name="scope_valid"),
        CheckConstraint("max_cost_microusd >= 0", name="max_cost_nonnegative"),
        CheckConstraint("reserved_cost_microusd >= 0", name="reserved_cost_nonnegative"),
        CheckConstraint("consumed_cost_microusd >= 0", name="consumed_cost_nonnegative"),
    )

    budget_date: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), primary_key=True, default="public", server_default="public")
    budget_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    max_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    consumed_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


class BudgetReservation(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        UniqueConstraint("task_attempt_id", "call_key", "retry_number"),
        CheckConstraint("operation IN ('chat', 'document_embedding', 'query_embedding')", name="operation_valid"),
        CheckConstraint("status IN ('reserved', 'reconciled', 'released')", name="status_valid"),
        CheckConstraint("retry_number > 0", name="retry_number_positive"),
        CheckConstraint("estimated_tokens >= 0", name="estimated_tokens_nonnegative"),
        CheckConstraint("estimated_cost_microusd >= 0", name="estimated_cost_nonnegative"),
        CheckConstraint("actual_tokens IS NULL OR actual_tokens >= 0", name="actual_tokens_nonnegative"),
        CheckConstraint("actual_cost_microusd IS NULL OR actual_cost_microusd >= 0", name="actual_cost_nonnegative"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False)
    task_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False)
    task_attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="CASCADE"), nullable=False)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False)
    model_policy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("model_policy_versions.id", ondelete="RESTRICT"), nullable=False)
    call_key: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    retry_number: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_tokens: Mapped[int | None] = mapped_column(BigInteger)
    actual_cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved", server_default="reserved")
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_budget_reservations_run_status", BudgetReservation.run_id, BudgetReservation.status)


class UsageEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("reservation_id"),
        UniqueConstraint("task_attempt_id", "call_key", "retry_number"),
        CheckConstraint("operation IN ('chat', 'document_embedding', 'query_embedding')", name="operation_valid"),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="status_valid"),
        CheckConstraint(
            "usage_status IN ('pending', 'complete', 'reconciled', 'unreconcilable')",
            name="usage_status_valid",
        ),
        CheckConstraint("retry_number > 0", name="retry_number_positive"),
        CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0", name="prompt_tokens_nonnegative"),
        CheckConstraint("completion_tokens IS NULL OR completion_tokens >= 0", name="completion_tokens_nonnegative"),
        CheckConstraint("reasoning_tokens IS NULL OR reasoning_tokens >= 0", name="reasoning_tokens_nonnegative"),
        CheckConstraint("cached_tokens IS NULL OR cached_tokens >= 0", name="cached_tokens_nonnegative"),
        CheckConstraint("cache_write_tokens IS NULL OR cache_write_tokens >= 0", name="cache_write_tokens_nonnegative"),
        CheckConstraint("audio_tokens IS NULL OR audio_tokens >= 0", name="audio_tokens_nonnegative"),
        CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="total_tokens_nonnegative"),
        CheckConstraint("total_cost_microusd IS NULL OR total_cost_microusd >= 0", name="total_cost_nonnegative"),
        CheckConstraint("upstream_cost_microusd IS NULL OR upstream_cost_microusd >= 0", name="upstream_cost_nonnegative"),
        CheckConstraint("queue_time_ms IS NULL OR queue_time_ms >= 0", name="queue_time_nonnegative"),
        CheckConstraint("time_to_first_token_ms IS NULL OR time_to_first_token_ms >= 0", name="ttft_nonnegative"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"),
        CheckConstraint("reconciliation_attempts >= 0", name="reconciliation_attempts_nonnegative"),
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("budget_reservations.id", ondelete="RESTRICT"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False)
    task_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False)
    task_attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="CASCADE"), nullable=False)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False)
    model_policy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("model_policy_versions.id", ondelete="RESTRICT"), nullable=False)
    embedding_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("embedding_policy_versions.id", ondelete="RESTRICT"))
    call_key: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    retry_number: Mapped[int] = mapped_column(Integer, nullable=False)
    app_request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    generation_id: Mapped[str | None] = mapped_column(String(255))
    requested_models: Mapped[list] = mapped_column(JSONB, nullable=False)
    actual_model: Mapped[str | None] = mapped_column(String(300))
    actual_provider: Mapped[str | None] = mapped_column(String(160))
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int | None] = mapped_column(BigInteger)
    audio_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    upstream_cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    queue_time_ms: Mapped[int | None] = mapped_column(BigInteger)
    time_to_first_token_ms: Mapped[int | None] = mapped_column(BigInteger)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    finish_reason: Mapped[str | None] = mapped_column(String(80))
    service_tier: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    usage_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    error_category: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(120))
    reconciliation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_reconciliation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_usage_events_run_created_at", UsageEvent.run_id, UsageEvent.created_at)
Index("ix_usage_events_dimensions", UsageEvent.model_policy_version_id, UsageEvent.operation, UsageEvent.created_at)
Index(
    "ix_usage_events_pending_reconciliation",
    UsageEvent.next_reconciliation_at,
    postgresql_where=UsageEvent.usage_status == "pending",
)
Index(
    "uq_usage_events_generation_id",
    UsageEvent.generation_id,
    unique=True,
    postgresql_where=UsageEvent.generation_id.is_not(None),
)


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'degraded', 'quarantined', 'deleted')",
            name="status_valid",
        ),
        CheckConstraint(
            "neo4j_status IN ('pending', 'ready', 'degraded', 'rebuilding')",
            name="neo4j_status_valid",
        ),
        CheckConstraint(
            "qdrant_status IN ('pending', 'ready', 'degraded', 'rebuilding')",
            name="qdrant_status_valid",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    root_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    neo4j_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    qdrant_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection_error_code: Mapped[str | None] = mapped_column(String(120))


class ContextNode(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "context_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type IN ('product', 'source', 'transcript', 'transcript_chunk', 'comment_set', "
            "'source_analysis', 'audience_signal', 'evidence', 'claim', 'finding', 'comparison', "
            "'verdict', 'report', 'agent_memory', 'run_summary')",
            name="node_type_valid",
        ),
        CheckConstraint("status IN ('active', 'quarantined', 'deleted')", name="status_valid"),
        UniqueConstraint("current_version_id", name="uq_context_nodes_current_version_id"),
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["context_node_versions.node_id", "context_node_versions.id"],
            name="fk_context_nodes_current_owned_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    node_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_context_nodes_workspace_type", ContextNode.workspace_id, ContextNode.node_type, ContextNode.status)


class ContextNodeVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "context_node_versions"
    __table_args__ = (
        UniqueConstraint("node_id", "version_number", name="uq_context_node_versions_node_version"),
        UniqueConstraint("node_id", "id", name="uq_context_node_versions_node_identity"),
        UniqueConstraint("workspace_id", "body_path", name="uq_context_node_versions_workspace_path"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint(
            "trust_level IN ('primary_source', 'secondary_source', 'derived', 'operational')",
            name="trust_level_valid",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="confidence_range"),
        CheckConstraint("validation_status = 'valid'", name="validation_status_valid"),
        CheckConstraint(
            "public_visibility IN ('public', 'admin', 'private')",
            name="public_visibility_valid",
        ),
    )

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_nodes.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body_path: Mapped[str] = mapped_column(String(800), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    frontmatter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(40), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2000))
    source_language: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="valid", server_default="valid")
    public_visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="admin", server_default="admin")
    created_by_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="RESTRICT")
    )
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_context_node_versions_workspace_created", ContextNodeVersion.workspace_id, ContextNodeVersion.created_at)
Index("ix_context_node_versions_title_search", ContextNodeVersion.title)


class ContextEdge(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "context_edges"
    __table_args__ = (
        CheckConstraint("source_version_id <> target_version_id", name="not_self_referential"),
        CheckConstraint(
            "relation_type IN ('ABOUT', 'DERIVED_FROM', 'CONTAINS', 'SUPPORTS', 'CONTRADICTS', "
            "'AGREES_WITH', 'MENTIONS', 'SUMMARIZES', 'GENERATED_IN', 'EVALUATED_BY', "
            "'USED_AS_CONTEXT', 'NEXT_VERSION_OF')",
            name="relation_type_valid",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="confidence_range"),
        CheckConstraint("status IN ('active', 'revoked')", name="status_valid"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_node_versions.id", ondelete="RESTRICT"), nullable=False
    )
    target_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_node_versions.id", ondelete="RESTRICT"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_by_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="RESTRICT")
    )
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


Index(
    "uq_context_edges_active_idempotency",
    ContextEdge.idempotency_key,
    unique=True,
    postgresql_where=ContextEdge.status == "active",
)
Index("ix_context_edges_source_relation", ContextEdge.source_version_id, ContextEdge.relation_type)
Index("ix_context_edges_target_relation", ContextEdge.target_version_id, ContextEdge.relation_type)


class ContextManifest(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "context_manifests"
    __table_args__ = (
        CheckConstraint(
            "retrieval_mode IN ('hybrid', 'degraded_qdrant', 'degraded_neo4j', 'postgres_only', 'direct')",
            name="retrieval_mode_valid",
        ),
        CheckConstraint("token_budget > 0", name="token_budget_positive"),
        CheckConstraint("estimated_tokens >= 0", name="estimated_tokens_nonnegative"),
        CheckConstraint("actual_tokens IS NULL OR actual_tokens >= 0", name="actual_tokens_nonnegative"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    task_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    retrieval_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_policy_versions.id", ondelete="RESTRICT")
    )
    retrieval_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_budget: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_tokens: Mapped[int | None] = mapped_column(BigInteger)
    rendered_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_context_manifests_attempt_created", ContextManifest.task_attempt_id, ContextManifest.created_at)


class ContextManifestItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "context_manifest_items"
    __table_args__ = (
        UniqueConstraint("manifest_id", "position", name="uq_context_manifest_items_manifest_position"),
        UniqueConstraint("manifest_id", "node_version_id", name="uq_context_manifest_items_manifest_node"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("estimated_tokens >= 0", name="estimated_tokens_nonnegative"),
        CheckConstraint("rendered_start >= 0", name="rendered_start_nonnegative"),
        CheckConstraint("rendered_end >= rendered_start", name="rendered_range_valid"),
    )

    manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_manifests.id", ondelete="CASCADE"), nullable=False
    )
    node_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_node_versions.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    body_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    selection_reason: Mapped[str] = mapped_column(String(120), nullable=False)
    score_components: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    estimated_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rendered_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rendered_end: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ProjectionOutbox(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "projection_outbox"
    __table_args__ = (
        CheckConstraint("target IN ('neo4j', 'qdrant')", name="target_valid"),
        CheckConstraint("operation IN ('upsert', 'delete', 'rebuild')", name="operation_valid"),
        CheckConstraint("status IN ('pending', 'processing', 'published', 'failed')", name="status_valid"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    target: Mapped[str] = mapped_column(String(20), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_projection_outbox_pending",
    ProjectionOutbox.target,
    ProjectionOutbox.next_attempt_at,
    postgresql_where=ProjectionOutbox.status.in_(("pending", "failed")),
)


class Report(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="schema_version_positive"),
        CheckConstraint(
            "audit_status IN ('pass', 'pass_with_warnings', 'fail')",
            name="audit_status_valid",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'revoked')",
            name="status_valid",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    configuration_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("configuration_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    report_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    audit_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_reports_status_created_at", Report.status, Report.created_at)


class RunSubmission(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "run_submissions"
    __table_args__ = (
        UniqueConstraint("actor_type", "actor_id", "idempotency_key", name="uq_run_submissions_actor_key"),
        UniqueConstraint("run_id", name="uq_run_submissions_run"),
        CheckConstraint("actor_type IN ('public', 'admin')", name="actor_type_valid"),
    )

    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_run_submissions_ip_created", RunSubmission.ip_hash, RunSubmission.created_at)


class ReportPublication(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "report_publications"
    __table_args__ = (
        UniqueConstraint("report_id", name="uq_report_publications_report"),
        UniqueConstraint("token_hash", name="uq_report_publications_token_hash"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    graph_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminJob(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "admin_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="status_valid"),
        UniqueConstraint("actor_id", "idempotency_key"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(320))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_admin_jobs_status_created", AdminJob.status, AdminJob.created_at)


class EvaluationBudgetState(TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "evaluation_budget_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    token_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=300_000)
    cost_limit_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=500_000)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    consumed_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    consumed_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class UsageAggregate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_aggregates"
    __table_args__ = (
        UniqueConstraint("granularity", "bucket_start", "dimension", "dimension_key"),
        CheckConstraint("granularity IN ('hour', 'day')", name="granularity_valid"),
    )

    granularity: Mapped[str] = mapped_column(String(8), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dimension: Mapped[str] = mapped_column(String(40), nullable=False)
    dimension_key: Mapped[str] = mapped_column(String(320), nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_usage_aggregates_bucket", UsageAggregate.granularity, UsageAggregate.bucket_start)


class RetainedLLMContent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "retained_llm_content"
    __table_args__ = (UniqueConstraint("usage_event_id"),)

    usage_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usage_events.id", ondelete="CASCADE"), nullable=False
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompatibilityRequest(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "compatibility_requests"
    __table_args__ = (
        UniqueConstraint("request_id"),
        CheckConstraint("transport IN ('sync', 'stream')", name="transport_valid"),
        CheckConstraint(
            "status IN ('accepted', 'complete', 'partial', 'failed', 'cancelled', 'timed_out', "
            "'disconnected', 'rejected', 'mapping_failed')",
            name="status_valid",
        ),
        CheckConstraint("http_status IS NULL OR (http_status >= 100 AND http_status <= 599)", name="http_status_valid"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="SET NULL")
    )
    endpoint: Mapped[str] = mapped_column(String(80), nullable=False)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="accepted")
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    mapped_response: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    error_code: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)


Index("ix_compatibility_requests_started", CompatibilityRequest.started_at)
Index("ix_compatibility_requests_run", CompatibilityRequest.run_id)


class CutoverObservation(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    __tablename__ = "cutover_observations"
    __table_args__ = (
        CheckConstraint("status IN ('observing', 'passed', 'rolled_back')", name="status_valid"),
        CheckConstraint("root_mode IN ('v2', 'v1')", name="root_mode_valid"),
    )

    environment: Mapped[str] = mapped_column(String(80), nullable=False)
    test_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="observing", server_default="observing")
    root_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False)
    latest_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    started_by_admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_cutover_observations_environment_started", CutoverObservation.environment, CutoverObservation.started_at)
Index(
    "uq_cutover_observations_active_environment",
    CutoverObservation.environment,
    unique=True,
    postgresql_where=CutoverObservation.status == "observing",
)


VERSION_TABLE_NAMES = (
    "agent_versions",
    "workflow_versions",
    "model_policy_versions",
    "embedding_policy_versions",
    "budget_policy_versions",
    "tool_versions",
    "system_settings_versions",
)
