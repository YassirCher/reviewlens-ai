"""Phase 2 durable run and task execution backbone.

Revision ID: 20260915_0002
Revises: 20260915_0001
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0002"
down_revision: str | None = "20260915_0001"
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


def upgrade() -> None:
    op.create_table(
        "configuration_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workflow_version_id", UUID, nullable=False),
        sa.Column("budget_policy_version_id", UUID, nullable=False),
        sa.Column("embedding_policy_version_id", UUID, nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["workflow_versions.id"], ondelete="RESTRICT",
            name="fk_config_snapshot_workflow",
        ),
        sa.ForeignKeyConstraint(
            ["budget_policy_version_id"], ["budget_policy_versions.id"], ondelete="RESTRICT",
            name="fk_config_snapshot_budget",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_policy_version_id"], ["embedding_policy_versions.id"], ondelete="RESTRICT",
            name="fk_config_snapshot_embedding",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_configuration_snapshots"),
    )
    op.create_index(
        "ix_configuration_snapshots_content_hash", "configuration_snapshots", ["content_hash"]
    )

    op.create_table(
        "analysis_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("product_input", sa.String(length=500), nullable=False),
        sa.Column("canonical_product", sa.String(length=500), nullable=False),
        sa.Column("initiator_type", sa.String(length=40), nullable=False),
        sa.Column("initiator_id", UUID, nullable=True),
        sa.Column("requested_options", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("configuration_snapshot_id", UUID, nullable=False),
        sa.Column("progress_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("coverage", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("warning_summary", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'complete', 'partial', 'failed', 'cancelling', 'cancelled')",
            name="ck_analysis_runs_status_valid",
        ),
        sa.CheckConstraint(
            "progress_sequence >= 0", name="ck_analysis_runs_progress_sequence_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["configuration_snapshot_id"], ["configuration_snapshots.id"], ondelete="RESTRICT",
            name="fk_analysis_run_snapshot",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_runs"),
        sa.UniqueConstraint(
            "configuration_snapshot_id", name="uq_analysis_runs_configuration_snapshot_id"
        ),
    )
    op.create_index("ix_analysis_runs_status_created_at", "analysis_runs", ["status", "created_at"])
    op.create_index(
        "ix_analysis_runs_active",
        "analysis_runs",
        ["created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )

    op.create_table(
        "run_budget_states",
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("budget_policy_version_id", UUID, nullable=False),
        sa.Column("max_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("reserved_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("consumed_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("consumed_cost_microusd", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        *_timestamps_and_lock(),
        sa.CheckConstraint(
            "max_tokens IS NULL OR max_tokens >= 0", name="ck_run_budget_states_max_tokens_nonnegative"
        ),
        sa.CheckConstraint(
            "max_cost_microusd IS NULL OR max_cost_microusd >= 0",
            name="ck_run_budget_states_max_cost_nonnegative",
        ),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_run_budget_states_reserved_tokens_nonnegative"),
        sa.CheckConstraint("consumed_tokens >= 0", name="ck_run_budget_states_consumed_tokens_nonnegative"),
        sa.CheckConstraint(
            "reserved_cost_microusd >= 0", name="ck_run_budget_states_reserved_cost_nonnegative"
        ),
        sa.CheckConstraint(
            "consumed_cost_microusd >= 0", name="ck_run_budget_states_consumed_cost_nonnegative"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'exhausted', 'closed')", name="ck_run_budget_states_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], ondelete="CASCADE",
            name="fk_run_budget_run",
        ),
        sa.ForeignKeyConstraint(
            ["budget_policy_version_id"], ["budget_policy_versions.id"], ondelete="RESTRICT",
            name="fk_run_budget_policy",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_run_budget_states"),
    )

    op.create_table(
        "task_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("workflow_task_key", sa.String(length=120), nullable=False),
        sa.Column("source_key", sa.String(length=320), nullable=True),
        sa.Column("executor_kind", sa.String(length=40), nullable=False),
        sa.Column("handler", sa.String(length=160), nullable=False),
        sa.Column("agent_version_id", UUID, nullable=True),
        sa.Column("tool_version_id", UUID, nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("weight", sa.Integer(), server_default="1", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("retry_policy", JSONB, nullable=False),
        sa.Column("input_payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("current_attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint(
            "status IN ('blocked', 'queued', 'running', 'succeeded', 'failed', 'skipped', "
            "'cancelling', 'cancelled', 'timed_out')",
            name="ck_task_runs_status_valid",
        ),
        sa.CheckConstraint("current_attempt >= 0", name="ck_task_runs_current_attempt_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_task_runs_max_attempts_positive"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_task_runs_timeout_seconds_positive"),
        sa.CheckConstraint("weight > 0", name="ck_task_runs_weight_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], ondelete="CASCADE",
            name="fk_task_run_run",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"], ["agent_versions.id"], ondelete="RESTRICT",
            name="fk_task_run_agent_version",
        ),
        sa.ForeignKeyConstraint(
            ["tool_version_id"], ["tool_versions.id"], ondelete="RESTRICT",
            name="fk_task_run_tool_version",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_runs"),
        sa.UniqueConstraint("idempotency_key", name="uq_task_runs_idempotency_key"),
        sa.UniqueConstraint(
            "run_id", "workflow_task_key", "source_key",
            name="uq_task_runs_run_id_workflow_task_key",
        ),
    )
    op.create_index("ix_task_runs_status_created_at", "task_runs", ["status", "created_at"])
    op.create_index(
        "ix_task_runs_dispatchable",
        "task_runs",
        ["priority", "created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "task_dependencies",
        sa.Column("upstream_task_id", UUID, nullable=False),
        sa.Column("downstream_task_id", UUID, nullable=False),
        sa.CheckConstraint(
            "upstream_task_id <> downstream_task_id", name="ck_task_dependencies_not_self_referential"
        ),
        sa.ForeignKeyConstraint(
            ["upstream_task_id"], ["task_runs.id"], ondelete="CASCADE",
            name="fk_task_dependency_upstream",
        ),
        sa.ForeignKeyConstraint(
            ["downstream_task_id"], ["task_runs.id"], ondelete="CASCADE",
            name="fk_task_dependency_downstream",
        ),
        sa.PrimaryKeyConstraint(
            "upstream_task_id", "downstream_task_id", name="pk_task_dependencies"
        ),
    )

    op.create_table(
        "task_attempts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("task_run_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_payload", JSONB, nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("worker_identity", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_task_attempts_attempt_number_positive"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'timed_out')",
            name="ck_task_attempts_status_valid",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_task_attempts_duration_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["task_run_id"], ["task_runs.id"], ondelete="CASCADE",
            name="fk_task_attempt_task",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_attempts"),
        sa.UniqueConstraint(
            "task_run_id", "attempt_number", name="uq_task_attempts_task_run_id"
        ),
    )
    op.create_index(
        "ix_task_attempts_status_lease", "task_attempts", ["status", "lease_expires_at"]
    )

    op.create_table(
        "progress_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("task_run_id", UUID, nullable=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("public_payload", JSONB, nullable=False),
        sa.Column("admin_metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_progress_events_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], ondelete="CASCADE",
            name="fk_progress_event_run",
        ),
        sa.ForeignKeyConstraint(
            ["task_run_id"], ["task_runs.id"], ondelete="SET NULL",
            name="fk_progress_event_task",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_progress_events"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_progress_events_run_id"),
    )
    op.create_index(
        "ix_progress_events_run_created_at", "progress_events", ["run_id", "created_at"]
    )

    op.create_table(
        "runtime_outbox",
        sa.Column("id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("aggregate_type", sa.String(length=40), nullable=False),
        sa.Column("aggregate_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("task_run_id", UUID, nullable=True),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint(
            "kind IN ('task.dispatch', 'task.revoke', 'progress.publish')",
            name="ck_runtime_outbox_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published')",
            name="ck_runtime_outbox_status_valid",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_runtime_outbox_attempts_nonnegative"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], ondelete="CASCADE",
            name="fk_runtime_outbox_run",
        ),
        sa.ForeignKeyConstraint(
            ["task_run_id"], ["task_runs.id"], ondelete="CASCADE",
            name="fk_runtime_outbox_task",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_runtime_outbox"),
        sa.UniqueConstraint("idempotency_key", name="uq_runtime_outbox_idempotency_key"),
    )
    op.create_index(
        "ix_runtime_outbox_pending",
        "runtime_outbox",
        ["next_attempt_at", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_runtime_immutable_change()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'immutable row in % cannot be changed', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in ("configuration_snapshots", "progress_events"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_runtime_immutable_change();
            """
        )
    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_terminal_attempt_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.status IN ('succeeded', 'failed', 'cancelled', 'timed_out') THEN
            RAISE EXCEPTION 'terminal task attempts are immutable';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_task_attempts_terminal_immutable
        BEFORE UPDATE OR DELETE ON task_attempts
        FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_terminal_attempt_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_task_attempts_terminal_immutable ON task_attempts")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_terminal_attempt_change()")
    for table_name in ("progress_events", "configuration_snapshots"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_runtime_immutable_change()")

    for table_name in (
        "runtime_outbox",
        "progress_events",
        "task_attempts",
        "task_dependencies",
        "task_runs",
        "run_budget_states",
        "analysis_runs",
        "configuration_snapshots",
    ):
        op.drop_table(table_name)
