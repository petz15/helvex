"""add company_id integer FK to sogc_person_appearances and sogc_auditors

Revision ID: 0089
Revises: 0088
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

BATCH_SIZE = 10_000


def _backfill_in_batches(conn, table: str, id_col: str = "id") -> None:
    while True:
        result = conn.execute(
            sa.text(
                f"""
                UPDATE {table} t
                SET company_id = c.id
                FROM companies c
                WHERE c.uid = t.company_uid
                  AND t.company_id IS NULL
                  AND t.{id_col} IN (
                      SELECT {id_col} FROM {table}
                      WHERE company_id IS NULL
                      LIMIT {BATCH_SIZE}
                  )
                """
            )
        )
        if result.rowcount == 0:
            break


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("SET LOCAL statement_timeout = '0'"))

    op.add_column(
        "sogc_person_appearances",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_sogc_person_appearances_company_id", "sogc_person_appearances", ["company_id"])
    _backfill_in_batches(conn, "sogc_person_appearances")

    op.add_column(
        "sogc_auditors",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_sogc_auditors_company_id", "sogc_auditors", ["company_id"])
    _backfill_in_batches(conn, "sogc_auditors")


def downgrade() -> None:
    op.drop_index("ix_sogc_auditors_company_id", table_name="sogc_auditors")
    op.drop_column("sogc_auditors", "company_id")
    op.drop_index("ix_sogc_person_appearances_company_id", table_name="sogc_person_appearances")
    op.drop_column("sogc_person_appearances", "company_id")
