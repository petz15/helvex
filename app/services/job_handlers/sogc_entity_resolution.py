"""Handler for resolve_bisher_links job."""
from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_resolve_bisher_links(ctx: JobContext) -> tuple[dict, str]:
    from app.services.sogc_entity_resolver import run_resolve_bisher_links

    batch_size = int(ctx.params.get("batch_size", 500))

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processing {done}/{total} — "
            f"{_stats.get('hard_links_found', 0)} hard links, "
            f"{_stats.get('entities_merged', 0)} merged, "
            f"{len(_stats.get('errors', []))} errors"
        )
        ctx.progress(done, total, _stats, msg)

    stats = run_resolve_bisher_links(
        ctx.db,
        batch_size=batch_size,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['hard_links_found']} hard links found, "
        f"{stats['entities_merged']} entities merged, "
        f"{len(stats['errors'])} errors"
    )
    return stats, done_msg
