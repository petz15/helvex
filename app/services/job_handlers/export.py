"""Handler for csv_export job."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_csv_export(ctx: JobContext) -> tuple[dict, str]:
    from app.services.csv_export import run_csv_export
    from app.services.s3_client import is_configured

    if not is_configured():
        raise ValueError("S3 not configured: set S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, S3_ENDPOINT_URL")

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"Exported {done:,}" + (f"/{total:,}" if total else "") + " rows…"
        ctx._maybe_sync(ctx.app, job_type=ctx.job.job_type, label=ctx.job.label, message=msg, stats=dict(_stats), error=None, done=False)
        from app import crud
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=_stats)
        ctx._heartbeat()

    stats = run_csv_export(
        ctx.db,
        params=ctx.params,
        user_id=ctx.job.user_id,
        org_id=ctx.job.org_id,
        progress_cb=_progress,
    )
    return stats, f"Done — {stats['row_count']:,} rows exported to S3"
