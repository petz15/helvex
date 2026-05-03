"""Handler for shab_daily and shab_backfill jobs."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_shab(ctx: JobContext) -> tuple[dict, str]:
    from datetime import date as _date
    from app.services.shab_import import import_shab_publications, yesterday

    if ctx.job.job_type == "shab_daily":
        date_str = ctx.params.get("date")
        if date_str:
            target_date = _date.fromisoformat(date_str)
        else:
            target_date = yesterday()
        from_date = target_date
        to_date = target_date
    else:
        from_date = _date.fromisoformat(ctx.params["from_date"])
        to_date_str = ctx.params.get("to_date")
        to_date = _date.fromisoformat(to_date_str) if to_date_str else yesterday()

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processing {done}/{total} — "
            f"{_stats.get('created', 0)} new, "
            f"{_stats.get('updated', 0)} updated, "
            f"{_stats.get('deleted', 0)} deleted"
        )
        ctx.progress(done, total, _stats, msg)

    stats = import_shab_publications(
        ctx.db,
        from_date=from_date,
        to_date=to_date,
        app=ctx.app,
        request_delay=float(ctx.params.get("request_delay", 0.15)),
        resume_from=ctx.resume_from,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )
    done_msg = (
        f"Done — {stats['created']} new, {stats['updated']} updated, "
        f"{stats['deleted']} deleted, {stats['skipped']} skipped, "
        f"{len(stats['errors'])} errors "
        f"({stats['publications_fetched']} publications fetched)"
    )
    if stats.get("detail_jobs_queued"):
        done_msg += f"; {stats['detail_jobs_queued']} detail job(s) queued for new UIDs"
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg
