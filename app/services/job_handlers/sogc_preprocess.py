"""Handler for sogc_preprocess job."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_sogc_preprocess(ctx: JobContext) -> tuple[dict, str]:
    from app.services.sogc_preprocessor import run_sogc_preprocess_batch

    mode = ctx.params.get("mode", "missing")
    batch_size = int(ctx.params.get("batch_size", 500))

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
        batch_size=batch_size,
        resume_from=ctx.resume_from,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['processed']} companies processed, "
        f"{stats['publications_written']} publications written, "
        f"{stats['skipped_no_pub']} skipped (no sogc_pub), "
        f"{len(stats['errors'])} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg
