"""Add subscription_cancel_at_period_end and recurring_transaction_id to organizations.

subscription_cancel_at_period_end — True when customer has requested cancellation;
    nightly job downgrades tier once subscription_period_end is reached.
recurring_transaction_id — Saferpay Transaction.Id from initial subscription payment,
    used as reference for AuthorizeReferenced recurring charges.

Revision ID: 0051_add_billing_renewal_fields
Revises: 0050_add_cluster_registry
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa

revision = "0051_add_billing_renewal_fields"
down_revision = "0050_add_cluster_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "subscription_cancel_at_period_end",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("recurring_transaction_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "recurring_transaction_id")
    op.drop_column("organizations", "subscription_cancel_at_period_end")
