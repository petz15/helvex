"""Add cardholder_name, billing_address to payment_transactions and organizations table.

Also adds billing_address_json to organizations for storing collected address before checkout.

Revision ID: 0044
Revises: 0043
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add cardholder identity and billing address to payment_transactions
    op.add_column(
        "payment_transactions",
        sa.Column("cardholder_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("billing_address", sa.Text(), nullable=True),
    )

    # Add billing address storage to organizations (collected during checkout)
    op.add_column(
        "organizations",
        sa.Column("billing_address_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "billing_address_json")
    op.drop_column("payment_transactions", "billing_address")
    op.drop_column("payment_transactions", "cardholder_name")
