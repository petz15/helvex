"""Add dedicated table for TF-IDF and purpose extraction stopwords.

Revision ID: 0037
Revises: 0036
Create Date: 2026-03-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tfidf_stopwords",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("value"),
    )
    op.create_index("ix_tfidf_stopwords_id", "tfidf_stopwords", ["id"])
    op.create_index("ix_tfidf_stopwords_value", "tfidf_stopwords", ["value"])
    op.create_index("ix_tfidf_stopwords_active", "tfidf_stopwords", ["active"])


def downgrade() -> None:
    op.drop_index("ix_tfidf_stopwords_active", table_name="tfidf_stopwords")
    op.drop_index("ix_tfidf_stopwords_value", table_name="tfidf_stopwords")
    op.drop_index("ix_tfidf_stopwords_id", table_name="tfidf_stopwords")
    op.drop_table("tfidf_stopwords")
