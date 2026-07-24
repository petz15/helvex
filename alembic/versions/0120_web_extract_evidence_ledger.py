"""Add evidence ledger to company_web_extract.

Part of the web-pipeline holistic rework, Layer B phase 1 (identity ledger):
restructures the existing confidence-model inputs (UID match, address match,
zone-weighted name match, signal coverage) into a typed, inspectable evidence
list instead of only the final scalar `confidence`. Purely additive — no
existing behavior (confidence/method/compute_verdict) changes in this phase.
This is the feature vector the later categorical-verdict phase (and
eventually a trained model) will consume.

Revision ID: 0120
Revises: 0119
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0120"
down_revision: str = "0119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_web_extract", sa.Column("evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("company_web_extract", "evidence")
