"""Disable fastupdate on ix_companies_old_uids

``resolve_shab_old_uids`` interleaves reads (array overlap lookups) and
writes (appending newly API-resolved old numbers) on ``old_uids`` within
the same long-lived session/transaction. GIN's default fastupdate mode
buffers writes in an unsorted "pending list" that every subsequent read
must scan linearly until it's flushed by VACUUM/autovacuum — so as a job
run appends more old numbers, its own later lookups get progressively
slower, eventually exceeding the 30s statement_timeout. Write volume on
this column is low (pre-2014 companies only, appended rarely), so turning
off fastupdate trades a negligible per-write cost for consistently fast
reads.
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0117"
down_revision: Union[str, None] = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER INDEX ix_companies_old_uids SET (fastupdate = off)"
    )
    # Flush any pending-list entries already buffered from prior runs.
    op.execute("SELECT gin_clean_pending_list('ix_companies_old_uids')")


def downgrade() -> None:
    op.execute(
        "ALTER INDEX ix_companies_old_uids SET (fastupdate = on)"
    )
