"""Add alert fields to user_views for saved-search email alerts.

alert_enabled       — user opt-in for daily new-company alerts on this view
alert_last_count    — company count recorded on last check (baseline for delta)
alert_last_checked_at — UTC timestamp of last check run

Revision ID: 0052_add_user_view_alert_fields
Revises: 0051_add_billing_renewal_fields
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0052_add_user_view_alert_fields"
down_revision = "0051_add_billing_renewal_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_views",
        sa.Column("alert_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "user_views",
        sa.Column("alert_last_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "user_views",
        sa.Column("alert_last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_views", "alert_last_checked_at")
    op.drop_column("user_views", "alert_last_count")
    op.drop_column("user_views", "alert_enabled")
