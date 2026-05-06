"""Handlers for Zefix import, scoring, and geocoding jobs."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_re_geocode(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import re_geocode_all_companies

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"Geocoded {done}/{total} — {stats['geocoded']} updated, {stats['failed']} no match"
        ctx.progress(done, total, stats, msg)

    stats = re_geocode_all_companies(ctx.db, resume_from=ctx.resume_from, progress_cb=_progress)
    done_msg = (
        f"Done — {stats['geocoded']} geocoded, {stats['failed']} no match, "
        f"{len(stats['errors'])} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    from app import crud
    crud.set_setting(ctx.db, "geocoding_building_level_done", "true")
    return stats, done_msg


def handle_recalculate_scores(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import recalculate_flex_scores

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        phase = stats.get("_phase", "scoring")
        if phase == "writing":
            msg = f"Writing normalised scores — {done}/{total}"
        else:
            msg = f"Computing raw scores — {done}/{total} ({stats.get('geocoded', 0)} geocoded)"
        ctx.progress(done, total, stats, msg)

    stats = recalculate_flex_scores(ctx.db, org_id=ctx.job.org_id, resume_from=ctx.resume_from, progress_cb=_progress)
    done_msg = f"Done — {stats['updated']} recalculated, {stats.get('geocoded', 0)} geocoded, {len(stats['errors'])} errors"
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_recalculate_google_scores(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import recalculate_google_scores

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"Recalculated Google scores for {done}/{total} companies"
        ctx.progress(done, total, stats, msg)

    stats = recalculate_google_scores(ctx.db, resume_from=ctx.resume_from, progress_cb=_progress)
    done_msg = (
        f"Done — {stats['updated']} updated, {stats['skipped']} skipped, "
        f"{len(stats['errors'])} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_reextract_purpose(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import reextract_purpose_from_zefix_raw

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
            f"{stats.get('skipped_not_detailed', 0)} not-detailed, "
            f"{stats.get('skipped_existing', 0)} unchanged"
        )
        ctx.progress(done, total, stats, msg)

    stats = reextract_purpose_from_zefix_raw(
        ctx.db,
        resume_from=ctx.resume_from,
        only_missing_purpose=bool(ctx.params.get("only_missing_purpose", True)),
        progress_cb=_progress,
    )
    done_msg = (
        f"Done — {stats.get('updated', 0)} updated, "
        f"{stats.get('skipped_not_detailed', 0)} skipped (not detailed), "
        f"{stats.get('skipped_existing', 0)} skipped (existing), "
        f"{stats.get('skipped_empty_extracted', 0)} skipped (empty extraction), "
        f"{len(stats.get('errors', []))} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_bulk(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import bulk_import_zefix
    from app import crud

    def _progress(canton: str, prefix: str, created: int, updated: int) -> None:
        ctx.assert_not_cancelled()
        msg = f"Canton {canton} prefix {prefix} — {created} created, {updated} updated"
        stats_now = {"created": created, "updated": updated}
        crud.update_progress(ctx.db, ctx.job, message=msg, stats=stats_now)
        crud.create_event(ctx.db, job_id=ctx.job.id, level="info", message=msg)
        ctx.sync(msg, stats_now, False)
        ctx._heartbeat()

    stats = bulk_import_zefix(
        ctx.db,
        cantons=ctx.params.get("cantons"),
        active_only=ctx.params.get("active_only", True),
        request_delay=float(ctx.params.get("delay", 0.5)),
        resume=bool(ctx.params.get("resume", False)),
        start_from_canton=ctx.params.get("start_from_canton"),
        empty_abort_threshold=int(ctx.params.get("empty_abort_threshold", 100)),
        progress_cb=_progress,
        status_cb=lambda m: ctx.status(m),
        abort_cb=ctx.assert_not_cancelled,
    )
    return stats, f"Done — {stats['created']} created, {stats['updated']} updated, {len(stats['errors'])} errors"


def handle_batch(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import run_batch_collect

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"Processing {done}/{total} companies"
        ctx.progress(done, total, stats, msg)

    stats = run_batch_collect(
        ctx.db,
        limit=int(ctx.params.get("limit", 100)),
        only_missing_website=bool(ctx.params.get("only_missing_website", True)),
        refresh_zefix=bool(ctx.params.get("refresh_zefix", False)),
        run_google=bool(ctx.params.get("run_google", True)),
        resume_from=ctx.resume_from,
        progress_cb=_progress,
        canton=ctx.params.get("canton"),
        min_flex_score=ctx.params.get("min_zefix_score"),
        min_ai_score=ctx.params.get("min_claude_score"),
        purpose_keywords=ctx.params.get("purpose_keywords"),
        tfidf_cluster=ctx.params.get("tfidf_cluster"),
        review_status=ctx.params.get("review_status"),
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )
    done_msg = (
        f"Done — {stats['google_enriched']} enriched, "
        f"{stats['google_no_result']} no result, {len(stats['errors'])} errors"
    )
    if stats.get("warnings"):
        done_msg += f", {len(stats['warnings'])} warning(s)"
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_initial(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import initial_collect

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Collected {done}/{total} — {stats.get('created', 0)} created, "
            f"{stats.get('updated', 0)} updated"
        )
        ctx.progress(done, total, stats, msg)

    stats = initial_collect(
        ctx.db,
        names=ctx.params.get("names", []),
        uids=ctx.params.get("uids", []),
        canton=ctx.params.get("canton"),
        legal_form=ctx.params.get("legal_form"),
        active_only=bool(ctx.params.get("active_only", True)),
        run_google=bool(ctx.params.get("run_google", True)),
        resume_from=ctx.resume_from,
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )
    done_msg = (
        f"Done — {stats['created']} created, {stats['updated']} updated, "
        f"{stats['google_enriched']} enriched, {len(stats['errors'])} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_reextract_zefix_raw(ctx: JobContext) -> tuple[dict, str]:
    from app.services.zefix_import import reextract_zefix_raw_fields, REEXTRACTABLE_FIELDS

    fields = ctx.params.get("fields") or None
    if fields is not None:
        invalid = set(fields) - REEXTRACTABLE_FIELDS
        if invalid:
            raise ValueError(f"Unknown fields: {sorted(invalid)}. Valid: {sorted(REEXTRACTABLE_FIELDS)}")

    ids = ctx.params.get("ids") or None
    mode = ctx.params.get("mode", "missing")
    if mode not in ("missing", "all"):
        raise ValueError(f"mode must be 'missing' or 'all', got {mode!r}")

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Re-extracted {done}/{total} — "
            f"{stats.get('updated', 0)} updated, "
            f"{stats.get('skipped_no_raw', 0)} no raw, "
            f"{len(stats.get('errors', []))} errors"
        )
        ctx.progress_no_event(done, total, stats, msg)

    stats = reextract_zefix_raw_fields(
        ctx.db,
        fields=fields,
        ids=ids,
        mode=mode,
        resume_from=ctx.resume_from,
        progress_cb=_progress,
        abort_cb=ctx.assert_not_cancelled,
    )
    done_msg = (
        f"Done — {stats.get('updated', 0)} updated, "
        f"{stats.get('skipped_no_raw', 0)} skipped (no raw), "
        f"{len(stats.get('errors', []))} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_detail(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import run_zefix_detail_collect

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"Processing {done}/{total}"
        ctx.progress(done, total, stats, msg)

    stats = run_zefix_detail_collect(
        ctx.db,
        cantons=ctx.params.get("cantons"),
        uids=ctx.params.get("uids"),
        score_if_missing=bool(ctx.params.get("score_if_missing", True)),
        only_missing_details=bool(ctx.params.get("only_missing_details", False)),
        resume_from=ctx.resume_from,
        request_delay=float(ctx.params.get("delay", 0.3)),
        progress_cb=_progress,
        status_cb=lambda m: ctx.status_with_stats(m),
        abort_cb=ctx.assert_not_cancelled,
    )
    done_msg = f"Done — {stats['updated']} updated, {stats['scored']} scored, {stats.get('geocoded', 0)} geocoded, {len(stats['errors'])} errors"
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg
