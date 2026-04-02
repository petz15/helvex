"""Add payment_transactions table for audit trail of all payments.

Revision ID: 0043
Revises: 0042
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False, unique=True),
        sa.Column("order_reference", sa.String(255), nullable=False),
        sa.Column("amount_chf", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CHF"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("provider_transaction_id", sa.String(255), nullable=True),
        sa.Column("subscription_tier", sa.String(20), nullable=True),
        sa.Column("subscription_billing_cycle", sa.String(10), nullable=True),
        sa.Column("credits_purchased", sa.BigInteger(), nullable=True),
        sa.Column("credits_bonus", sa.BigInteger(), nullable=True),
        sa.Column("credits_total_granted", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("refunded_amount_chf", sa.Float(), nullable=True),
        sa.Column("refund_reason", sa.String(255), nullable=True),
        sa.Column("webhook_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Index for fast lookup by external_id
    op.create_index("ix_payment_transactions_external_id", "payment_transactions", ["external_id"], unique=True)
    # Index for fast lookup by org_id
    op.create_index("ix_payment_transactions_org_id", "payment_transactions", ["org_id"])
    # Index for status queries
    op.create_index("ix_payment_transactions_status", "payment_transactions", ["status"])
    # Index for created_at queries
    op.create_index("ix_payment_transactions_created_at", "payment_transactions", ["created_at"])


def downgrade() -> None:
    op.drop_table("payment_transactions")
