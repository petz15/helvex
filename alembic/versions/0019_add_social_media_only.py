"""Add social_media_only column to companies.

Revision ID: 0019
Revises: 0018
Create Date: 2026-03-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.add_column(sa.Column("social_media_only", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_column("social_media_only")
