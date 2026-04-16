"""Add pending_downgrade_tier to organizations.

Stores the target tier for scheduled downgrades ("downgrade at end of period").
When subscription_cancel_at_period_end is True and this column is set, the
nightly billing_renewal job switches to this tier (instead of free) and charges
for the new plan's first period.

Revision ID: 0056_add_pending_downgrade_tier
Revises: 0055_add_upgrade_proration_credits
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0056_add_pending_downgrade_tier"
down_revision = "0055_add_upgrade_proration_credits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("pending_downgrade_tier", sa.String(20), nullable=True),
    )


def downgrade():
    op.drop_column("organizations", "pending_downgrade_tier")
