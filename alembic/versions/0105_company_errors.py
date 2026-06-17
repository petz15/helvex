"""add company_errors table"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0105"
down_revision: Union[str, None] = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=True),
        sa.Column("error_source", sa.String(32), nullable=False),
        sa.Column("error_type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("job_run_id", sa.Integer(), sa.ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ignored", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_errors_company_id", "company_errors", ["company_id"])
    op.create_index("ix_company_errors_error_source", "company_errors", ["error_source"])
    op.create_index("ix_company_errors_created_at", "company_errors", ["created_at"])
    # Partial index for active (unresolved, not ignored) errors — used in most queries
    op.execute(
        "CREATE INDEX ix_company_errors_active ON company_errors (created_at DESC) "
        "WHERE resolved_at IS NULL AND NOT ignored"
    )


def downgrade() -> None:
    op.drop_index("ix_company_errors_active", table_name="company_errors")
    op.drop_index("ix_company_errors_created_at", table_name="company_errors")
    op.drop_index("ix_company_errors_error_source", table_name="company_errors")
    op.drop_index("ix_company_errors_company_id", table_name="company_errors")
    op.drop_table("company_errors")
