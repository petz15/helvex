"""Widen tfidf_cluster from String(128) to String(512) for multi-keyword storage.

Revision ID: 0015
Revises: 0014
Create Date: 2026-03-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.alter_column(
            "tfidf_cluster",
            existing_type=sa.String(128),
            type_=sa.String(512),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.alter_column(
            "tfidf_cluster",
            existing_type=sa.String(512),
            type_=sa.String(128),
            existing_nullable=True,
        )
