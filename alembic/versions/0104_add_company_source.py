"""add source column to companies"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0104"
down_revision: Union[str, None] = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("source", sa.String(32), nullable=True))
    op.create_index("ix_companies_source", "companies", ["source"])


def downgrade() -> None:
    op.drop_index("ix_companies_source", table_name="companies")
    op.drop_column("companies", "source")
