"""Add GIN full-text search index on simap_awards title + authority columns.

Enables fast `plainto_tsquery('simple', :q)` queries over the concatenated
title and procuring-office name fields (DE/FR/IT) without needing a stored
tsvector column.
"""
from __future__ import annotations
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0106"
down_revision: Union[str, None] = "0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_simap_awards_fts ON simap_awards USING GIN (
            to_tsvector('simple',
                coalesce(title_de, '') || ' ' ||
                coalesce(title_fr, '') || ' ' ||
                coalesce(title_it, '') || ' ' ||
                coalesce(proc_office_name_de, '') || ' ' ||
                coalesce(proc_office_name_fr, '') || ' ' ||
                coalesce(proc_office_name_it, '')
            )
        )
    """)
    # Index vendor name for fuzzy award-by-vendor-name lookup
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_simap_award_vendors_name_trgm
        ON simap_award_vendors USING GIN (vendor_name gin_trgm_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_simap_awards_fts")
    op.execute("DROP INDEX IF EXISTS ix_simap_award_vendors_name_trgm")
