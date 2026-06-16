"""Add partial index for companies missing a NOGA classification.

The reclassify_noga job's only_missing_noga (non-stale) path filters on
noga_code IS NULL but had no matching index, forcing a sequential scan
on the 700k-row companies table. Mirrors the existing
ix_companies_no_ai_score / ix_companies_no_flex_score partial indexes
from migration 0101.

Revision ID: 0102
Revises: 0101
Create Date: 2026-06-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0102"
down_revision: Union[str, None] = "0101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_no_noga_code "
        "ON companies (id) WHERE noga_code IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_no_noga_code")
