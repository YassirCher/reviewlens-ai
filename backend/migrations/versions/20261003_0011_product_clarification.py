"""Durable product clarification without rewriting historical run snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261003_0011"
down_revision = "20260921_0010"
branch_labels = None
depends_on = None


def _states(waiting: bool) -> None:
    extra = "'waiting_for_input', " if waiting else ""
    for table, statuses in (
        ("analysis_runs", "'queued', 'running', " + extra + "'complete', 'partial', 'failed', 'cancelling', 'cancelled'"),
        ("task_runs", "'blocked', 'queued', 'running', " + extra + "'succeeded', 'failed', 'skipped', 'cancelling', 'cancelled', 'timed_out'"),
    ):
        # Earlier migrations supplied already-prefixed names to the naming
        # convention. Inspect the existing schema instead of assuming its name.
        existing = [item["name"] for item in sa.inspect(op.get_bind()).get_check_constraints(table)
                    if item["name"].endswith("status_valid") and "status" in item["sqltext"]]
        if len(existing) != 1:
            raise RuntimeError(f"Expected one status constraint on {table}.")
        op.drop_constraint(op.f(existing[0]), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_status_valid"), table, f"status IN ({statuses})")
    op.drop_index("ix_analysis_runs_active", table_name="analysis_runs")
    op.create_index("ix_analysis_runs_active", "analysis_runs", ["created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running', " + extra + "'cancelling')"))


def upgrade() -> None:
    _states(True)
    op.add_column("analysis_runs", sa.Column("resolved_product_name", sa.String(500)))
    op.add_column("analysis_runs", sa.Column("resolved_canonical_product", sa.String(500)))
    op.create_table("product_clarifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.String(500), nullable=False),
        sa.Column("choices", postgresql.JSONB(), nullable=False),
        sa.Column("resolver_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("answer", sa.String(200)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'answered', 'expired', 'cancelled')", name="status_valid"))
    op.create_index("ix_product_clarifications_pending_expiry", "product_clarifications", ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM analysis_runs WHERE status = 'waiting_for_input' LIMIT 1")).first():
        raise RuntimeError("Cancel waiting runs before downgrading clarification support.")
    op.drop_table("product_clarifications")
    op.drop_column("analysis_runs", "resolved_canonical_product")
    op.drop_column("analysis_runs", "resolved_product_name")
    _states(False)
