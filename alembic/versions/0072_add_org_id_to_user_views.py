"""Add org_id to user_views.

user_views is the only workflow table without org_id. A user switching orgs
sees views saved in a different org context, which is confusing.

This migration:
1. Adds org_id FK (nullable initially for safe backfill)
2. Backfills from users.org_id at migration time
3. Sets default so new rows auto-populate via the application layer

Note: org_id intentionally stays nullable (SET NULL on org delete) so views
survive if a user's org is deleted. The application layer filters views by
org_id IS NULL OR org_id = current_org_id for backward compatibility with
any views that existed before this migration without a valid org.

Revision ID: 0072
Revises: 0071
Create Date: 2026-05-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_views",
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_user_views_org_id", "user_views", ["org_id"])

    # Backfill: set org_id from the owning user's current org at migration time.
    # Views without a resolvable org remain NULL and are shown in all org contexts.
    op.execute(
        """
        UPDATE user_views uv
        SET org_id = u.org_id
        FROM users u
        WHERE uv.user_id = u.id
          AND u.org_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_user_views_org_id", table_name="user_views")
    op.drop_column("user_views", "org_id")
