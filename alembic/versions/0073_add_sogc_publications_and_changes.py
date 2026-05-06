"""Add sogc_publications and sogc_changes tables for structured SOGC history.

Revision ID: 0073
Revises: 0072
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sogc_publications",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("sogc_id", sa.String(32), nullable=False),
        sa.Column(
            "company_uid",
            sa.String(20),
            sa.ForeignKey("companies.uid", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pub_date", sa.String(32), nullable=True),
        sa.Column("sub_rubric", sa.String(8), nullable=True),
        sa.Column("pub_number", sa.String(64), nullable=True),
        sa.Column("text_de", sa.Text(), nullable=True),
        sa.Column("text_fr", sa.Text(), nullable=True),
        sa.Column("text_it", sa.Text(), nullable=True),
        sa.Column("text_en", sa.Text(), nullable=True),
        sa.Column("detected_language", sa.String(8), nullable=True),
        sa.Column("encoding_fixed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("preprocessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_sogc_publications_sogc_id", "sogc_publications", ["sogc_id"], unique=True)
    op.create_index("ix_sogc_publications_company_uid", "sogc_publications", ["company_uid"])
    op.create_index("ix_sogc_publications_pub_date", "sogc_publications", ["pub_date"])

    op.create_table(
        "sogc_changes",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "sogc_publication_id",
            sa.Integer(),
            sa.ForeignKey("sogc_publications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("keywords_matched", sa.Text(), nullable=True),
        sa.Column("raw_excerpt", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_sogc_changes_sogc_publication_id", "sogc_changes", ["sogc_publication_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sogc_changes_sogc_publication_id", table_name="sogc_changes")
    op.drop_table("sogc_changes")

    op.drop_index("ix_sogc_publications_pub_date", table_name="sogc_publications")
    op.drop_index("ix_sogc_publications_company_uid", table_name="sogc_publications")
    op.drop_index("ix_sogc_publications_sogc_id", table_name="sogc_publications")
    op.drop_table("sogc_publications")
