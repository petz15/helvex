"""Add uid_detail_fetched_at to companies

Tracks when the UID GetByUID detail call was last attempted for source='uid'
companies, so fetch_uid_details never re-processes already-fetched rows.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0114"
down_revision: Union[str, None] = "0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("uid_detail_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "uid_detail_fetched_at")
