"""Handler for repair_is_current job."""
from __future__ import annotations

from app.services.jobs.job_handlers import JobContext


def handle_repair_is_current(ctx: JobContext) -> tuple[dict, str]:
    from app.services.registry.sogc_person_extractor import run_repair_is_current

    batch_size = int(ctx.params.get("batch_size", 2000))

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        ctx.progress(
            done, total, _stats,
            f"Processing {done}/{total} entities — {len(_stats.get('errors', []))} errors",
        )

    stats = run_repair_is_current(
        ctx.db,
        batch_size=batch_size,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['entities_processed']} entities repaired, "
        f"{len(stats['errors'])} errors"
    )
    return stats, done_msg
