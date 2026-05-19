"""add noga_peak_code and noga_peak_label to companies

Revision ID: 0081_add_noga_peak_fields
Revises: 0080_add_user_logged_out_at
Create Date: 2026-05-19

Records the best-match 'peak' code found by the v2 global embedding search,
separately from the final leaf code produced by constrained descent.
"""
from alembic import op
import sqlalchemy as sa

revision = "0081_add_noga_peak_fields"
down_revision = "0080_add_user_logged_out_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("noga_peak_code", sa.String(16), nullable=True))
    op.add_column("companies", sa.Column("noga_peak_label", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "noga_peak_label")
    op.drop_column("companies", "noga_peak_code")
