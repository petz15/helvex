"""Add translations, zefix_detail_web, address_city, address_zip to companies

Revision ID: 0033
Revises: 0032
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("translations", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("zefix_detail_web", sa.String(1024), nullable=True))
    op.add_column("companies", sa.Column("address_city", sa.String(256), nullable=True))
    op.add_column("companies", sa.Column("address_zip", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "address_zip")
    op.drop_column("companies", "address_city")
    op.drop_column("companies", "zefix_detail_web")
    op.drop_column("companies", "translations")
