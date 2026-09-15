"""Phase 3 OpenRouter gateway and LLMOps accounting core.

Revision ID: 20260915_0003
Revises: 20260915_0002
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0003"
down_revision: str | None = "20260915_0002"
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
        "openrouter_catalog_refreshes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("catalog_kind", sa.String(length=40), nullable=False),
        sa.Column("target_slug", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "catalog_kind IN ('chat_models', 'embedding_models', 'providers', 'model_endpoints')",
            name="ck_openrouter_catalog_refreshes_catalog_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_openrouter_catalog_refreshes_status_valid",
        ),
        sa.CheckConstraint("item_count >= 0", name="ck_openrouter_catalog_refreshes_item_count_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_openrouter_catalog_refreshes"),
    )
    op.create_index(
        "ix_openrouter_catalog_refreshes_lookup",
        "openrouter_catalog_refreshes",
        ["catalog_kind", "target_slug", "started_at"],
    )

    op.create_table(
        "openrouter_model_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("refresh_id", UUID, nullable=False),
        sa.Column("model_kind", sa.String(length=20), nullable=False),
        sa.Column("slug", sa.String(length=300), nullable=False),
        sa.Column("canonical_slug", sa.String(length=300), nullable=False),
        sa.Column("author", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_timestamp", sa.BigInteger(), nullable=True),
        sa.Column("expiration_date", sa.String(length=40), nullable=True),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("max_completion_tokens", sa.Integer(), nullable=True),
        sa.Column("input_modalities", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("output_modalities", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("architecture", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("supported_parameters", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("pricing", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("top_provider", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("model_kind IN ('chat', 'embedding')", name="ck_openrouter_model_snapshots_model_kind_valid"),
        sa.CheckConstraint("context_length IS NULL OR context_length >= 0", name="ck_openrouter_model_snapshots_context_length_nonnegative"),
        sa.CheckConstraint("max_completion_tokens IS NULL OR max_completion_tokens >= 0", name="ck_openrouter_model_snapshots_max_completion_tokens_nonnegative"),
        sa.ForeignKeyConstraint(["refresh_id"], ["openrouter_catalog_refreshes.id"], ondelete="CASCADE", name="fk_openrouter_model_snapshots_refresh"),
        sa.PrimaryKeyConstraint("id", name="pk_openrouter_model_snapshots"),
        sa.UniqueConstraint("refresh_id", "model_kind", "slug", name="uq_openrouter_model_snapshots_refresh_model_slug"),
    )
    op.create_index("ix_openrouter_model_snapshots_slug", "openrouter_model_snapshots", ["slug", "fetched_at"])
    op.create_index("ix_openrouter_model_snapshots_kind", "openrouter_model_snapshots", ["model_kind", "fetched_at"])

    op.create_table(
        "openrouter_provider_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("refresh_id", UUID, nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("privacy", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=True),
        sa.Column("metadata_json", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["refresh_id"], ["openrouter_catalog_refreshes.id"], ondelete="CASCADE", name="fk_openrouter_provider_snapshots_refresh"),
        sa.PrimaryKeyConstraint("id", name="pk_openrouter_provider_snapshots"),
        sa.UniqueConstraint("refresh_id", "slug", name="uq_openrouter_provider_snapshots_refresh_slug"),
    )
    op.create_index("ix_openrouter_provider_snapshots_slug", "openrouter_provider_snapshots", ["slug", "fetched_at"])

    op.create_table(
        "openrouter_endpoint_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("refresh_id", UUID, nullable=False),
        sa.Column("model_slug", sa.String(length=300), nullable=False),
        sa.Column("endpoint_key", sa.String(length=400), nullable=False),
        sa.Column("provider_slug", sa.String(length=160), nullable=False),
        sa.Column("provider_name", sa.String(length=300), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("max_completion_tokens", sa.Integer(), nullable=True),
        sa.Column("quantization", sa.String(length=80), nullable=True),
        sa.Column("supported_parameters", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("pricing", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("performance", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("moderation", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("privacy", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=True),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("context_length IS NULL OR context_length >= 0", name="ck_openrouter_endpoint_snapshots_context_length_nonnegative"),
        sa.CheckConstraint("max_completion_tokens IS NULL OR max_completion_tokens >= 0", name="ck_openrouter_endpoint_snapshots_max_completion_tokens_nonnegative"),
        sa.ForeignKeyConstraint(["refresh_id"], ["openrouter_catalog_refreshes.id"], ondelete="CASCADE", name="fk_openrouter_endpoint_snapshots_refresh"),
        sa.PrimaryKeyConstraint("id", name="pk_openrouter_endpoint_snapshots"),
        sa.UniqueConstraint("refresh_id", "model_slug", "endpoint_key", name="uq_openrouter_endpoint_snapshots_refresh_model_endpoint"),
    )
    op.create_index("ix_openrouter_endpoint_snapshots_model", "openrouter_endpoint_snapshots", ["model_slug", "fetched_at"])
    op.create_index("ix_openrouter_endpoint_snapshots_provider", "openrouter_endpoint_snapshots", ["provider_slug", "fetched_at"])

    op.create_table(
        "openrouter_account_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("environment", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="unknown", nullable=False),
        sa.Column("key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("total_credits_microusd", sa.BigInteger(), nullable=True),
        sa.Column("total_usage_microusd", sa.BigInteger(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint("id = 1", name="ck_openrouter_account_state_singleton"),
        sa.CheckConstraint("status IN ('unknown', 'healthy', 'authentication_blocked', 'payment_blocked', 'unavailable')", name="ck_openrouter_account_state_status_valid"),
        sa.CheckConstraint("total_credits_microusd IS NULL OR total_credits_microusd >= 0", name="ck_openrouter_account_state_credits_nonnegative"),
        sa.CheckConstraint("total_usage_microusd IS NULL OR total_usage_microusd >= 0", name="ck_openrouter_account_state_usage_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_openrouter_account_state"),
    )

    op.create_table(
        "daily_budget_states",
        sa.Column("budget_date", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=20), server_default="public", nullable=False),
        sa.Column("budget_policy_version_id", UUID, nullable=False),
        sa.Column("max_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("consumed_cost_microusd", sa.BigInteger(), server_default="0", nullable=False),
        *_timestamps_and_lock(),
        sa.CheckConstraint("scope IN ('public')", name="ck_daily_budget_states_scope_valid"),
        sa.CheckConstraint("max_cost_microusd >= 0", name="ck_daily_budget_states_max_cost_nonnegative"),
        sa.CheckConstraint("reserved_cost_microusd >= 0", name="ck_daily_budget_states_reserved_cost_nonnegative"),
        sa.CheckConstraint("consumed_cost_microusd >= 0", name="ck_daily_budget_states_consumed_cost_nonnegative"),
        sa.ForeignKeyConstraint(["budget_policy_version_id"], ["budget_policy_versions.id"], ondelete="RESTRICT", name="fk_daily_budget_states_budget_policy"),
        sa.PrimaryKeyConstraint("budget_date", "scope", name="pk_daily_budget_states"),
        sa.UniqueConstraint("budget_date", "scope", name="uq_daily_budget_states_date_scope"),
    )

    op.create_table(
        "budget_reservations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("task_run_id", UUID, nullable=False),
        sa.Column("task_attempt_id", UUID, nullable=False),
        sa.Column("agent_version_id", UUID, nullable=False),
        sa.Column("model_policy_version_id", UUID, nullable=False),
        sa.Column("call_key", sa.String(length=120), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("retry_number", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("actual_tokens", sa.BigInteger(), nullable=True),
        sa.Column("actual_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="reserved", nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint("operation IN ('chat', 'document_embedding', 'query_embedding')", name="ck_budget_reservations_operation_valid"),
        sa.CheckConstraint("status IN ('reserved', 'reconciled', 'released')", name="ck_budget_reservations_status_valid"),
        sa.CheckConstraint("retry_number > 0", name="ck_budget_reservations_retry_number_positive"),
        sa.CheckConstraint("estimated_tokens >= 0", name="ck_budget_reservations_estimated_tokens_nonnegative"),
        sa.CheckConstraint("estimated_cost_microusd >= 0", name="ck_budget_reservations_estimated_cost_nonnegative"),
        sa.CheckConstraint("actual_tokens IS NULL OR actual_tokens >= 0", name="ck_budget_reservations_actual_tokens_nonnegative"),
        sa.CheckConstraint("actual_cost_microusd IS NULL OR actual_cost_microusd >= 0", name="ck_budget_reservations_actual_cost_nonnegative"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="CASCADE", name="fk_budget_reservations_run"),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE", name="fk_budget_reservations_task"),
        sa.ForeignKeyConstraint(["task_attempt_id"], ["task_attempts.id"], ondelete="CASCADE", name="fk_budget_reservations_attempt"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="RESTRICT", name="fk_budget_reservations_agent"),
        sa.ForeignKeyConstraint(["model_policy_version_id"], ["model_policy_versions.id"], ondelete="RESTRICT", name="fk_budget_reservations_model_policy"),
        sa.PrimaryKeyConstraint("id", name="pk_budget_reservations"),
        sa.UniqueConstraint("task_attempt_id", "call_key", "retry_number", name="uq_budget_reservations_attempt_call_retry"),
    )
    op.create_index("ix_budget_reservations_run_status", "budget_reservations", ["run_id", "status"])

    op.create_table(
        "usage_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("reservation_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("task_run_id", UUID, nullable=False),
        sa.Column("task_attempt_id", UUID, nullable=False),
        sa.Column("agent_version_id", UUID, nullable=False),
        sa.Column("workflow_version_id", UUID, nullable=False),
        sa.Column("model_policy_version_id", UUID, nullable=False),
        sa.Column("embedding_policy_version_id", UUID, nullable=True),
        sa.Column("call_key", sa.String(length=120), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("retry_number", sa.Integer(), nullable=False),
        sa.Column("app_request_id", sa.String(length=64), nullable=False),
        sa.Column("generation_id", sa.String(length=255), nullable=True),
        sa.Column("requested_models", JSONB, nullable=False),
        sa.Column("actual_model", sa.String(length=300), nullable=True),
        sa.Column("actual_provider", sa.String(length=160), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=True),
        sa.Column("audio_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("upstream_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("queue_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("time_to_first_token_ms", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("finish_reason", sa.String(length=80), nullable=True),
        sa.Column("service_tier", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("usage_status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("reconciliation_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_reconciliation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("operation IN ('chat', 'document_embedding', 'query_embedding')", name="ck_usage_events_operation_valid"),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_usage_events_status_valid"),
        sa.CheckConstraint("usage_status IN ('pending', 'complete', 'reconciled', 'unreconcilable')", name="ck_usage_events_usage_status_valid"),
        sa.CheckConstraint("retry_number > 0", name="ck_usage_events_retry_number_positive"),
        sa.CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0", name="ck_usage_events_prompt_tokens_nonnegative"),
        sa.CheckConstraint("completion_tokens IS NULL OR completion_tokens >= 0", name="ck_usage_events_completion_tokens_nonnegative"),
        sa.CheckConstraint("reasoning_tokens IS NULL OR reasoning_tokens >= 0", name="ck_usage_events_reasoning_tokens_nonnegative"),
        sa.CheckConstraint("cached_tokens IS NULL OR cached_tokens >= 0", name="ck_usage_events_cached_tokens_nonnegative"),
        sa.CheckConstraint("cache_write_tokens IS NULL OR cache_write_tokens >= 0", name="ck_usage_events_cache_write_tokens_nonnegative"),
        sa.CheckConstraint("audio_tokens IS NULL OR audio_tokens >= 0", name="ck_usage_events_audio_tokens_nonnegative"),
        sa.CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="ck_usage_events_total_tokens_nonnegative"),
        sa.CheckConstraint("total_cost_microusd IS NULL OR total_cost_microusd >= 0", name="ck_usage_events_total_cost_nonnegative"),
        sa.CheckConstraint("upstream_cost_microusd IS NULL OR upstream_cost_microusd >= 0", name="ck_usage_events_upstream_cost_nonnegative"),
        sa.CheckConstraint("queue_time_ms IS NULL OR queue_time_ms >= 0", name="ck_usage_events_queue_time_nonnegative"),
        sa.CheckConstraint("time_to_first_token_ms IS NULL OR time_to_first_token_ms >= 0", name="ck_usage_events_ttft_nonnegative"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_usage_events_latency_nonnegative"),
        sa.CheckConstraint("reconciliation_attempts >= 0", name="ck_usage_events_reconciliation_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["reservation_id"], ["budget_reservations.id"], ondelete="RESTRICT", name="fk_usage_events_reservation"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="CASCADE", name="fk_usage_events_run"),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE", name="fk_usage_events_task"),
        sa.ForeignKeyConstraint(["task_attempt_id"], ["task_attempts.id"], ondelete="CASCADE", name="fk_usage_events_attempt"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="RESTRICT", name="fk_usage_events_agent"),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"], ondelete="RESTRICT", name="fk_usage_events_workflow"),
        sa.ForeignKeyConstraint(["model_policy_version_id"], ["model_policy_versions.id"], ondelete="RESTRICT", name="fk_usage_events_model_policy"),
        sa.ForeignKeyConstraint(["embedding_policy_version_id"], ["embedding_policy_versions.id"], ondelete="RESTRICT", name="fk_usage_events_embedding_policy"),
        sa.PrimaryKeyConstraint("id", name="pk_usage_events"),
        sa.UniqueConstraint("reservation_id", name="uq_usage_events_reservation_id"),
        sa.UniqueConstraint("task_attempt_id", "call_key", "retry_number", name="uq_usage_events_attempt_call_retry"),
        sa.UniqueConstraint("app_request_id", name="uq_usage_events_app_request_id"),
    )
    op.create_index("ix_usage_events_run_created_at", "usage_events", ["run_id", "created_at"])
    op.create_index("ix_usage_events_dimensions", "usage_events", ["model_policy_version_id", "operation", "created_at"])
    op.create_index("ix_usage_events_pending_reconciliation", "usage_events", ["next_reconciliation_at"], postgresql_where=sa.text("usage_status = 'pending'"))
    op.create_index("uq_usage_events_generation_id", "usage_events", ["generation_id"], unique=True, postgresql_where=sa.text("generation_id IS NOT NULL"))

    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_llmops_snapshot_change()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'immutable row in % cannot be changed', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "openrouter_model_snapshots",
        "openrouter_provider_snapshots",
        "openrouter_endpoint_snapshots",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_llmops_snapshot_change()"
        )
    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_terminal_catalog_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.status IN ('succeeded', 'failed') THEN
            RAISE EXCEPTION 'terminal catalog refreshes are immutable';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_openrouter_catalog_refreshes_terminal_immutable "
        "BEFORE UPDATE OR DELETE ON openrouter_catalog_refreshes FOR EACH ROW "
        "EXECUTE FUNCTION reviewlens_prevent_terminal_catalog_change()"
    )
    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_terminal_reservation_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.status IN ('reconciled', 'released') THEN
            RAISE EXCEPTION 'terminal budget reservations are immutable';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_budget_reservations_terminal_immutable BEFORE UPDATE OR DELETE "
        "ON budget_reservations FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_terminal_reservation_change()"
    )
    op.execute(
        """
        CREATE FUNCTION reviewlens_restrict_usage_event_change()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'usage events are append-only';
          END IF;
          IF OLD.usage_status <> 'pending' THEN
            RAISE EXCEPTION 'final usage events are immutable';
          END IF;
          IF (OLD.reservation_id, OLD.run_id, OLD.task_run_id, OLD.task_attempt_id,
              OLD.agent_version_id, OLD.workflow_version_id, OLD.model_policy_version_id,
              OLD.embedding_policy_version_id, OLD.call_key, OLD.operation, OLD.retry_number,
              OLD.app_request_id, OLD.requested_models)
             IS DISTINCT FROM
             (NEW.reservation_id, NEW.run_id, NEW.task_run_id, NEW.task_attempt_id,
              NEW.agent_version_id, NEW.workflow_version_id, NEW.model_policy_version_id,
              NEW.embedding_policy_version_id, NEW.call_key, NEW.operation, NEW.retry_number,
              NEW.app_request_id, NEW.requested_models) THEN
            RAISE EXCEPTION 'usage attribution is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_usage_events_restricted_update BEFORE UPDATE OR DELETE ON usage_events "
        "FOR EACH ROW EXECUTE FUNCTION reviewlens_restrict_usage_event_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_usage_events_restricted_update ON usage_events")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_restrict_usage_event_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_budget_reservations_terminal_immutable ON budget_reservations")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_terminal_reservation_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_openrouter_catalog_refreshes_terminal_immutable ON openrouter_catalog_refreshes")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_terminal_catalog_change()")
    for table_name in (
        "openrouter_endpoint_snapshots",
        "openrouter_provider_snapshots",
        "openrouter_model_snapshots",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_llmops_snapshot_change()")
    for table_name in (
        "usage_events",
        "budget_reservations",
        "daily_budget_states",
        "openrouter_account_state",
        "openrouter_endpoint_snapshots",
        "openrouter_provider_snapshots",
        "openrouter_model_snapshots",
        "openrouter_catalog_refreshes",
    ):
        op.drop_table(table_name)
