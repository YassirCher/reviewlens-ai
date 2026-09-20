"""Phase 11 compatibility telemetry and cutover observations.

Revision ID: 20260920_0009
Revises: 20260917_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260920_0009"
down_revision: str | None = "20260917_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "compatibility_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("request_id", UUID, nullable=False, unique=True),
        sa.Column("run_id", UUID, sa.ForeignKey("analysis_runs.id", ondelete="SET NULL")),
        sa.Column("endpoint", sa.String(80), nullable=False),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="accepted"),
        sa.Column("http_status", sa.SmallInteger()),
        sa.Column("mapped_response", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(120)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("transport IN ('sync', 'stream')", name="ck_compatibility_requests_transport_valid"),
        sa.CheckConstraint(
            "status IN ('accepted', 'complete', 'partial', 'failed', 'cancelled', 'timed_out', "
            "'disconnected', 'rejected', 'mapping_failed')",
            name="ck_compatibility_requests_status_valid",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="ck_compatibility_requests_http_status_valid",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_compatibility_requests_duration_nonnegative",
        ),
    )
    op.create_index("ix_compatibility_requests_started", "compatibility_requests", ["started_at"])
    op.create_index("ix_compatibility_requests_run", "compatibility_requests", ["run_id"])

    op.create_table(
        "cutover_observations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("environment", sa.String(80), nullable=False),
        sa.Column("test_evidence", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="observing"),
        sa.Column("root_mode", sa.String(8), nullable=False),
        sa.Column("thresholds", JSONB, nullable=False),
        sa.Column("latest_result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_by_admin_id", UUID, sa.ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('observing', 'passed', 'rolled_back')",
            name="ck_cutover_observations_status_valid",
        ),
        sa.CheckConstraint("root_mode IN ('v2', 'v1')", name="ck_cutover_observations_root_mode_valid"),
    )
    op.create_index(
        "ix_cutover_observations_environment_started",
        "cutover_observations",
        ["environment", "started_at"],
    )
    op.create_index(
        "uq_cutover_observations_active_environment",
        "cutover_observations",
        ["environment"],
        unique=True,
        postgresql_where=sa.text("status = 'observing'"),
    )


def downgrade() -> None:
    op.drop_index("uq_cutover_observations_active_environment", table_name="cutover_observations")
    op.drop_index("ix_cutover_observations_environment_started", table_name="cutover_observations")
    op.drop_table("cutover_observations")
    op.drop_index("ix_compatibility_requests_run", table_name="compatibility_requests")
    op.drop_index("ix_compatibility_requests_started", table_name="compatibility_requests")
    op.drop_table("compatibility_requests")
