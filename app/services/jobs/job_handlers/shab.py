"""Handler for shab_daily and shab_backfill jobs."""

from __future__ import annotations

import logging

from app import crud
from app.services.jobs.job_handlers import JobContext

logger = logging.getLogger(__name__)


def handle_shab(ctx: JobContext) -> tuple[dict, str]:
    from datetime import date as _date
    from app.services.registry.shab_import import import_shab_publications, yesterday

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

    # Auto-chain SOGC preprocessing and person extraction after a daily import.
    # Only chain when shab_daily actually brought in new or updated publications.
    # ctx.enqueue_job routes through enqueue_job(app, db=ctx.db, ...) → the same
    # _enqueue_job_in_session used elsewhere, so the dedup key is stored and a second
    # shab_daily run finds the already-queued job rather than creating a duplicate.
    # It also ensures the worker thread is running, so no separate kick is needed.
    if ctx.job.job_type == "shab_daily" and (stats.get("created", 0) + stats.get("updated", 0)) > 0:
        _CHAIN = [
            ("sogc_preprocess",      "SOGC preprocess — nightly auto (after shab_daily)"),
            ("extract_sogc_persons", "SOGC person extraction — nightly auto (after shab_daily)"),
        ]
        queued_types: list[str] = []
        for chain_type, chain_label in _CHAIN:
            try:
                chain_job = ctx.enqueue_job(
                    job_type=chain_type,
                    label=chain_label,
                    params={"mode": "missing"},
                )
                crud.create_event(
                    ctx.db, job_id=chain_job.id, level="info",
                    message=f"Auto-queued after shab_daily job #{ctx.job.id}",
                )
                queued_types.append(chain_type)
            except Exception:
                logger.warning(
                    "Failed to auto-queue %s after shab_daily job #%s",
                    chain_type, ctx.job.id, exc_info=True,
                )
        if queued_types:
            done_msg += f"; queued {', '.join(queued_types)}"

    return stats, done_msg
