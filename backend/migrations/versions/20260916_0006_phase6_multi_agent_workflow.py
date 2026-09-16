"""Phase 6 bounded multi-agent workflow and internal reports.

Revision ID: 20260916_0006
Revises: 20260916_0005
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0006"
down_revision: str | None = "20260916_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "agent_versions",
        sa.Column("purpose", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "agent_versions",
        sa.Column("prohibited_behaviors", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "agent_versions",
        sa.Column("input_schema", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "agent_versions",
        sa.Column("generation_config", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "agent_versions",
        sa.Column("execution_limits", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "agent_versions",
        sa.Column("evaluation_metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )

    op.create_table(
        "agent_evaluation_results",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_version_id", UUID, nullable=False),
        sa.Column("model_policy_version_id", UUID, nullable=False),
        sa.Column("suite_version", sa.String(length=80), nullable=False),
        sa.Column("suite_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("issue_codes", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_agent_evaluation_results_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"], ["agent_versions.id"], ondelete="RESTRICT",
            name="fk_agent_evaluation_agent_version",
        ),
        sa.ForeignKeyConstraint(
            ["model_policy_version_id"], ["model_policy_versions.id"], ondelete="RESTRICT",
            name="fk_agent_evaluation_model_policy_version",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_evaluation_results"),
    )
    op.create_index(
        "ix_agent_evaluation_results_version_created",
        "agent_evaluation_results",
        ["agent_version_id", "created_at"],
    )

    op.add_column(
        "task_runs",
        sa.Column("dependency_mode", sa.String(length=40), server_default="all_succeeded", nullable=False),
    )
    op.add_column(
        "task_runs",
        sa.Column("minimum_successes", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "task_runs",
        sa.Column("optional", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        "ck_task_runs_dependency_mode_valid",
        "task_runs",
        "dependency_mode IN ('all_succeeded', 'all_terminal_min_success')",
    )
    op.create_check_constraint(
        "ck_task_runs_minimum_successes_nonnegative",
        "task_runs",
        "minimum_successes >= 0",
    )
    op.create_table(
        "task_run_tools",
        sa.Column("task_run_id", UUID, nullable=False),
        sa.Column("tool_version_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["task_run_id"], ["task_runs.id"], ondelete="CASCADE",
            name="fk_task_run_tools_task",
        ),
        sa.ForeignKeyConstraint(
            ["tool_version_id"], ["tool_versions.id"], ondelete="RESTRICT",
            name="fk_task_run_tools_tool",
        ),
        sa.PrimaryKeyConstraint("task_run_id", "tool_version_id", name="pk_task_run_tools"),
    )

    op.add_column(
        "task_attempts",
        sa.Column("attempt_kind", sa.String(length=20), server_default="primary", nullable=False),
    )
    op.add_column("task_attempts", sa.Column("correction_of_attempt_id", UUID, nullable=True))
    op.add_column("task_attempts", sa.Column("context_manifest_id", UUID, nullable=True))
    op.add_column("task_attempts", sa.Column("prompt_hash", sa.String(length=64), nullable=True))
    op.add_column("task_attempts", sa.Column("invalid_output_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "task_attempts",
        sa.Column("validator_results", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_check_constraint(
        "ck_task_attempts_attempt_kind_valid",
        "task_attempts",
        "attempt_kind IN ('primary', 'retry', 'correction')",
    )
    op.create_foreign_key(
        "fk_task_attempts_correction_parent",
        "task_attempts",
        "task_attempts",
        ["correction_of_attempt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_task_attempts_context_manifest",
        "task_attempts",
        "context_manifests",
        ["context_manifest_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "reports",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("configuration_snapshot_id", UUID, nullable=False),
        sa.Column("report_node_id", UUID, nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("audit_result", JSONB, nullable=False),
        sa.Column("audit_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="ck_reports_schema_version_positive"),
        sa.CheckConstraint(
            "audit_status IN ('pass', 'pass_with_warnings', 'fail')",
            name="ck_reports_audit_status_valid",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'revoked')",
            name="ck_reports_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], ondelete="CASCADE", name="fk_reports_run"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT", name="fk_reports_workspace"
        ),
        sa.ForeignKeyConstraint(
            ["configuration_snapshot_id"], ["configuration_snapshots.id"], ondelete="RESTRICT",
            name="fk_reports_configuration_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["report_node_id"], ["context_nodes.id"], ondelete="RESTRICT",
            name="fk_reports_report_node",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reports"),
        sa.UniqueConstraint("run_id", name="uq_reports_run_id"),
    )
    op.create_index("ix_reports_status_created_at", "reports", ["status", "created_at"])
    op.add_column("analysis_runs", sa.Column("report_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_analysis_runs_report",
        "analysis_runs",
        "reports",
        ["report_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        CREATE FUNCTION reviewlens_phase6_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'append-only row in % cannot be changed', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_agent_evaluation_results_append_only BEFORE UPDATE OR DELETE "
        "ON agent_evaluation_results FOR EACH ROW EXECUTE FUNCTION reviewlens_phase6_append_only()"
    )
    op.execute(
        """
        CREATE FUNCTION reviewlens_restrict_report_change() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'reports cannot be deleted';
          END IF;
          IF OLD.status IN ('published', 'revoked') THEN
            IF OLD.status = 'published' AND NEW.status = 'revoked'
               AND OLD.id = NEW.id AND OLD.run_id = NEW.run_id
               AND OLD.workspace_id = NEW.workspace_id
               AND OLD.configuration_snapshot_id = NEW.configuration_snapshot_id
               AND OLD.report_node_id = NEW.report_node_id
               AND OLD.schema_version = NEW.schema_version
               AND OLD.payload = NEW.payload AND OLD.content_hash = NEW.content_hash
               AND OLD.audit_result = NEW.audit_result AND OLD.audit_status = NEW.audit_status
               AND OLD.published_at = NEW.published_at AND OLD.created_at = NEW.created_at THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'published reports are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_reports_restricted_update BEFORE UPDATE OR DELETE ON reports "
        "FOR EACH ROW EXECUTE FUNCTION reviewlens_restrict_report_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_reports_restricted_update ON reports")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_restrict_report_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_agent_evaluation_results_append_only ON agent_evaluation_results")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_phase6_append_only()")
    op.drop_constraint("fk_analysis_runs_report", "analysis_runs", type_="foreignkey")
    op.drop_column("analysis_runs", "report_id")
    op.drop_table("reports")
    op.drop_constraint("fk_task_attempts_context_manifest", "task_attempts", type_="foreignkey")
    op.drop_constraint("fk_task_attempts_correction_parent", "task_attempts", type_="foreignkey")
    op.drop_constraint("ck_task_attempts_attempt_kind_valid", "task_attempts", type_="check")
    for column in (
        "validator_results",
        "invalid_output_hash",
        "prompt_hash",
        "context_manifest_id",
        "correction_of_attempt_id",
        "attempt_kind",
    ):
        op.drop_column("task_attempts", column)
    op.drop_table("task_run_tools")
    op.drop_constraint("ck_task_runs_minimum_successes_nonnegative", "task_runs", type_="check")
    op.drop_constraint("ck_task_runs_dependency_mode_valid", "task_runs", type_="check")
    for column in ("optional", "minimum_successes", "dependency_mode"):
        op.drop_column("task_runs", column)
    op.drop_table("agent_evaluation_results")
    for column in (
        "evaluation_metadata",
        "execution_limits",
        "generation_config",
        "input_schema",
        "prohibited_behaviors",
        "purpose",
    ):
        op.drop_column("agent_versions", column)
