"""Phase 1 platform and persistence foundation.

Revision ID: 20260915_0001
Revises: None
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps_and_lock() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    ]


def _create_definition(table_name: str) -> None:
    op.create_table(
        table_name,
        sa.Column("id", UUID, nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        *_timestamps_and_lock(),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table_name}"),
        sa.UniqueConstraint("key", name=f"uq_{table_name}_key"),
    )


def _version_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("lifecycle", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("change_note", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _version_constraints(table_name: str) -> list[sa.Constraint]:
    return [
        sa.CheckConstraint("version_number > 0", name=f"ck_{table_name}_version_number_positive"),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'published', 'retired')",
            name=f"ck_{table_name}_lifecycle_valid",
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table_name}"),
        sa.UniqueConstraint("definition_id", "version_number", name=f"uq_{table_name}_definition_id"),
    ]


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", UUID, nullable=False),
        sa.Column("identifier", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint("failed_login_count >= 0", name="ck_admin_users_failed_login_count_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_admin_users"),
        sa.UniqueConstraint("identifier", name="uq_admin_users_identifier"),
    )
    op.create_table(
        "anonymous_sessions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("identifier_hash", sa.String(length=64), nullable=False),
        sa.Column("quota_counters", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps_and_lock(),
        sa.PrimaryKeyConstraint("id", name="pk_anonymous_sessions"),
        sa.UniqueConstraint("identifier_hash", name="uq_anonymous_sessions_identifier_hash"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=320), nullable=True),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("safe_metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_action_created_at", "audit_events", ["action", "created_at"])
    op.create_index("ix_audit_events_target", "audit_events", ["target_type", "target_id"])

    for table_name in (
        "agent_definitions",
        "workflow_definitions",
        "model_policies",
        "embedding_policies",
        "budget_policies",
        "tool_definitions",
    ):
        _create_definition(table_name)

    op.create_table(
        "model_policy_versions",
        *_version_columns(),
        sa.Column("definition_id", UUID, nullable=False),
        sa.Column("policy", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["model_policies.id"], ondelete="CASCADE", name="fk_model_policy_version_definition"
        ),
        *_version_constraints("model_policy_versions"),
    )
    op.create_table(
        "embedding_policy_versions",
        *_version_columns(),
        sa.Column("definition_id", UUID, nullable=False),
        sa.Column("model_slug", sa.String(length=300), nullable=True),
        sa.Column("provider_policy", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("chunking_version", sa.String(length=80), nullable=False),
        sa.Column("eligible_node_types", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("active_collection", sa.String(length=200), nullable=True),
        sa.CheckConstraint("dimensions IS NULL OR dimensions > 0", name="ck_embedding_policy_versions_dimensions_positive"),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["embedding_policies.id"], ondelete="CASCADE", name="fk_embedding_policy_version_definition"
        ),
        *_version_constraints("embedding_policy_versions"),
    )
    op.create_table(
        "budget_policy_versions",
        *_version_columns(),
        sa.Column("definition_id", UUID, nullable=False),
        sa.Column("public_runs_per_hour", sa.Integer(), nullable=False),
        sa.Column("public_runs_per_day", sa.Integer(), nullable=False),
        sa.Column("public_concurrent_runs", sa.Integer(), nullable=False),
        sa.Column("public_run_cost_cap_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("public_daily_cost_cap_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("min_video_count", sa.SmallInteger(), nullable=False),
        sa.Column("default_video_count", sa.SmallInteger(), nullable=False),
        sa.Column("max_video_count", sa.SmallInteger(), nullable=False),
        sa.Column("comments_enabled_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("token_limits", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("public_runs_per_hour >= 0", name="ck_budget_policy_versions_hourly_nonnegative"),
        sa.CheckConstraint("public_runs_per_day >= 0", name="ck_budget_policy_versions_daily_nonnegative"),
        sa.CheckConstraint("public_concurrent_runs >= 0", name="ck_budget_policy_versions_concurrent_nonnegative"),
        sa.CheckConstraint("min_video_count <= default_video_count", name="ck_budget_policy_versions_min_default_order"),
        sa.CheckConstraint("default_video_count <= max_video_count", name="ck_budget_policy_versions_default_max_order"),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["budget_policies.id"], ondelete="CASCADE", name="fk_budget_policy_version_definition"
        ),
        *_version_constraints("budget_policy_versions"),
    )
    op.create_table(
        "tool_versions",
        *_version_columns(),
        sa.Column("definition_id", UUID, nullable=False),
        sa.Column("input_schema", JSONB, nullable=False),
        sa.Column("output_schema", JSONB, nullable=False),
        sa.Column("capability_metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("limits", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("risk_class", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["tool_definitions.id"], ondelete="CASCADE", name="fk_tool_version_definition"
        ),
        *_version_constraints("tool_versions"),
    )
    op.create_table(
        "agent_versions",
        *_version_columns(),
        sa.Column("definition_id", UUID, nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("output_schema", JSONB, nullable=False),
        sa.Column("retrieval_policy", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("model_policy_version_id", UUID, nullable=True),
        sa.Column("budget_policy_version_id", UUID, nullable=True),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["agent_definitions.id"], ondelete="CASCADE", name="fk_agent_version_definition"
        ),
        sa.ForeignKeyConstraint(
            ["model_policy_version_id"], ["model_policy_versions.id"], ondelete="RESTRICT", name="fk_agent_version_model_policy"
        ),
        sa.ForeignKeyConstraint(
            ["budget_policy_version_id"], ["budget_policy_versions.id"], ondelete="RESTRICT", name="fk_agent_version_budget_policy"
        ),
        *_version_constraints("agent_versions"),
    )
    op.create_table(
        "agent_version_tools",
        sa.Column("agent_version_id", UUID, nullable=False),
        sa.Column("tool_version_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_version_id"], ["agent_versions.id"], ondelete="CASCADE", name="fk_agent_version_tool_agent"
        ),
        sa.ForeignKeyConstraint(
            ["tool_version_id"], ["tool_versions.id"], ondelete="RESTRICT", name="fk_agent_version_tool_tool"
        ),
        sa.PrimaryKeyConstraint("agent_version_id", "tool_version_id", name="pk_agent_version_tools"),
    )
    op.create_table(
        "workflow_versions",
        *_version_columns(),
        sa.Column("definition_id", UUID, nullable=False),
        sa.Column("dag", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["workflow_definitions.id"], ondelete="CASCADE", name="fk_workflow_version_definition"
        ),
        *_version_constraints("workflow_versions"),
    )
    op.create_table(
        "active_configuration",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("environment", sa.String(length=80), nullable=False),
        sa.Column("workflow_version_id", UUID, nullable=True),
        sa.Column("budget_policy_version_id", UUID, nullable=True),
        sa.Column("embedding_policy_version_id", UUID, nullable=True),
        sa.Column("kill_switch", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("public_analysis_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("feature_flags", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps_and_lock(),
        sa.CheckConstraint("id = 1", name="ck_active_configuration_singleton"),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["workflow_versions.id"], ondelete="RESTRICT", name="fk_active_config_workflow"
        ),
        sa.ForeignKeyConstraint(
            ["budget_policy_version_id"], ["budget_policy_versions.id"], ondelete="RESTRICT", name="fk_active_config_budget"
        ),
        sa.ForeignKeyConstraint(
            ["embedding_policy_version_id"], ["embedding_policy_versions.id"], ondelete="RESTRICT", name="fk_active_config_embedding"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_active_configuration"),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("admin_id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admin_users.id"], ondelete="CASCADE", name="fk_admin_session_admin"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_admin_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_admin_sessions_token_hash"),
    )
    op.create_index(
        "ix_admin_sessions_active_expiry",
        "admin_sessions",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_immutable_change()
        RETURNS trigger AS $$
        BEGIN
          IF TG_TABLE_NAME = 'audit_events' OR OLD.lifecycle = 'published' THEN
            RAISE EXCEPTION 'immutable row in % cannot be changed', TG_TABLE_NAME;
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "agent_versions",
        "workflow_versions",
        "model_policy_versions",
        "embedding_policy_versions",
        "budget_policy_versions",
        "tool_versions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_immutable_change();
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_immutable_change();
        """
    )
    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_published_agent_tool_change()
        RETURNS trigger AS $$
        DECLARE
          parent_id uuid;
        BEGIN
          parent_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.agent_version_id ELSE NEW.agent_version_id END;
          IF EXISTS (
            SELECT 1 FROM agent_versions
            WHERE id = parent_id AND lifecycle = 'published'
          ) THEN
            RAISE EXCEPTION 'tool relations for a published agent version are immutable';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_version_tools_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON agent_version_tools
        FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_published_agent_tool_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_agent_version_tools_immutable ON agent_version_tools")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_published_agent_tool_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    for table_name in (
        "agent_versions",
        "workflow_versions",
        "model_policy_versions",
        "embedding_policy_versions",
        "budget_policy_versions",
        "tool_versions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_immutable_change()")

    for table_name in (
        "admin_sessions",
        "active_configuration",
        "workflow_versions",
        "agent_version_tools",
        "agent_versions",
        "tool_versions",
        "budget_policy_versions",
        "embedding_policy_versions",
        "model_policy_versions",
        "tool_definitions",
        "budget_policies",
        "embedding_policies",
        "model_policies",
        "workflow_definitions",
        "agent_definitions",
        "audit_events",
        "anonymous_sessions",
        "admin_users",
    ):
        op.drop_table(table_name)
