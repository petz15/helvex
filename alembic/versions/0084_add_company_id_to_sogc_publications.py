"""Add company_id FK to sogc_publications for direct company reference.

Revision ID: 0084
Revises: 0083_add_company_embeddings
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sogc_publications", sa.Column("company_id", sa.Integer(), nullable=True))
    op.create_index("ix_sogc_publications_company_id", "sogc_publications", ["company_id"])
    op.create_foreign_key(
        "fk_sogc_publications_company_id",
        "sogc_publications", "companies",
        ["company_id"], ["id"],
        ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_sogc_publications_company_id", "sogc_publications", type_="foreignkey")
    op.drop_index("ix_sogc_publications_company_id", "sogc_publications")
    op.drop_column("sogc_publications", "company_id")
