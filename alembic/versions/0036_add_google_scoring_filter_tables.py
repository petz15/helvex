"""Add dedicated tables for Google scoring stopwords and directory domains.

Revision ID: 0036
Revises: 0035
Create Date: 2026-03-30
"""

from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SPLIT_RE = re.compile(r"[,;\n\r]+")


def _parts(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip().lower() for p in _SPLIT_RE.split(raw) if p and p.strip()]


def upgrade() -> None:
    op.create_table(
        "google_stopwords",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("value"),
    )
    op.create_index("ix_google_stopwords_id", "google_stopwords", ["id"])
    op.create_index("ix_google_stopwords_value", "google_stopwords", ["value"])
    op.create_index("ix_google_stopwords_active", "google_stopwords", ["active"])

    op.create_table(
        "google_directory_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("value"),
    )
    op.create_index("ix_google_directory_domains_id", "google_directory_domains", ["id"])
    op.create_index("ix_google_directory_domains_value", "google_directory_domains", ["value"])
    op.create_index("ix_google_directory_domains_active", "google_directory_domains", ["active"])

    # One-time migration of existing settings values into dedicated tables.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT key, value FROM app_settings WHERE key IN ('google_scoring_stopwords', 'google_scoring_directory_domains')"
        )
    ).fetchall()

    stopwords: set[str] = set()
    domains: set[str] = set()
    for key, value in rows:
        if key == "google_scoring_stopwords":
            stopwords.update(_parts(value))
        elif key == "google_scoring_directory_domains":
            domains.update(_parts(value))

    for value in sorted(stopwords):
        conn.execute(
            sa.text(
                "INSERT INTO google_stopwords (value, description, active) VALUES (:value, :description, true)"
            ),
            {"value": value, "description": "migrated from app_settings"},
        )

    for value in sorted(domains):
        conn.execute(
            sa.text(
                "INSERT INTO google_directory_domains (value, description, active) VALUES (:value, :description, true)"
            ),
            {"value": value, "description": "migrated from app_settings"},
        )


def downgrade() -> None:
    op.drop_index("ix_google_directory_domains_active", table_name="google_directory_domains")
    op.drop_index("ix_google_directory_domains_value", table_name="google_directory_domains")
    op.drop_index("ix_google_directory_domains_id", table_name="google_directory_domains")
    op.drop_table("google_directory_domains")

    op.drop_index("ix_google_stopwords_active", table_name="google_stopwords")
    op.drop_index("ix_google_stopwords_value", table_name="google_stopwords")
    op.drop_index("ix_google_stopwords_id", table_name="google_stopwords")
    op.drop_table("google_stopwords")
