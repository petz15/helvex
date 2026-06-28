"""Add purpose_sim to company_web_extract

Cosine similarity (0–1) between the company's Zefix purpose embedding and the
crawled site's description/service_keywords embedding. Computed by the ML-worker
enrich_web_purpose_sim job using paraphrase-multilingual-mpnet-base-v2 (same model
as NOGA classification). Used as a 4th confidence layer in website_status.py
_extract_tier() to lift borderline extracts when the site's content semantically
matches what the company says it does.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0110"
down_revision: Union[str, None] = "0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_web_extract",
        sa.Column("purpose_sim", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_web_extract", "purpose_sim")
