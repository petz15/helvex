"""Add old_uids array to companies

Pre-2014 companies carry an old cantonal Handelsregister number
(e.g. ``CH-035.3.029.394-7``) that the pre-2014 SHAB archive publications
reference instead of the modern CHE-UID. ``old_uids`` stores every such
number for a company (a company that changed canton can have several),
GIN-indexed so SHAB matching can resolve an old number via array overlap.
Populated on demand by ``resolve_shab_old_uids`` (reverse UID Search) and,
when present, by the ``uid_detail`` GetByUID enrichment.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

revision: str = "0116"
down_revision: Union[str, None] = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("old_uids", ARRAY(TEXT), nullable=True),
    )
    # GIN index enables fast ANY/overlap (&&) operators for SHAB old-number lookup.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_old_uids "
        "ON companies USING gin (old_uids)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_old_uids")
    op.drop_column("companies", "old_uids")
