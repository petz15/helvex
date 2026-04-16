"""Add upgrade_proration_credits to payment_transactions.

Credits for plan upgrades are now granted only after payment is captured,
not when the user opens the upgrade dialog. The amount is stored on the
transaction so apply_successful_payment can grant it idempotently.

Revision ID: 0055_add_upgrade_proration_credits
Revises: 0055a_widen_alembic_version
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0055_add_upgrade_proration_credits"
down_revision = "0055a_widen_alembic_version"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "payment_transactions",
        sa.Column("upgrade_proration_credits", sa.BigInteger, nullable=True),
    )


def downgrade():
    op.drop_column("payment_transactions", "upgrade_proration_credits")
