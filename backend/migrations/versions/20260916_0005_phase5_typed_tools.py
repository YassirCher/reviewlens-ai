"""Phase 5 typed tools and YouTube quota accounting.

Revision ID: 20260916_0005
Revises: 20260915_0004
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0005"
down_revision: str | None = "20260915_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "tool_versions",
        sa.Column("semantic_version", sa.String(length=32), server_default="1.0.0", nullable=False),
    )
    op.create_check_constraint(
        "ck_tool_versions_semantic_version_valid",
        "tool_versions",
        "semantic_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
    )
    op.create_unique_constraint(
        "uq_tool_versions_definition_semantic_version",
        "tool_versions",
        ["definition_id", "semantic_version"],
    )

    op.create_table(
        "tool_invocations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("task_run_id", UUID, nullable=False),
        sa.Column("task_attempt_id", UUID, nullable=False),
        sa.Column("agent_version_id", UUID, nullable=True),
        sa.Column("tool_version_id", UUID, nullable=False),
        sa.Column("tool_key", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("safe_metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'timed_out')",
            name="ck_tool_invocations_status_valid",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_tool_invocations_retry_count_nonnegative"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_tool_invocations_duration_nonnegative",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_attempt_id"], ["task_attempts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tool_version_id"], ["tool_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_tool_invocations"),
        sa.UniqueConstraint("idempotency_key", name="uq_tool_invocations_idempotency_key"),
    )
    op.create_index(
        "ix_tool_invocations_attempt_created",
        "tool_invocations",
        ["task_attempt_id", "created_at"],
    )
    op.create_index(
        "ix_tool_invocations_status_started",
        "tool_invocations",
        ["status", "started_at"],
    )

    op.create_table(
        "youtube_quota_states",
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("limit_units", sa.Integer(), nullable=False),
        sa.Column("reserved_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consumed_units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("bucket IN ('search', 'data_api')", name="ck_youtube_quota_states_bucket_valid"),
        sa.CheckConstraint("limit_units >= 0", name="ck_youtube_quota_states_limit_nonnegative"),
        sa.CheckConstraint("reserved_units >= 0", name="ck_youtube_quota_states_reserved_nonnegative"),
        sa.CheckConstraint("consumed_units >= 0", name="ck_youtube_quota_states_consumed_nonnegative"),
        sa.CheckConstraint(
            "reserved_units + consumed_units <= limit_units",
            name="ck_youtube_quota_states_within_limit",
        ),
        sa.PrimaryKeyConstraint("bucket", "quota_date", name="pk_youtube_quota_states"),
    )

    op.create_table(
        "youtube_quota_reservations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("tool_invocation_id", UUID, nullable=False),
        sa.Column("network_attempt", sa.Integer(), nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="reserved", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("network_attempt > 0", name="ck_youtube_quota_reservations_attempt_positive"),
        sa.CheckConstraint("bucket IN ('search', 'data_api')", name="ck_youtube_quota_reservations_bucket_valid"),
        sa.CheckConstraint("units > 0", name="ck_youtube_quota_reservations_units_positive"),
        sa.CheckConstraint(
            "status IN ('reserved', 'consumed', 'released')",
            name="ck_youtube_quota_reservations_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tool_invocation_id"], ["tool_invocations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["bucket", "quota_date"],
            ["youtube_quota_states.bucket", "youtube_quota_states.quota_date"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_youtube_quota_reservations"),
        sa.UniqueConstraint(
            "tool_invocation_id",
            "network_attempt",
            name="uq_youtube_quota_reservations_invocation_attempt",
        ),
    )

    op.execute(
        """
        CREATE FUNCTION reviewlens_restrict_tool_invocation_change() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'tool invocations cannot be deleted';
          END IF;
          IF OLD.status <> 'running' THEN
            RAISE EXCEPTION 'terminal tool invocations are immutable';
          END IF;
          IF OLD.id <> NEW.id OR OLD.run_id <> NEW.run_id OR OLD.task_run_id <> NEW.task_run_id
             OR OLD.task_attempt_id <> NEW.task_attempt_id
             OR OLD.agent_version_id IS DISTINCT FROM NEW.agent_version_id
             OR OLD.tool_version_id <> NEW.tool_version_id OR OLD.tool_key <> NEW.tool_key
             OR OLD.idempotency_key <> NEW.idempotency_key OR OLD.input_hash <> NEW.input_hash
             OR OLD.started_at <> NEW.started_at OR OLD.created_at <> NEW.created_at THEN
            RAISE EXCEPTION 'tool invocation identity is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_tool_invocations_restricted_update BEFORE UPDATE OR DELETE "
        "ON tool_invocations FOR EACH ROW EXECUTE FUNCTION reviewlens_restrict_tool_invocation_change()"
    )
    op.execute(
        """
        CREATE FUNCTION reviewlens_restrict_youtube_quota_reservation_change() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'YouTube quota reservations cannot be deleted';
          END IF;
          IF OLD.status <> 'reserved' THEN
            RAISE EXCEPTION 'terminal YouTube quota reservations are immutable';
          END IF;
          IF OLD.id <> NEW.id OR OLD.tool_invocation_id <> NEW.tool_invocation_id
             OR OLD.network_attempt <> NEW.network_attempt OR OLD.bucket <> NEW.bucket
             OR OLD.quota_date <> NEW.quota_date OR OLD.units <> NEW.units
             OR OLD.created_at <> NEW.created_at THEN
            RAISE EXCEPTION 'YouTube quota reservation identity is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_youtube_quota_reservations_restricted_update BEFORE UPDATE OR DELETE "
        "ON youtube_quota_reservations FOR EACH ROW EXECUTE FUNCTION "
        "reviewlens_restrict_youtube_quota_reservation_change()"
    )


def downgrade() -> None:
    # Phase 4 has no semantic-version column. Dropping it after compatible
    # successor versions were published would make re-upgrade assign 1.0.0
    # to every row and silently destroy version identity.
    bind = op.get_bind()
    populated_successors = bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM tool_versions "
        "WHERE semantic_version <> '1.0.0')"
    )).scalar()
    if populated_successors:
        raise RuntimeError(
            "Cannot downgrade Phase 5 while successor tool versions exist; "
            "run migration-cycle tests before seeding or use an empty database"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_youtube_quota_reservations_restricted_update "
        "ON youtube_quota_reservations"
    )
    op.execute("DROP FUNCTION IF EXISTS reviewlens_restrict_youtube_quota_reservation_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_tool_invocations_restricted_update ON tool_invocations")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_restrict_tool_invocation_change()")
    op.drop_table("youtube_quota_reservations")
    op.drop_table("youtube_quota_states")
    op.drop_table("tool_invocations")
    op.drop_constraint(
        "uq_tool_versions_definition_semantic_version", "tool_versions", type_="unique"
    )
    op.drop_constraint("ck_tool_versions_semantic_version_valid", "tool_versions", type_="check")
    op.drop_column("tool_versions", "semantic_version")
