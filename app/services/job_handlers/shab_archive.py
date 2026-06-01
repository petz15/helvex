"""Handler for the shab_archive job type."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_shab_archive(ctx: JobContext) -> tuple[dict, str]:
    from app.services.shab_archive_import import import_shab_archive

    # Date-window mode params
    date_start = ctx.params.get("date_start")
    date_end = ctx.params.get("date_end")
    window_days = int(ctx.params.get("window_days", 28))

    # Page mode params (used when no dates given)
    start_page = int(ctx.params.get("start_page", 0))
    end_page_raw = ctx.params.get("end_page")
    end_page = int(end_page_raw) if end_page_raw is not None else None

    page_size = int(ctx.params.get("page_size", 100))
    request_delay = float(ctx.params.get("request_delay", 0.5))
    pdf_delay = float(ctx.params.get("pdf_delay", 0.3))

    # resume_from is stored as progress_done (window count or page number)
    resume_from = ctx.resume_from or 0

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        if date_start:
            msg = (
                f"Window {done}/{total} — "
                f"{_stats.get('created', 0)} new, "
                f"{_stats.get('updated', 0)} updated, "
                f"{_stats.get('pdfs_downloaded', 0)} PDFs, "
                f"{len(_stats.get('errors', []))} errors"
            )
        else:
            msg = (
                f"Page {done}/{total} — "
                f"{_stats.get('hr_entries', 0)} HR, "
                f"{_stats.get('created', 0)} new, "
                f"{_stats.get('updated', 0)} updated, "
                f"{_stats.get('pdfs_downloaded', 0)} PDFs"
            )
        ctx.progress(done, total, _stats, msg)

    stats = import_shab_archive(
        ctx.db,
        date_start=date_start,
        date_end=date_end,
        window_days=window_days,
        start_page=start_page,
        end_page=end_page,
        page_size=page_size,
        request_delay=request_delay,
        pdf_delay=pdf_delay,
        resume_from=resume_from,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    mode = "date-window" if date_start else "page"
    done_msg = (
        f"Done ({mode}) — "
        f"{stats['pages_fetched']} pages, "
        f"{stats['pdfs_downloaded']} PDFs, "
        f"{stats['created']} created, "
        f"{stats['updated']} updated, "
        f"{stats['skipped']} skipped"
    )
    if stats["errors"]:
        done_msg += f", {len(stats['errors'])} errors"
    if stats.get("warnings"):
        done_msg += f", {len(stats['warnings'])} warnings"
    if resume_from:
        done_msg += f" (resumed from {resume_from})"
    return stats, done_msg
