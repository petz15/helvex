"""Add purpose_clean to companies

Stores the boilerplate-stripped purpose text: semantic embedding-similarity
method for DE/FR (validated to correctly preserve company-specific content that
the regex-based _strip_purpose_boilerplate either misses or over-truncates —
see scripts/validate_boilerplate_similarity.py), falling back to the existing
regex method for other languages. Precomputed once by the strip_purpose_semantic
job (app/services/ml/boilerplate_semantic.py) and read by get_purpose_clean() at
NOGA/Claude classification time and by the purpose_clean embedding job — no live
model calls needed once populated.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0127"
down_revision: Union[str, None] = "0126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("purpose_clean", sa.Text(), nullable=True))
    op.add_column(
        "companies",
        sa.Column("purpose_clean_computed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "purpose_clean_computed_at")
    op.drop_column("companies", "purpose_clean")
