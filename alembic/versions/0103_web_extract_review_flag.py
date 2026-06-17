"""add review_flag to company_web_extract"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0103"
down_revision: Union[str, None] = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_web_extract", sa.Column("review_flag", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("company_web_extract", "review_flag")
