"""Add GIN trigram indexes for tags and claude_category ILIKE filters.

Revision ID: 0025
Revises: 0024
Create Date: 2026-03-25
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pg_trgm is already enabled by migration 0022 but CREATE EXTENSION is idempotent
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_tags_trgm ON companies "
        "USING gin (tags gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_claude_category_trgm ON companies "
        "USING gin (claude_category gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_claude_category_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_tags_trgm")
