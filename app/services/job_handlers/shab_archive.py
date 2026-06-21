"""Handlers for shab_archive and link_sogc_stubs job types."""

from __future__ import annotations

from app.services.job_handlers import JobContext

# Dates before this use the daily bulk-PDF endpoint; at/after use the per-publication API.
_OLD_PDF_CUTOFF = "2012-12-01"


def handle_shab_archive(ctx: JobContext) -> tuple[dict, str]:
    date_start = ctx.params.get("date_start")
    date_end = ctx.params.get("date_end")
    mode = ctx.params.get("mode", "auto")  # "auto" | "old_pdf" | "api"

    # Determine which mode(s) to use
    use_old_pdf = mode == "old_pdf" or (
        mode == "auto" and date_start is not None and date_start < _OLD_PDF_CUTOFF
    )
    use_api = mode == "api" or (
        mode == "auto" and not (use_old_pdf and (date_end is None or date_end < _OLD_PDF_CUTOFF))
    )

    resume_from = ctx.resume_from or 0

    # ── Old bulk-PDF mode ─────────────────────────────────────────────────────
    if use_old_pdf:
        from app.services.shab_archive_import import import_shab_old_pdfs

        old_end = date_end if (date_end and date_end < _OLD_PDF_CUTOFF) else _OLD_PDF_CUTOFF
        request_delay = float(ctx.params.get("request_delay", 1.0))

        def _old_progress(done: int, total: int, _stats: dict) -> None:
            ctx.assert_not_cancelled()
            msg = (
                f"Day {done}/{total} — "
                f"{_stats.get('pdfs_downloaded', 0)} PDFs, "
                f"{_stats.get('entries_parsed', 0)} entries, "
                f"{_stats.get('created', 0)} new, "
                f"{_stats.get('updated', 0)} updated"
                + (f", {len(_stats.get('errors', []))} errors" if _stats.get('errors') else "")
            )
            ctx.progress(done, total, _stats, msg)

        old_stats = import_shab_old_pdfs(
            ctx.db,
            date_start=date_start,
            date_end=old_end,
            request_delay=request_delay,
            resume_from=resume_from,
            job_run_id=ctx.job.id,
            progress_cb=_old_progress,
            status_cb=lambda m: ctx.status_with_stats(m),
            abort_cb=ctx.assert_not_cancelled,
        )

        if not use_api or (date_end and date_end < _OLD_PDF_CUTOFF):
            done_msg = (
                f"Done (old-PDF) — "
                f"{old_stats['pdfs_downloaded']} PDFs, "
                f"{old_stats['entries_parsed']} entries parsed, "
                f"{old_stats['created']} created, "
                f"{old_stats['updated']} updated"
            )
            if old_stats.get("days_skipped"):
                done_msg += f", {old_stats['days_skipped']} days skipped"
            if old_stats["errors"]:
                done_msg += f", {len(old_stats['errors'])} errors"
            return old_stats, done_msg

    # ── Modern per-publication API mode ───────────────────────────────────────
    if use_api:
        from app.services.shab_archive_import import import_shab_archive

        api_start = _OLD_PDF_CUTOFF if use_old_pdf else date_start
        window_days = int(ctx.params.get("window_days", 28))
        start_page = int(ctx.params.get("start_page", 0))
        end_page_raw = ctx.params.get("end_page")
        end_page = int(end_page_raw) if end_page_raw is not None else None
        page_size = int(ctx.params.get("page_size", 100))
        request_delay = float(ctx.params.get("request_delay", 0.3))
        pdf_delay = float(ctx.params.get("pdf_delay", 0.0))
        pdf_workers = int(ctx.params.get("pdf_workers", 8))

        def _api_progress(done: int, total: int, _stats: dict) -> None:
            ctx.assert_not_cancelled()
            if api_start:
                msg = (
                    f"Window {done}/{total} — "
                    f"{_stats.get('created', 0)} new, "
                    f"{_stats.get('updated', 0)} updated, "
                    f"{_stats.get('pdfs_downloaded', 0)} PDFs"
                    + (f", {_stats.get('pdf_text_empty', 0)} no-text" if _stats.get('pdf_text_empty') else "")
                    + (f", {len(_stats.get('errors', []))} errors" if _stats.get('errors') else "")
                )
            else:
                msg = (
                    f"Page {done}/{total} — "
                    f"{_stats.get('hr_entries', 0)} HR, "
                    f"{_stats.get('created', 0)} new, "
                    f"{_stats.get('updated', 0)} updated, "
                    f"{_stats.get('pdfs_downloaded', 0)} PDFs"
                    + (f", {_stats.get('pdf_text_empty', 0)} no-text" if _stats.get('pdf_text_empty') else "")
                )
            ctx.progress(done, total, _stats, msg)

        api_stats = import_shab_archive(
            ctx.db,
            date_start=api_start,
            date_end=date_end,
            window_days=window_days,
            start_page=start_page,
            end_page=end_page,
            page_size=page_size,
            request_delay=request_delay,
            pdf_delay=pdf_delay,
            pdf_workers=pdf_workers,
            resume_from=resume_from,
            progress_cb=_api_progress,
            status_cb=lambda m: ctx.status_with_stats(m),
            abort_cb=ctx.assert_not_cancelled,
        )

        api_mode = "date-window" if api_start else "page"
        done_msg = (
            f"Done ({api_mode}) — "
            f"{api_stats['pages_fetched']} pages, "
            f"{api_stats['pdfs_downloaded']} PDFs, "
            f"{api_stats['created']} created, "
            f"{api_stats['updated']} updated, "
            f"{api_stats['skipped']} skipped"
        )
        if api_stats.get("pdf_text_empty"):
            done_msg += f", {api_stats['pdf_text_empty']} no-text PDFs"
        if api_stats["errors"]:
            done_msg += f", {len(api_stats['errors'])} errors"
        if api_stats.get("warnings"):
            done_msg += f", {len(api_stats['warnings'])} warnings"
        if resume_from:
            done_msg += f" (resumed from {resume_from})"
        return api_stats, done_msg

    return {}, "No mode selected"


def handle_link_sogc_stubs(ctx: JobContext) -> tuple[dict, str]:
    from app.services.shab_archive_import import run_link_sogc_stubs

    batch_size = int(ctx.params.get("batch_size", 500))

    def _progress(done: int, total: int, _stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"{done:,}/{total:,} UIDs — "
            f"{_stats.get('stubs_created', 0)} stubs, "
            f"{_stats.get('publications_linked', 0)} pubs linked, "
            f"{_stats.get('appearances_linked', 0)} appearances linked"
            + (f", {len(_stats.get('errors', []))} errors" if _stats.get('errors') else "")
        )
        ctx.progress(done, total, _stats, msg)

    stats = run_link_sogc_stubs(
        ctx.db,
        batch_size=batch_size,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )

    done_msg = (
        f"Done — {stats['uids_scanned']:,} UIDs scanned, "
        f"{stats['stubs_created']} stubs created, "
        f"{stats['publications_linked']:,} publications linked, "
        f"{stats['appearances_linked']:,} appearances linked"
    )
    if stats["errors"]:
        done_msg += f", {len(stats['errors'])} errors"
    return stats, done_msg
