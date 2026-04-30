"""Add indexes for batch classification queries and job_runs ordering.

Revision ID: 0070
Revises: 0069
Create Date: 2026-04-30
"""

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── companies: partial indexes for batch pipeline hot paths ────────────────
    # purpose IS NOT NULL is the base filter for every classification/clustering
    # job (collection.py, cluster_pipeline.py, incremental_classify.py, etc.)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_has_purpose "
        "ON companies (id) WHERE purpose IS NOT NULL"
    )

    # noga_code IS NULL: reclassify_noga and incremental_classify filter on this
    # to find companies that still need NOGA classification
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_unclassified_noga "
        "ON companies (id) WHERE noga_code IS NULL"
    )

    # purpose_language IS NULL + purpose IS NOT NULL: detect_language_bulk filters
    # exactly this combination to find companies needing language detection
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_no_language "
        "ON companies (id) WHERE purpose_language IS NULL AND purpose IS NOT NULL"
    )

    # ── companies: legal_form B-tree index ────────────────────────────────────
    # _apply_filters filters on legal_form directly; no index exists anywhere
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_legal_form "
        "ON companies (legal_form)"
    )

    # ── job_runs: queued_at — used in ORDER BY for every job list query ────────
    # list_jobs, list_org_jobs, list_active_jobs, get_next_queued_job, etc. all
    # ORDER BY queued_at; the column has no index despite being the primary sort key
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_runs_queued_at "
        "ON job_runs (queued_at DESC)"
    )

    # ── job_runs: (job_type, status) composite ─────────────────────────────────
    # list_waiting_llm_batches, get_latest_csv_export, has_shab_daily_run_today,
    # cancel_active_csv_exports all filter on both columns simultaneously
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_runs_job_type_status "
        "ON job_runs (job_type, status)"
    )

    # ── job_runs: completed_at ─────────────────────────────────────────────────
    # delete_old_finished_jobs and requeue_recent_abandoned_jobs filter/range-scan
    # on completed_at; no index exists
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_runs_completed_at "
        "ON job_runs (completed_at)"
    )

    op.execute("ANALYSE companies")
    op.execute("ANALYSE job_runs")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_job_runs_completed_at")
    op.execute("DROP INDEX IF EXISTS ix_job_runs_job_type_status")
    op.execute("DROP INDEX IF EXISTS ix_job_runs_queued_at")
    op.execute("DROP INDEX IF EXISTS ix_companies_legal_form")
    op.execute("DROP INDEX IF EXISTS ix_companies_no_language")
    op.execute("DROP INDEX IF EXISTS ix_companies_unclassified_noga")
    op.execute("DROP INDEX IF EXISTS ix_companies_has_purpose")
