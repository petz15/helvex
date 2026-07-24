"""Add shab_old_uid_misses cache table.

resolve_shab_old_uids currently re-queries the UID register for every
unmatched old cantonal HR number on every run, because a failed lookup
leaves company_uid NULL and the publication row never leaves the candidate
set. Since the same old_uid is often cited by several publications (a
company's later mutations), the cache is keyed by the number itself, not by
publication.

Revision ID: 0122
Revises: 0121
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0122"
down_revision: str = "0121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shab_old_uid_misses",
        sa.Column("old_uid", sa.String(length=20), primary_key=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_table("shab_old_uid_misses")
