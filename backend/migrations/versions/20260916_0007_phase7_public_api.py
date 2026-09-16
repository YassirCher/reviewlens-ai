"""Phase 7 anonymous admission and immutable public report projection.

Revision ID: 20260916_0007
Revises: 20260916_0006
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0007"
down_revision: str | None = "20260916_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "budget_policy_versions",
        sa.Column("public_queue_capacity", sa.Integer(), server_default="20", nullable=False),
    )
    op.create_check_constraint(
        "ck_budget_policy_versions_queue_capacity_nonnegative",
        "budget_policy_versions",
        "public_queue_capacity >= 0",
    )
    op.add_column("anonymous_sessions", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("anonymous_sessions", sa.Column("absolute_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_anonymous_sessions_expiry", "anonymous_sessions", ["expires_at"])
    op.create_table(
        "run_submissions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("ip_hash", sa.String(64)),
        sa.Column("run_id", UUID, sa.ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("actor_type IN ('public', 'admin')", name="ck_run_submissions_actor_type_valid"),
        sa.UniqueConstraint("actor_type", "actor_id", "idempotency_key", name="uq_run_submissions_actor_key"),
        sa.UniqueConstraint("run_id", name="uq_run_submissions_run"),
    )
    op.create_index("ix_run_submissions_ip_created", "run_submissions", ["ip_hash", "created_at"])
    op.create_table(
        "report_publications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("report_id", UUID, sa.ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("run_id", UUID, sa.ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("graph_payload", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("report_id", name="uq_report_publications_report"),
        sa.UniqueConstraint("token_hash", name="uq_report_publications_token_hash"),
    )
    op.execute(
        """
        CREATE FUNCTION reviewlens_restrict_publication_change() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'public report publication cannot be deleted';
          END IF;
          IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
             AND (to_jsonb(OLD) - 'revoked_at') = (to_jsonb(NEW) - 'revoked_at') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'public report publication is immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_report_publications_immutable BEFORE UPDATE OR DELETE "
        "ON report_publications FOR EACH ROW EXECUTE FUNCTION reviewlens_restrict_publication_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_report_publications_immutable ON report_publications")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_restrict_publication_change()")
    op.drop_table("report_publications")
    op.drop_index("ix_run_submissions_ip_created", "run_submissions")
    op.drop_table("run_submissions")
    op.drop_index("ix_anonymous_sessions_expiry", "anonymous_sessions")
    op.drop_column("anonymous_sessions", "absolute_expires_at")
    op.drop_column("anonymous_sessions", "expires_at")
    op.drop_constraint(
        "ck_budget_policy_versions_queue_capacity_nonnegative",
        "budget_policy_versions",
        type_="check",
    )
    op.drop_column("budget_policy_versions", "public_queue_capacity")
