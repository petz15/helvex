"""Handler for sogc_preprocess job."""

from __future__ import annotations

from app.services.jobs.job_handlers import JobContext


def handle_sogc_preprocess(ctx: JobContext) -> tuple[dict, str]:
    from app.services.registry.sogc_preprocessor import (
        run_sogc_preprocess_batch,
        run_sogc_publications_backfill,
    )

    mode = ctx.params.get("mode", "missing")
    batch_size = int(ctx.params.get("batch_size", 500))

    # mode="publications": fix encoding + regenerate changes on existing publications
    if mode == "publications":
        def _progress_pub(done: int, total: int, _stats: dict) -> None:
            ctx.assert_not_cancelled()
            msg = (
                f"Processing {done}/{total} publications — "
                f"{_stats.get('changes_written', 0)} changes, "
                f"{_stats.get('encoding_fixed', 0)} encoding fixes"
            )
            ctx.progress(done, total, _stats, msg)

        stats = run_sogc_publications_backfill(
            ctx.db,
            batch_size=batch_size,
            resume_from=ctx.resume_from,
            progress_cb=_progress_pub,
            status_cb=lambda m: ctx.status_with_stats(m),
            abort_cb=ctx.assert_not_cancelled,
        )
        done_msg = (
            f"Done — {stats['processed']} publications processed, "
            f"{stats['changes_written']} changes written, "
            f"{stats['encoding_fixed']} encoding fixes, "
            f"{len(stats['errors'])} errors"
        )
        return stats, done_msg

    uids_raw = ctx.params.get("uids")
    uids = [u for u in (uids_raw or []) if u] or None

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processing {done}/{total} — "
            f"{_stats.get('processed', 0)} companies, "
            f"{_stats.get('publications_written', 0)} publications"
        )
        ctx.progress(done, total, _stats, msg)

    stats = run_sogc_preprocess_batch(
        ctx.db,
        mode=mode,
        uids=uids,
        batch_size=batch_size,
        resume_from=ctx.resume_from,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['processed']} companies processed, "
        f"{stats['publications_written']} publications written, "
        f"{stats['skipped_no_pub']} skipped (no data), "
        f"{len(stats['errors'])} errors"
    )
    if uids:
        done_msg += f" (targeted: {len(uids)} UID(s))"
    elif ctx.resume_from:
        done_msg += f" (resumed from company id={ctx.resume_from})"
    return stats, done_msg
