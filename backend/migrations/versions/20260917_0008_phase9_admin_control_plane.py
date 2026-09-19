"""Phase 9 admin operations, settings, evaluation budget, and aggregates.

Revision ID: 20260917_0008
Revises: 20260916_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260917_0008"
down_revision: str | None = "20260916_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "system_settings_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("lifecycle", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("catalog_refresh_minutes", sa.Integer(), nullable=False),
        sa.Column("raw_content_retention", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_content_ttl_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("created_by_admin_id", UUID, sa.ForeignKey("admin_users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("version_number"),
        sa.CheckConstraint("version_number > 0", name="ck_system_settings_versions_version_number_positive"),
        sa.CheckConstraint("lifecycle IN ('draft', 'published', 'retired')", name="ck_system_settings_versions_lifecycle_valid"),
        sa.CheckConstraint("catalog_refresh_minutes BETWEEN 1 AND 1440", name="ck_system_settings_versions_catalog_interval_valid"),
        sa.CheckConstraint("raw_content_ttl_hours = 24", name="ck_system_settings_versions_raw_content_ttl_valid"),
    )
    op.execute(
        "CREATE TRIGGER trg_system_settings_versions_immutable BEFORE UPDATE OR DELETE ON system_settings_versions "
        "FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_immutable_change()"
    )
    op.add_column("active_configuration", sa.Column("system_settings_version_id", UUID))
    op.create_foreign_key(
        "fk_active_configuration_system_settings_version",
        "active_configuration", "system_settings_versions", ["system_settings_version_id"], ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "admin_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("actor_id", UUID, sa.ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("target_id", sa.String(320)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("safe_result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(120)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("actor_id", "idempotency_key"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_admin_jobs_status_valid"),
    )
    op.create_index("ix_admin_jobs_status_created", "admin_jobs", ["status", "created_at"])

    op.create_table(
        "evaluation_budget_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("token_limit", sa.BigInteger(), nullable=False, server_default="300000"),
        sa.Column("cost_limit_microusd", sa.BigInteger(), nullable=False, server_default="500000"),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("consumed_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("consumed_cost_microusd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1", name="ck_evaluation_budget_state_singleton"),
        sa.CheckConstraint("token_limit >= 0 AND cost_limit_microusd >= 0", name="ck_evaluation_budget_state_limits_nonnegative"),
        sa.CheckConstraint("reserved_tokens >= 0 AND consumed_tokens >= 0", name="ck_evaluation_budget_state_tokens_nonnegative"),
        sa.CheckConstraint("reserved_cost_microusd >= 0 AND consumed_cost_microusd >= 0", name="ck_evaluation_budget_state_cost_nonnegative"),
    )
    op.execute("INSERT INTO evaluation_budget_state (id) VALUES (1)")

    op.create_table(
        "usage_aggregates",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("granularity", sa.String(8), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dimension", sa.String(40), nullable=False),
        sa.Column("dimension_key", sa.String(320), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_cost_microusd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("granularity", "bucket_start", "dimension", "dimension_key"),
        sa.CheckConstraint("granularity IN ('hour', 'day')", name="ck_usage_aggregates_granularity_valid"),
    )
    op.create_index("ix_usage_aggregates_bucket", "usage_aggregates", ["granularity", "bucket_start"])

    op.create_table(
        "retained_llm_content",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("usage_event_id", UUID, sa.ForeignKey("usage_events.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_retained_llm_content_expires", "retained_llm_content", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_retained_llm_content_expires", table_name="retained_llm_content")
    op.drop_table("retained_llm_content")
    op.drop_index("ix_usage_aggregates_bucket", table_name="usage_aggregates")
    op.drop_table("usage_aggregates")
    op.drop_table("evaluation_budget_state")
    op.drop_index("ix_admin_jobs_status_created", table_name="admin_jobs")
    op.drop_table("admin_jobs")
    op.drop_constraint(
        "fk_active_configuration_system_settings_version",
        "active_configuration", type_="foreignkey",
    )
    op.drop_column("active_configuration", "system_settings_version_id")
    op.execute("DROP TRIGGER IF EXISTS trg_system_settings_versions_immutable ON system_settings_versions")
    op.drop_table("system_settings_versions")
