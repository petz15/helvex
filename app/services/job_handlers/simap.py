"""Handlers for simap_daily, simap_backfill, and simap_archive jobs."""
from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_simap(ctx: JobContext) -> tuple[dict, str]:
    from datetime import date as _date, datetime as _dt, timedelta, timezone

    from app.services.simap_import import import_simap_awards

    if ctx.job.job_type == "simap_daily":
        date_str = ctx.params.get("date")
        if date_str:
            target = _date.fromisoformat(date_str)
        else:
            target = (_dt.now(tz=timezone.utc) - timedelta(days=1)).date()
        from_date = target
        to_date = target
    else:  # simap_backfill
        from_date = _date.fromisoformat(ctx.params["from_date"])
        to_date_str = ctx.params.get("to_date")
        to_date = (
            _date.fromisoformat(to_date_str)
            if to_date_str
            else (_dt.now(tz=timezone.utc) - timedelta(days=1)).date()
        )

    # Resume support: restore pagination cursor from previous run stats
    import json as _json
    resume_cursor: str | None = None
    if ctx.resume_from and ctx.job.stats_json:
        try:
            prev_stats = _json.loads(ctx.job.stats_json)
            resume_cursor = prev_stats.get("last_cursor")
        except Exception:
            pass

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processing {done}/{total} — "
            f"{_stats.get('created', 0)} new, "
            f"{_stats.get('updated', 0)} updated, "
            f"{_stats.get('vendor_matched', 0)} CH companies matched"
        )
        ctx.progress(done, total, _stats, msg)

    stats = import_simap_awards(
        ctx.db,
        from_date=from_date,
        to_date=to_date,
        request_delay=float(ctx.params.get("request_delay", 0.12)),
        resume_cursor=resume_cursor,
        app=ctx.app,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['created']} new, {stats['updated']} updated, "
        f"{stats['skipped']} skipped, {stats['vendor_matched']} CH companies matched, "
        f"{stats['vendor_foreign']} foreign vendors"
    )
    if stats.get("errors"):
        done_msg += f", {len(stats['errors'])} errors"
    if resume_cursor:
        done_msg += " (resumed)"
    return stats, done_msg


def handle_simap_archive(ctx: JobContext) -> tuple[dict, str]:
    from datetime import date as _date

    from app.services.simap_archive_import import import_simap_archive

    from_date: _date | None = None
    to_date: _date | None = None
    if ctx.params.get("from_date"):
        from_date = _date.fromisoformat(ctx.params["from_date"])
    if ctx.params.get("to_date"):
        to_date = _date.fromisoformat(ctx.params["to_date"])

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done} projects — "
            f"{_stats.get('created', 0)} new, "
            f"{_stats.get('updated', 0)} updated, "
            f"{_stats.get('matched', 0)} CH companies matched"
        )
        ctx.progress(done, max(done, total), _stats, msg)

    stats = import_simap_archive(
        ctx.db,
        from_date=from_date,
        to_date=to_date,
        request_delay=float(ctx.params.get("request_delay", 0.10)),
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['created']} new, {stats['updated']} updated, "
        f"{stats['skipped']} skipped, "
        f"{stats['matched']} CH matched, {stats['unmatched']} unmatched, "
        f"{stats['foreign']} foreign"
    )
    if stats.get("errors"):
        done_msg += f", {len(stats['errors'])} errors"
    return stats, done_msg
