"""Add GIN trigram index on companies.name for fast ILIKE substring search.

Revision ID: 0090
Revises: 0089
Create Date: 2026-06-01
"""

from alembic import op

revision: str = "0090"
down_revision: str = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # B-tree index on name cannot serve leading-wildcard ILIKE ('%term%').
    # GIN trigram index supports any ILIKE pattern in O(log n) via pg_trgm.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_name_trgm "
        "ON companies USING GIN (name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_name_trgm")
