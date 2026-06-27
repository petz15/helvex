"""Job handlers for UID register import and detail fetch."""
from __future__ import annotations

from app import crud
from app.services.job_handlers import JobContext


def handle_uid_detail(ctx: JobContext) -> tuple[dict, str]:
    from app.services.uid_import import fetch_uid_details
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
    from app.services.uid_import import import_uid_entities

    batch_size = int(ctx.params.get("batch_size", 500))
    active_only = bool(ctx.params.get("active_only", False))

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        prefix = stats.get("current_prefix", "?")
        msg = (
            f"Pair {done}/{total} ('{prefix}') — "
            f"{stats.get('inserted', 0)} new, "
            f"{stats.get('updated_type', 0)} type-updated, "
            f"{stats.get('api_errors', 0)} API errors"
        )
        ctx.progress(done, total or 0, stats, msg)

    _live = {"prefix": "", "calls": 0}

    def _ping() -> None:
        _live["calls"] += 1
        ctx._heartbeat()
        ctx.assert_not_cancelled()
        crud.update_progress(
            ctx.db, ctx.job,
            message=f"Sweeping '{_live['prefix']}' — {_live['calls']} API calls",
        )

    def _status(prefix: str) -> None:
        _live["prefix"] = prefix
        _live["calls"] = 0
        crud.update_progress(
            ctx.db, ctx.job,
            message=f"Sweeping prefix '{prefix}'…",
        )

    stats = import_uid_entities(
        ctx.db,
        resume_from=ctx.resume_from,
        batch_size=batch_size,
        active_only=active_only,
        progress_cb=_progress,
        abort_cb=_ping,
        status_cb=_status,
    )

    done_msg = (
        f"UID import done — {stats['inserted']} new companies, "
        f"{stats['updated_type']} registration_type updates, "
        f"{stats['skipped_invalid']} skipped (no UID), "
        f"{stats['api_errors']} API errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from pair index {ctx.resume_from})"
    return stats, done_msg
