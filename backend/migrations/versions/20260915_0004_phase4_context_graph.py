"""Phase 4 reconstructable context graph and retrieval.

Revision ID: 20260915_0004
Revises: 20260915_0003
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0004"
down_revision: str | None = "20260915_0003"
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
        "workspaces",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("root_path", sa.String(length=500), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("neo4j_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("qdrant_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projection_error_code", sa.String(length=120), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint(
            "status IN ('active', 'degraded', 'quarantined', 'deleted')",
            name="ck_workspaces_status_valid",
        ),
        sa.CheckConstraint(
            "neo4j_status IN ('pending', 'ready', 'degraded', 'rebuilding')",
            name="ck_workspaces_neo4j_status_valid",
        ),
        sa.CheckConstraint(
            "qdrant_status IN ('pending', 'ready', 'degraded', 'rebuilding')",
            name="ck_workspaces_qdrant_status_valid",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="CASCADE", name="fk_workspaces_run"),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("run_id", name="uq_workspaces_run_id"),
        sa.UniqueConstraint("root_path", name="uq_workspaces_root_path"),
    )

    op.create_table(
        "context_nodes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("current_version_id", UUID, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint(
            "node_type IN ('product', 'source', 'transcript', 'transcript_chunk', 'comment_set', "
            "'source_analysis', 'audience_signal', 'evidence', 'claim', 'finding', 'comparison', "
            "'verdict', 'report', 'agent_memory', 'run_summary')",
            name="ck_context_nodes_node_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'quarantined', 'deleted')",
            name="ck_context_nodes_status_valid",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE", name="fk_context_nodes_workspace"),
        sa.PrimaryKeyConstraint("id", name="pk_context_nodes"),
        sa.UniqueConstraint("current_version_id", name="uq_context_nodes_current_version_id"),
    )
    op.create_index(
        "ix_context_nodes_workspace_type",
        "context_nodes",
        ["workspace_id", "node_type", "status"],
    )

    op.create_table(
        "context_node_versions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("node_id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body_path", sa.String(length=800), nullable=False),
        sa.Column("body_hash", sa.String(length=71), nullable=False),
        sa.Column("frontmatter_hash", sa.String(length=64), nullable=False),
        sa.Column("trust_level", sa.String(length=40), nullable=False),
        sa.Column("source_uri", sa.String(length=2000), nullable=True),
        sa.Column("source_language", sa.String(length=40), nullable=True),
        sa.Column("confidence", sa.SmallInteger(), nullable=False),
        sa.Column("tags", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("provenance", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("validation_status", sa.String(length=20), server_default="valid", nullable=False),
        sa.Column("public_visibility", sa.String(length=20), server_default="admin", nullable=False),
        sa.Column("created_by_attempt_id", UUID, nullable=True),
        sa.Column("created_by_admin_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version_number > 0", name="ck_context_node_versions_version_number_positive"),
        sa.CheckConstraint(
            "trust_level IN ('primary_source', 'secondary_source', 'derived', 'operational')",
            name="ck_context_node_versions_trust_level_valid",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_context_node_versions_confidence_range",
        ),
        sa.CheckConstraint(
            "validation_status = 'valid'",
            name="ck_context_node_versions_validation_status_valid",
        ),
        sa.CheckConstraint(
            "public_visibility IN ('public', 'admin', 'private')",
            name="ck_context_node_versions_public_visibility_valid",
        ),
        sa.ForeignKeyConstraint(["node_id"], ["context_nodes.id"], ondelete="CASCADE", name="fk_context_node_versions_node"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE", name="fk_context_node_versions_workspace"),
        sa.ForeignKeyConstraint(["created_by_attempt_id"], ["task_attempts.id"], ondelete="RESTRICT", name="fk_context_node_versions_attempt"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT", name="fk_context_node_versions_admin"),
        sa.PrimaryKeyConstraint("id", name="pk_context_node_versions"),
        sa.UniqueConstraint("node_id", "version_number", name="uq_context_node_versions_node_version"),
        sa.UniqueConstraint("node_id", "id", name="uq_context_node_versions_node_identity"),
        sa.UniqueConstraint("workspace_id", "body_path", name="uq_context_node_versions_workspace_path"),
    )
    op.create_index(
        "ix_context_node_versions_workspace_created",
        "context_node_versions",
        ["workspace_id", "created_at"],
    )
    op.create_index("ix_context_node_versions_title_search", "context_node_versions", ["title"])
    op.execute(
        "CREATE INDEX ix_context_node_versions_lexical ON context_node_versions USING gin "
        "(to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(tags::text, '')))"
    )
    op.create_foreign_key(
        "fk_context_nodes_current_owned_version",
        "context_nodes",
        "context_node_versions",
        ["id", "current_version_id"],
        ["node_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "context_edges",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("source_version_id", UUID, nullable=False),
        sa.Column("target_version_id", UUID, nullable=False),
        sa.Column("relation_type", sa.String(length=40), nullable=False),
        sa.Column("properties", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("confidence", sa.SmallInteger(), nullable=False),
        sa.Column("created_by_attempt_id", UUID, nullable=True),
        sa.Column("created_by_admin_id", UUID, nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        *_timestamps_and_lock(),
        sa.CheckConstraint("source_version_id <> target_version_id", name="ck_context_edges_not_self_referential"),
        sa.CheckConstraint(
            "relation_type IN ('ABOUT', 'DERIVED_FROM', 'CONTAINS', 'SUPPORTS', 'CONTRADICTS', "
            "'AGREES_WITH', 'MENTIONS', 'SUMMARIZES', 'GENERATED_IN', 'EVALUATED_BY', "
            "'USED_AS_CONTEXT', 'NEXT_VERSION_OF')",
            name="ck_context_edges_relation_type_valid",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_context_edges_confidence_range"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_context_edges_status_valid"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE", name="fk_context_edges_workspace"),
        sa.ForeignKeyConstraint(["source_version_id"], ["context_node_versions.id"], ondelete="RESTRICT", name="fk_context_edges_source"),
        sa.ForeignKeyConstraint(["target_version_id"], ["context_node_versions.id"], ondelete="RESTRICT", name="fk_context_edges_target"),
        sa.ForeignKeyConstraint(["created_by_attempt_id"], ["task_attempts.id"], ondelete="RESTRICT", name="fk_context_edges_attempt"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="RESTRICT", name="fk_context_edges_admin"),
        sa.PrimaryKeyConstraint("id", name="pk_context_edges"),
    )
    op.create_index(
        "uq_context_edges_active_idempotency",
        "context_edges",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_context_edges_source_relation", "context_edges", ["source_version_id", "relation_type"])
    op.create_index("ix_context_edges_target_relation", "context_edges", ["target_version_id", "relation_type"])

    op.create_table(
        "context_manifests",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("task_attempt_id", UUID, nullable=False),
        sa.Column("retrieval_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_policy_version_id", UUID, nullable=True),
        sa.Column("retrieval_mode", sa.String(length=32), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("token_budget", sa.BigInteger(), nullable=False),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=False),
        sa.Column("actual_tokens", sa.BigInteger(), nullable=True),
        sa.Column("rendered_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "retrieval_mode IN ('hybrid', 'degraded_qdrant', 'degraded_neo4j', 'postgres_only', 'direct')",
            name="ck_context_manifests_retrieval_mode_valid",
        ),
        sa.CheckConstraint("token_budget > 0", name="ck_context_manifests_token_budget_positive"),
        sa.CheckConstraint("estimated_tokens >= 0", name="ck_context_manifests_estimated_tokens_nonnegative"),
        sa.CheckConstraint("actual_tokens IS NULL OR actual_tokens >= 0", name="ck_context_manifests_actual_tokens_nonnegative"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE", name="fk_context_manifests_workspace"),
        sa.ForeignKeyConstraint(["task_attempt_id"], ["task_attempts.id"], ondelete="RESTRICT", name="fk_context_manifests_attempt"),
        sa.ForeignKeyConstraint(["embedding_policy_version_id"], ["embedding_policy_versions.id"], ondelete="RESTRICT", name="fk_context_manifests_embedding_policy"),
        sa.PrimaryKeyConstraint("id", name="pk_context_manifests"),
    )
    op.create_index("ix_context_manifests_attempt_created", "context_manifests", ["task_attempt_id", "created_at"])

    op.create_table(
        "context_manifest_items",
        sa.Column("id", UUID, nullable=False),
        sa.Column("manifest_id", UUID, nullable=False),
        sa.Column("node_version_id", UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("body_hash", sa.String(length=71), nullable=False),
        sa.Column("selection_reason", sa.String(length=120), nullable=False),
        sa.Column("score_components", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=False),
        sa.Column("rendered_start", sa.BigInteger(), nullable=False),
        sa.Column("rendered_end", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_context_manifest_items_position_nonnegative"),
        sa.CheckConstraint("estimated_tokens >= 0", name="ck_context_manifest_items_estimated_tokens_nonnegative"),
        sa.CheckConstraint("rendered_start >= 0", name="ck_context_manifest_items_rendered_start_nonnegative"),
        sa.CheckConstraint("rendered_end >= rendered_start", name="ck_context_manifest_items_rendered_range_valid"),
        sa.ForeignKeyConstraint(["manifest_id"], ["context_manifests.id"], ondelete="CASCADE", name="fk_context_manifest_items_manifest"),
        sa.ForeignKeyConstraint(["node_version_id"], ["context_node_versions.id"], ondelete="RESTRICT", name="fk_context_manifest_items_node_version"),
        sa.PrimaryKeyConstraint("id", name="pk_context_manifest_items"),
        sa.UniqueConstraint("manifest_id", "position", name="uq_context_manifest_items_manifest_position"),
        sa.UniqueConstraint("manifest_id", "node_version_id", name="uq_context_manifest_items_manifest_node"),
    )

    op.create_table(
        "projection_outbox",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("target", sa.String(length=20), nullable=False),
        sa.Column("aggregate_type", sa.String(length=40), nullable=False),
        sa.Column("aggregate_id", UUID, nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps_and_lock(),
        sa.CheckConstraint("target IN ('neo4j', 'qdrant')", name="ck_projection_outbox_target_valid"),
        sa.CheckConstraint("operation IN ('upsert', 'delete', 'rebuild')", name="ck_projection_outbox_operation_valid"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'failed')",
            name="ck_projection_outbox_status_valid",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_projection_outbox_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE", name="fk_projection_outbox_workspace"),
        sa.PrimaryKeyConstraint("id", name="pk_projection_outbox"),
        sa.UniqueConstraint("idempotency_key", name="uq_projection_outbox_idempotency_key"),
    )
    op.create_index(
        "ix_projection_outbox_pending",
        "projection_outbox",
        ["target", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'failed')"),
    )

    op.execute(
        """
        CREATE FUNCTION reviewlens_prevent_context_immutable_change() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'context versions and manifests are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in ("context_node_versions", "context_manifests", "context_manifest_items"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reviewlens_prevent_context_immutable_change()"
        )


def downgrade() -> None:
    for table_name in ("context_manifest_items", "context_manifests", "context_node_versions"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reviewlens_prevent_context_immutable_change()")
    op.drop_table("projection_outbox")
    op.drop_table("context_manifest_items")
    op.drop_table("context_manifests")
    op.drop_table("context_edges")
    op.drop_constraint("fk_context_nodes_current_owned_version", "context_nodes", type_="foreignkey")
    op.drop_index("ix_context_node_versions_lexical", table_name="context_node_versions")
    op.drop_table("context_node_versions")
    op.drop_table("context_nodes")
    op.drop_table("workspaces")
