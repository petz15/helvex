"""Add NOGA classification fields to companies

Revision ID: 0035
Revises: 0034
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("noga_code", sa.String(length=16), nullable=True))
    op.add_column("companies", sa.Column("noga_label", sa.String(length=512), nullable=True))
    op.add_column("companies", sa.Column("noga_level", sa.String(length=32), nullable=True))
    op.add_column("companies", sa.Column("noga_confidence", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("noga_classified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_companies_noga_code", "companies", ["noga_code"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_companies_noga_code", table_name="companies")
    op.drop_column("companies", "noga_classified_at")
    op.drop_column("companies", "noga_confidence")
    op.drop_column("companies", "noga_level")
    op.drop_column("companies", "noga_label")
    op.drop_column("companies", "noga_code")
