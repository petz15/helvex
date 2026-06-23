"""Add registration_type and uid_raw columns to companies.

registration_type: 'hr' | 'mwst' | 'both' | 'uid_only' | NULL (unresolved)
  - hr       = registered with Handelsregister only
  - mwst     = registered for MWST/VAT only (not HR)
  - both     = HR + MWST
  - uid_only = in UID register via another authority (AHV, SUVA, statistical, etc.)
  - NULL     = legacy row; type not yet resolved

uid_raw: JSON blob of the raw UID web service response for this entity.
  Populated for source='uid' rows and also for source='zefix' rows once
  they are cross-referenced during the UID import.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0107"
down_revision: Union[str, None] = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("registration_type", sa.String(16), nullable=True))
    op.add_column("companies", sa.Column("uid_raw", sa.Text, nullable=True))
    op.create_index("ix_companies_registration_type", "companies", ["registration_type"])


def downgrade() -> None:
    op.drop_index("ix_companies_registration_type", table_name="companies")
    op.drop_column("companies", "uid_raw")
    op.drop_column("companies", "registration_type")
