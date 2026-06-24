"""Drop GIN tsvector index on simap_awards.

The index (ix_simap_awards_fts) was added to accelerate the ?q= full-text filter
in the SIMAP search route, but simap_awards is small enough that PostgreSQL uses
a sequential scan regardless. The index only added overhead on every upsert —
recomputing to_tsvector() over six text columns caused statement_timeout (30 s)
failures during bulk archive imports of records with long descriptions.
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0108"
down_revision: Union[str, None] = "05c41110fd1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_simap_awards_fts")
    op.execute("DROP INDEX IF EXISTS ix_simap_award_vendors_name_trgm")


def downgrade() -> None:
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
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_simap_award_vendors_name_trgm
        ON simap_award_vendors USING GIN (vendor_name gin_trgm_ops)
    """)
