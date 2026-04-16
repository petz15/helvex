"""Widen alembic_version.version_num to VARCHAR(128).

The default Alembic column is VARCHAR(32), which is too narrow for revision
IDs longer than 32 characters (e.g. 0055_add_upgrade_proration_credits = 34).
This migration widens it so subsequent long-named revisions can be applied.

Revision ID: 0055a_widen_alembic_version
Revises: 0054_add_first_sogc_date
Create Date: 2026-04-17
"""
from alembic import op

revision = "0055a_widen_alembic_version"
down_revision = "0054_add_first_sogc_date"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"
    )


def downgrade():
    # Truncation risk: only safe if no revision IDs exceed 32 chars.
    op.execute(
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32)"
    )
