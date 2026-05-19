"""add VAT fields: vat_id on organizations, vat_rate and vat_amount_chf on payment_transactions

Revision ID: 0082_add_vat_fields
Revises: 0081_add_noga_peak_fields
Create Date: 2026-05-19

Supports per-transaction VAT calculation (CH=8.1%, EU B2B=0%, EU B2C=local rate)
and org-level VAT number storage for reverse-charge eligibility.
"""
from alembic import op
import sqlalchemy as sa

revision = "0082_add_vat_fields"
down_revision = "0081_add_noga_peak_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("vat_id", sa.String(64), nullable=True))
    op.add_column("payment_transactions", sa.Column("vat_rate", sa.Float(), nullable=True))
    op.add_column("payment_transactions", sa.Column("vat_amount_chf", sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_transactions", "vat_amount_chf")
    op.drop_column("payment_transactions", "vat_rate")
    op.drop_column("organizations", "vat_id")
