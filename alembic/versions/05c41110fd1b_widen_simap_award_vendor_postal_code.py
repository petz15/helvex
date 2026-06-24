"""widen_simap_award_vendor_postal_code

Revision ID: 05c41110fd1b
Revises: 0107
Create Date: 2026-06-24 15:08:02.737723
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '05c41110fd1b'
down_revision: Union[str, None] = '0107'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "simap_award_vendors", "vendor_postal_code",
        type_=sa.String(50),
        existing_type=sa.String(10),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "simap_award_vendors", "vendor_postal_code",
        type_=sa.String(10),
        existing_type=sa.String(50),
        existing_nullable=True,
    )
