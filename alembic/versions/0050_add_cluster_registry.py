"""Add cluster_registry table for stable cluster identity across pipeline runs.

canonical_name  — stable label stored in tfidf_cluster
top_terms       — JSON array of top c-TF-IDF terms (used for Jaccard matching)
active          — False when cluster disappeared from the latest pipeline run

Revision ID: 0050_add_cluster_registry
Revises: 0049_add_noga_path_fields
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0050_add_cluster_registry"
down_revision = "0049_add_noga_path_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cluster_registry",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("canonical_name", sa.Text, nullable=False),
        sa.Column("top_terms", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cluster_registry_id", "cluster_registry", ["id"])
    op.create_index("ix_cluster_registry_canonical_name", "cluster_registry", ["canonical_name"])


def downgrade() -> None:
    op.drop_index("ix_cluster_registry_canonical_name", table_name="cluster_registry")
    op.drop_index("ix_cluster_registry_id", table_name="cluster_registry")
    op.drop_table("cluster_registry")
