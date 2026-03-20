"""Drop industry column from companies.

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_industry")
    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_column("industry")


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.add_column(sa.Column("industry", sa.String(length=128), nullable=True))
        batch_op.create_index("ix_companies_industry", ["industry"])
