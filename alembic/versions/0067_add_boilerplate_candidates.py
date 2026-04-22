"""Add boilerplate_candidates table for auto-mined stopword candidates.

Revision ID: 0067
Revises: 0066
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "boilerplate_candidates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("term", sa.Text, nullable=False, unique=True),
        sa.Column("doc_frequency", sa.Float, nullable=True),
        sa.Column("cluster_entropy", sa.Float, nullable=True),
        sa.Column("cluster_label_frequency", sa.Float, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="corpus_mined"),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column("llm_label", sa.String(16), nullable=True),
        sa.Column("promoted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("promoted_pattern_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_boilerplate_candidates_confidence", "boilerplate_candidates", ["confidence"])
    op.create_index("ix_boilerplate_candidates_promoted", "boilerplate_candidates", ["promoted"])


def downgrade() -> None:
    op.drop_table("boilerplate_candidates")
