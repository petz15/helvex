"""Merge sogc_publications branch with restore_sogc_json_blob_columns branch.

Revision ID: 0074_merge_sogc_heads
Revises: 0073, 0073_restore_sogc_json_blob_columns
Create Date: 2026-05-06
"""

from alembic import op

revision = "0074_merge_sogc_heads"
down_revision = ("0073", "0073_restore_sogc_json_blob_columns")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
