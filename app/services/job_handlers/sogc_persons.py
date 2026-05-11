"""Handler for extract_sogc_persons job."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_extract_sogc_persons(ctx: JobContext) -> tuple[dict, str]:
    from app.services.sogc_person_extractor import run_extract_sogc_persons_batch

    mode = ctx.params.get("mode", "missing")
    batch_size = int(ctx.params.get("batch_size", 1000))

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processing {done}/{total} — "
            f"{_stats.get('persons_written', 0)} persons, "
            f"{_stats.get('auditors_written', 0)} auditors, "
            f"{len(_stats.get('errors', []))} errors"
        )
        ctx.progress(done, total, _stats, msg)

    stats = run_extract_sogc_persons_batch(
        ctx.db,
        mode=mode,
        batch_size=batch_size,
        resume_from=ctx.resume_from,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['persons_written']} persons, "
        f"{stats['auditors_written']} auditors extracted from "
        f"{stats['processed']} changes, "
        f"{stats['skipped_no_excerpt']} skipped, "
        f"{len(stats['errors'])} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from change id={ctx.resume_from})"
    return stats, done_msg
