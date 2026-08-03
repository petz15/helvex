"""Housekeeping jobs — currently job-history retention.

`job_runs` is append-only and nothing ever pruned it. The crawler pipeline is
what makes that a problem rather than a curiosity: several crawl types are in
NO_DEDUP and auto-enqueue each other per batch, so a full pass over the ~700k
company corpus leaves tens of thousands of terminal rows behind, each with its
own `job_run_events` stream. That bloats the jobs UI query, the SSE poller, and
the table itself.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, text

from app import crud
from app.models.job_run import JobRun
from app.services.jobs.job_handlers import JobContext

logger = logging.getLogger(__name__)

# Statuses safe to delete. Anything still queued/running/paused/waiting_external
# is live work and is never touched, regardless of age.
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")

_DEFAULT_RETENTION_DAYS = 30
# Always keep this many most-recent terminal runs per job type, even if older
# than the cutoff — otherwise a rarely-run job (a yearly archive import) loses
# its entire history and the operator has nothing to compare a new run against.
_DEFAULT_KEEP_PER_TYPE = 20
# Deleted per statement. job_run_events cascades on the FK, so a large chunk can
# fan out to a lot of row deletions and blow the 30 s statement_timeout.
_CHUNK = 1_000


def handle_cleanup_job_runs(ctx: JobContext) -> tuple[dict, str]:
    """Delete terminal job_runs older than `retention_days`.

    `job_run_events` has `ON DELETE CASCADE` on `job_id`, so its rows go with
    the parent — no separate pass, and no chance of orphaning a stream.

    Params:
      retention_days  (default 30) — age cutoff for terminal runs
      keep_per_type   (default 20) — floor of recent runs kept per job_type
      dry_run         (default False) — count what would go, delete nothing
    """
    retention_days = int(ctx.params.get("retention_days", _DEFAULT_RETENTION_DAYS))
    keep_per_type = int(ctx.params.get("keep_per_type", _DEFAULT_KEEP_PER_TYPE))
    dry_run = bool(ctx.params.get("dry_run", False))

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    stats: dict = {
        "retention_days": retention_days,
        "keep_per_type": keep_per_type,
        "deleted": 0,
        "dry_run": dry_run,
    }

    # Rows eligible for deletion: terminal, older than the cutoff, and NOT among
    # the `keep_per_type` newest of their own type. The window function does the
    # per-type floor in one pass rather than a query per distinct job_type.
    # Written portably (expanding bindparam, no ANY(...)) so the SQLite test DB
    # exercises the same statement Postgres runs.
    _ranked_cte = """
        SELECT ranked.id AS id FROM (
            SELECT id, job_type, status, completed_at, queued_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY job_type ORDER BY COALESCE(completed_at, queued_at) DESC
                   ) AS rn
            FROM job_runs
            WHERE status IN :statuses
        ) ranked
        WHERE ranked.rn > :keep
          AND COALESCE(ranked.completed_at, ranked.queued_at) < :cutoff
    """

    def _stmt(sql: str):
        return text(sql).bindparams(
            bindparam("statuses", value=list(_TERMINAL_STATUSES), expanding=True),
            bindparam("keep", value=keep_per_type),
            bindparam("cutoff", value=cutoff),
        )

    total_eligible = int(
        ctx.db.execute(_stmt(f"SELECT COUNT(*) FROM ({_ranked_cte}) e")).scalar() or 0
    )
    stats["eligible"] = total_eligible

    if dry_run:
        return stats, (
            f"Dry run — {total_eligible} terminal job runs older than "
            f"{retention_days}d would be deleted (keeping {keep_per_type} per type)"
        )

    while True:
        ctx.assert_not_cancelled()

        ids = [
            r[0]
            for r in ctx.db.execute(
                _stmt(f"{_ranked_cte} LIMIT {int(_CHUNK)}")
            ).fetchall()
        ]
        # Never delete the job doing the deleting. It is 'running', so the status
        # filter already excludes it — but be explicit, because a future change to
        # _TERMINAL_STATUSES would otherwise have this job delete its own row
        # mid-run and lose every progress write that follows.
        ids = [i for i in ids if i != ctx.job.id]
        if not ids:
            break

        ctx.db.query(JobRun).filter(JobRun.id.in_(ids)).delete(
            synchronize_session=False
        )
        ctx.db.commit()
        stats["deleted"] += len(ids)

        msg = f"Pruned {stats['deleted']}/{total_eligible} old job runs"
        crud.update_progress(
            ctx.db, ctx.job, message=msg,
            done=stats["deleted"], total=total_eligible, stats=dict(stats),
        )

    return stats, (
        f"Pruned {stats['deleted']} terminal job runs older than {retention_days}d "
        f"(kept the {keep_per_type} most recent per job type; events cascaded)"
    )
