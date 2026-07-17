"""Job handlers for UID register import and detail fetch."""
from __future__ import annotations

from app import crud
from app.services.jobs.job_handlers import JobContext


def handle_uid_detail(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ingestion.uid_import import fetch_uid_details
    from sqlalchemy import text as _text

    # Refuse to run while uid_import is active — both hit the same rate-limited API.
    active_import = ctx.db.execute(
        _text(
            "SELECT id FROM job_runs WHERE job_type = 'uid_import' "
            "AND status IN ('queued', 'running') LIMIT 1"
        )
    ).fetchone()
    if active_import:
        raise RuntimeError(
            "uid_import is currently running (job_runs id=%d). "
            "uid_detail shares the same UID SOAP API rate limit — "
            "wait for uid_import to finish before starting uid_detail." % active_import[0]
        )

    batch_size = int(ctx.params.get("batch_size", 100))

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"GetByUID {done}/{total or '?'} — "
            f"{stats.get('updated', 0)} updated, "
            f"{stats.get('skipped_no_detail', 0)} no detail, "
            f"{stats.get('api_errors', 0)} errors"
        )
        ctx.progress(done, total or 0, stats, msg)

    stats = fetch_uid_details(
        ctx.db,
        resume_from=ctx.resume_from,
        batch_size=batch_size,
        progress_cb=_progress,
    )
    done_msg = (
        f"UID detail fetch done — {stats['updated']} address/legal-form updated, "
        f"{stats['skipped_no_detail']} not found in UID register, "
        f"{stats['api_errors']} API errors"
    )
    return stats, done_msg


def handle_uid_import(ctx: JobContext) -> tuple[dict, str]:
    """Name/keyword dictionary sweep, geo/status-refined (replaces prefix sweep).

    params: batch_size, not_in_hr (default True), mwst_only, refine_mwst, max_depth,
    min_token_freq, max_terms, second_token_count. resume_from = dictionary index.
    """
    from app.services.ingestion.uid_import import sweep_uid_gap

    batch_size = int(ctx.params.get("batch_size", 500))
    not_in_hr = bool(ctx.params.get("not_in_hr", True))
    mwst_only = bool(ctx.params.get("mwst_only", False))
    refine_mwst = bool(ctx.params.get("refine_mwst", True))
    max_depth = int(ctx.params.get("max_depth", 1))
    min_token_freq = int(ctx.params.get("min_token_freq", 2))
    max_terms = int(ctx.params.get("max_terms", 50000))
    second_token_count = int(ctx.params.get("second_token_count", 200))
    on_conflict = str(ctx.params.get("on_conflict", "update"))

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Term {done}/{total} ('{stats.get('current_token', '?')}') — "
            f"{stats.get('inserted', 0)} new, "
            f"{stats.get('updated_type', 0)} type-updated, "
            f"{stats.get('buckets_capped', 0)} refined, "
            f"{stats.get('api_errors', 0)} API errors"
        )
        ctx.progress(done, total or 0, stats, msg)

    _live = {"token": "", "calls": 0}

    def _ping() -> None:
        _live["calls"] += 1
        ctx._heartbeat()
        ctx.assert_not_cancelled()
        crud.update_progress(
            ctx.db, ctx.job,
            message=f"Term '{_live['token']}' — {_live['calls']} API calls",
        )

    def _status(token: str) -> None:
        _live["token"] = token
        _live["calls"] = 0
        crud.update_progress(ctx.db, ctx.job, message=f"Sweeping term '{token}'…")

    stats = sweep_uid_gap(
        ctx.db,
        resume_from=ctx.resume_from,
        batch_size=batch_size,
        not_in_hr=not_in_hr,
        mwst_only=mwst_only,
        refine_mwst=refine_mwst,
        max_depth=max_depth,
        min_token_freq=min_token_freq,
        max_terms=max_terms,
        second_token_count=second_token_count,
        on_conflict=on_conflict,
        progress_cb=_progress,
        abort_cb=_ping,
        status_cb=_status,
    )

    done_msg = (
        f"UID gap sweep done — {stats['inserted']} new companies, "
        f"{stats['updated_type']} uid-row updates, "
        f"{stats.get('skipped_existing', 0)} existing skipped, "
        f"{stats['buckets_capped']} capped buckets refined, "
        f"{stats['api_errors']} API errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from term index {ctx.resume_from})"
    return stats, done_msg
