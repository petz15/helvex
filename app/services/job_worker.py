"""Background job worker.

A daemon thread polls the DB queue and runs jobs sequentially in-process.
Zero external dependencies. `enqueue_job()` is the only public entry point
used by REST routes.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import traceback

from app import crud
from app.database import SessionLocal
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Graceful shutdown ──────────────────────────────────────────────────────────

_shutdown_requested: bool = False


def request_shutdown() -> None:
    """Signal all running jobs to pause at their next progress checkpoint.

    Called from app/main.py lifespan shutdown.
    """
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Graceful shutdown requested — running jobs will pause at next checkpoint")


class JobCancelledError(Exception):
    """Raised when a running job receives a cancellation request."""


class JobPausedError(Exception):
    """Raised when a running job receives a pause request."""


class JobEnqueueError(RuntimeError):
    """Raised when a job cannot be enqueued or processed due to configuration."""


class _JobWaitingExternalSignal(Exception):
    """Internal signal: job transitioned to waiting_external — skip mark_completed."""



def _compute_dedup_key(job_type: str, org_id: int | None, params: dict) -> str | None:
    """Return a dedup key for this job, or None if dedup is not enforced.

    At most one *active* job (queued/running/paused/waiting_external) with the
    same key is allowed per org.  Attempting to enqueue a duplicate returns the
    existing job without charging credits.
    """
    # These types allow at most one active job per org at a time.
    ONE_PER_ORG = {
        "bulk", "detail", "initial",
        "shab_daily", "shab_backfill",
        "recalculate_scores", "recalculate_google_scores",
        "reextract_purpose", "reextract_zefix_raw", "reclassify_noga",
        "build_noga_embeddings", "detect_language_bulk", "reclassify_low_conf_noga",
        "re_geocode",
        "tfidf_kmeans_cluster",
        "recompute_keywords", "reextract_keywords",
        "cluster_analysis", "discover_stopwords",
        "sogc_preprocess",
        "extract_sogc_persons",
        "saved_view_alerts",  # global singleton — org_id=None gives key "saved_view_alerts:None"
    }
    # No dedup: every trigger creates a fresh independent job.
    NO_DEDUP = {"batch", "csv_export"}

    if job_type in ("noga_explain", "noga_test"):
        company_id = params.get("company_id")
        return f"{job_type}:{company_id}" if company_id is not None else None

    if job_type in NO_DEDUP:
        return None
    if job_type in ONE_PER_ORG:
        return f"{job_type}:{org_id}"
    if job_type == "claude_classify":
        import hashlib as _hashlib
        relevant = {
            k: params.get(k)
            for k in (
                "use_fixed_categories", "system_prompt", "canton",
                "min_zefix_score", "max_zefix_score", "min_google_score",
                "purpose_keywords",
            )
        }
        h = _hashlib.sha256(
            json.dumps(relevant, sort_keys=True).encode()
        ).hexdigest()[:16]
        return f"claude_classify:{org_id}:{h}"
    return None


def _publish_job_update(org_id: int | None) -> None:
    pass


def _heartbeat() -> None:
    pass


def _preflight_job(db: Session, *, job_type: str, params: dict) -> tuple[dict, list[str]]:
    """Validate prerequisites for a job and optionally rewrite params.

    Returns:
        (new_params, warnings)

    Raises:
        ValueError: If the job cannot run as requested.
    """
    warnings: list[str] = []
    new_params = dict(params or {})

    if job_type in {"batch", "initial"}:
        if bool(new_params.get("run_google", True)):
            from app.services.collection import _google_search_ready

            ok, reason = _google_search_ready(db)
            if not ok:
                warnings.append(reason or "Google enrichment is not ready")
                new_params["run_google"] = False

    if job_type == "claude_classify":
        from app.config import settings as app_settings

        _preflight_org_id = new_params.get("org_id") or None
        api_key = (
            crud.get_effective_setting(db, "anthropic_api_key", org_id=_preflight_org_id, default="") or ""
        ).strip() or app_settings.anthropic_api_key
        if not (api_key or "").strip():
            raise ValueError(
                "Anthropic API key missing: set ANTHROPIC_API_KEY env var or configure it in your workspace settings"
            )

    return new_params, warnings


def _resolve_credit_action_and_count(db: Session, *, job_type: str, params: dict) -> tuple[str, int] | None:
    """Map a queued job to (credit_action, count) for deduction.

    Returns None when the job type is not credit-metered.
    """
    if job_type == "claude_classify":
        action = "batch_llm" if bool(params.get("use_batch_api", False)) else "immediate_llm"
        return action, max(1, int(params.get("limit") or 500))

    if job_type in {"batch", "initial"}:
        if not bool(params.get("run_google", True)):
            return None
        if job_type == "batch":
            return "web_search", max(1, int(params.get("limit") or 100))
        names = params.get("names") or []
        uids = params.get("uids") or []
        return "web_search", max(1, len(names) + len(uids))

    if job_type == "recalculate_scores":
        return "flex_rescore", max(1, int(crud.count_companies(db)))

    if job_type == "tfidf_kmeans_cluster":
        return "recluster", 1

    if job_type == "csv_export":
        filters = dict(params or {})
        filters.pop("sort", None)
        filters["name_filter"] = filters.pop("q", None)
        filters["uid_filter"] = filters.pop("uid", None)
        rows = crud.count_companies(
            db,
            **{k: v for k, v in filters.items() if v is not None},
        )
        units = max(1, math.ceil(max(1, int(rows)) / 10_000))
        return "bulk_export_basic", units

    return None


def _apply_credit_deduction_if_needed(
    db: Session,
    *,
    job_type: str,
    params: dict,
    org_id: int | None,
    user_id: int | None,
) -> tuple[str, int] | None:
    """Deduct enqueue-time credits for credit-metered actions.

    Returns (action, count) so the caller can store it for potential refund,
    or None when no deduction was made.

    Superadmins and org-less jobs bypass checks.
    """
    if org_id is None:
        return None

    if user_id is not None:
        user = crud.get_user(db, user_id)
        if user is not None and bool(user.is_superadmin):
            return None

    action_count = _resolve_credit_action_and_count(db, job_type=job_type, params=params)
    if action_count is None:
        return None

    from app.services.credits import check_and_deduct

    action, count = action_count
    ok = check_and_deduct(
        db,
        org_id=org_id,
        action=action,
        count=count,
        reference_id=f"enqueue:{job_type}",
    )
    if not ok:
        raise ValueError(f"Insufficient credits for {action} (required units: {count})")
    return action, count


def _refund_job_credits_if_needed(
    db: Session,
    *,
    job: "JobRun",  # type: ignore[name-defined]
    reason: str,
) -> None:
    """Issue a credit refund for a job that was charged at enqueue but never completed.

    Reads the deduction amount from `job.stats_json` (key: `_credit_deduction`).
    No-ops when: no deduction recorded, org-less job, or org has unlimited credits.
    """
    if not job.org_id or not job.stats_json:
        return
    try:
        stored = json.loads(job.stats_json)
        deduction = stored.get("_credit_deduction")
        if not deduction:
            return
        action = deduction["action"]
        cost = int(deduction["cost"])
        if cost <= 0:
            return
    except (KeyError, ValueError, TypeError):
        return

    from app.services.credits import grant_credits
    from app.models.organization import Organization

    org = db.get(Organization, job.org_id)
    if org is None or org.credits_unlimited:
        return

    try:
        grant_credits(
            db,
            org_id=job.org_id,
            amount=cost,
            tx_type="refund",
            action_type=action,
            reference_id=f"refund:job:{job.id}",
        )
        logger.info(
            "credit_refund job_id=%d org_id=%d action=%s cost=%d reason=%s",
            job.id, job.org_id, action, cost, reason,
        )
    except Exception as _e:  # noqa: BLE001
        logger.warning("credit_refund_failed job_id=%d error=%s", job.id, _e)


# ── Internal state helpers ─────────────────────────────────────────────────────

def _sync_active_task(
    app_state,
    *,
    job_type: str,
    label: str,
    message: str,
    stats: dict,
    error: str | None,
    done: bool,
) -> None:
    app_state.collection_task = {
        "type": job_type,
        "label": label,
        "started_at": time.time(),
        "message": message,
        "stats": stats,
        "error": error,
        "done": done,
    }


def _maybe_sync(app, **kwargs) -> None:
    """Call _sync_active_task only when running inside the web process (app != None)."""
    if app is not None:
        _sync_active_task(app.state, **kwargs)


# ── Job runner ─────────────────────────────────────────────────────────────────

def _run_job(app, job_id: int) -> None:  # noqa: C901
    """Execute one job. `app` may be None when called from an RQ worker."""
    from app.metrics import record_job_duration, record_job_error, ACTIVE_JOBS
    
    job_start_time = time.monotonic()

    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        if not job:
            return

        # Guard against double-dispatch: another worker may have already picked this up
        if job.status not in ("queued", "paused"):
            return

        if job.status == "cancelled" or job.cancel_requested:
            _refund_job_credits_if_needed(db, job=job, reason="cancelled_before_start")
            crud.mark_cancelled(db, job, message="Cancelled before start")
            crud.create_event(db, job_id=job.id, level="info", message="Job cancelled before execution started")
            duration = time.monotonic() - job_start_time
            record_job_duration(job.job_type, duration, "cancelled")
            return

        crud.mark_running(db, job, message="Starting…")
        crud.create_event(db, job_id=job.id, level="info", message="Job started")
        _publish_job_update(job.org_id)

        # Heartbeat daemon: stamps last_heartbeat_at every 30 s so that
        # requeue_interrupted_jobs() can tell this job is still alive and
        # must NOT be re-queued when the web pod restarts.
        _hb_stop = threading.Event()

        def _hb_daemon() -> None:
            while not _hb_stop.wait(30):
                try:
                    with SessionLocal() as _hb_db:
                        crud.update_heartbeat(_hb_db, job_id)
                except Exception:  # noqa: BLE001
                    pass

        _hb_thread = threading.Thread(target=_hb_daemon, daemon=True, name=f"hb-job-{job_id}")
        _hb_thread.start()

        if app is not None:
            _sync_active_task(
                app.state,
                job_type=job.job_type,
                label=job.label,
                message="Starting…",
                stats={},
                error=None,
                done=False,
            )

        params = json.loads(job.params_json or "{}")
        resume_from = max(0, int(job.progress_done or 0))

        def _assert_not_cancelled() -> None:
            db.refresh(job)
            if job.cancel_requested:
                raise JobCancelledError("Cancellation requested")
            if job.pause_requested:
                raise JobPausedError("Pause requested")
            if _shutdown_requested:
                raise JobPausedError("Worker shutdown — job paused for restart")

        try:
            if job.job_type == "re_geocode":
                from app.services.collection import re_geocode_all_companies

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Geocoded {done}/{total} — {stats['geocoded']} updated, {stats['failed']} no match"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = re_geocode_all_companies(db, resume_from=resume_from, progress_cb=_progress)
                done_msg = (
                    f"Done — {stats['geocoded']} geocoded, {stats['failed']} no match, "
                    f"{len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"
                crud.set_setting(db, "geocoding_building_level_done", "true")

            elif job.job_type == "recalculate_scores":
                from app.services.collection import recalculate_flex_scores

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    phase = stats.get("_phase", "scoring")
                    if phase == "writing":
                        msg = f"Writing normalised scores — {done}/{total}"
                    else:
                        msg = f"Computing raw scores — {done}/{total} ({stats.get('geocoded', 0)} geocoded)"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = recalculate_flex_scores(db, org_id=job.org_id, resume_from=resume_from, progress_cb=_progress)
                done_msg = f"Done — {stats['updated']} recalculated, {stats.get('geocoded', 0)} geocoded, {len(stats['errors'])} errors"
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "recalculate_google_scores":
                from app.services.collection import recalculate_google_scores

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Recalculated Google scores for {done}/{total} companies"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = recalculate_google_scores(db, resume_from=resume_from, progress_cb=_progress)
                done_msg = (
                    f"Done — {stats['updated']} updated, {stats['skipped']} skipped, "
                    f"{len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "reextract_purpose":
                from app.services.collection import reextract_purpose_from_zefix_raw

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
                        f"{stats.get('skipped_not_detailed', 0)} not-detailed, "
                        f"{stats.get('skipped_existing', 0)} unchanged"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = reextract_purpose_from_zefix_raw(
                    db,
                    resume_from=resume_from,
                    only_missing_purpose=bool(params.get("only_missing_purpose", True)),
                    progress_cb=_progress,
                )
                done_msg = (
                    f"Done — {stats.get('updated', 0)} updated, "
                    f"{stats.get('skipped_not_detailed', 0)} skipped (not detailed), "
                    f"{stats.get('skipped_existing', 0)} skipped (existing), "
                    f"{stats.get('skipped_empty_extracted', 0)} skipped (empty extraction), "
                    f"{len(stats.get('errors', []))} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "reextract_zefix_raw":
                from app.services.zefix_import import reextract_zefix_raw_fields, REEXTRACTABLE_FIELDS

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
                        f"{stats.get('skipped_no_raw', 0)} skipped (no raw), "
                        f"{len(stats.get('errors', []))} errors"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                raw_fields = params.get("fields") or None
                raw_ids = params.get("ids") or None
                mode = params.get("mode", "missing")
                stats = reextract_zefix_raw_fields(
                    db,
                    fields=raw_fields,
                    ids=raw_ids,
                    mode=mode,
                    resume_from=resume_from,
                    progress_cb=_progress,
                    abort_cb=_assert_not_cancelled,
                )
                done_msg = (
                    f"Done — {stats.get('updated', 0)} updated, "
                    f"{stats.get('skipped_no_raw', 0)} skipped (no raw), "
                    f"{len(stats.get('errors', []))} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "reclassify_noga":
                from app.services.collection import reclassify_noga

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
                        f"{stats.get('skipped_no_match', 0)} no match, "
                        f"{stats.get('skipped_not_detailed', 0)} not-detailed"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = reclassify_noga(
                    db,
                    resume_from=resume_from,
                    only_missing_noga=bool(params.get("only_missing_noga", False)),
                    include_stale=bool(params.get("include_stale", False)),
                    only_detailed_raw=bool(params.get("only_detailed_raw", True)),
                    progress_cb=_progress,
                )
                done_msg = (
                    f"Done — {stats.get('updated', 0)} reclassified, "
                    f"{stats.get('skipped_existing', 0)} skipped existing, "
                    f"{stats.get('skipped_not_detailed', 0)} skipped not-detailed, "
                    f"{stats.get('skipped_no_match', 0)} skipped no-match, "
                    f"{len(stats.get('errors', []))} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "build_noga_embeddings":
                from scripts.build_noga_embeddings_pg import run as _build_noga_emb

                batch_size = int(params.get("batch_size", 256))
                crud.update_progress(db, job, message="Embedding NOGA taxonomy…", done=0, total=1, stats={})
                _build_noga_emb(batch_size=batch_size, dry_run=False)
                stats = {"batch_size": batch_size}
                done_msg = "NOGA embeddings built and stored in pgvector"

            elif job.job_type == "detect_language_bulk":
                from app.services.collection import detect_language_bulk

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
                        f"{stats.get('skipped_no_purpose', 0)} no purpose"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    _heartbeat()

                stats = detect_language_bulk(
                    db,
                    only_missing=bool(params.get("only_missing", True)),
                    progress_cb=_progress,
                )
                done_msg = (
                    f"Done — {stats.get('updated', 0)} languages detected, "
                    f"{stats.get('skipped_existing', 0)} skipped existing, "
                    f"{len(stats.get('errors', []))} errors"
                )

            elif job.job_type == "reclassify_low_conf_noga":
                from app.services.collection import reclassify_low_confidence_noga

                threshold = float(params.get("confidence_threshold", 0.80))

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processed {done}/{total} — {stats.get('improved', 0)} improved, "
                        f"{stats.get('still_low', 0)} still low confidence"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    _heartbeat()

                stats = reclassify_low_confidence_noga(
                    db,
                    confidence_threshold=threshold,
                    progress_cb=_progress,
                )
                done_msg = (
                    f"Done — {stats.get('updated', 0)} reclassified, "
                    f"{stats.get('improved', 0)} now above threshold, "
                    f"{stats.get('still_low', 0)} still low, "
                    f"{len(stats.get('errors', []))} errors"
                )

            elif job.job_type == "discover_stopwords":
                from app.services.stopword_discovery import discover_stopwords
                from app.config import settings as _dsettings

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Phase {done}/{total} complete"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    _heartbeat()

                use_ai = bool(params.get("use_ai", False))
                api_key = _dsettings.anthropic_api_key if use_ai else None
                stats = discover_stopwords(db, use_ai=use_ai, anthropic_api_key=api_key, progress_cb=_progress)
                done_msg = (
                    f"Done — phase1:{stats['phase1_stopwords_staged']} staged, "
                    f"phase2:{stats['phase2_boilerplate_staged']} boilerplate, "
                    f"phase3:{stats['phase3_stopwords_staged']} cross-cluster, "
                    f"phase4:{stats['phase4_auto_approved']} auto-approved"
                )

            elif job.job_type == "analyze_boilerplate":
                from app.services.boilerplate_analysis import run_boilerplate_analysis

                def _progress(done: int, total: int, bstats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Scanned {done}/{total} purposes — "
                        f"{bstats.get('unique_sentences', 0)} unique sentences found"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=bstats)
                    _heartbeat()

                stats = run_boilerplate_analysis(
                    db,
                    min_match_count=params.get("min_match_count") or 500,
                    max_candidates=params.get("max_candidates") or 200,
                    sample_limit=params.get("sample_limit") or 200_000,
                    progress_cb=_progress,
                )
                done_msg = (
                    f"Done — {stats['total_purposes_scanned']} purposes scanned, "
                    f"{stats['candidates_found']} candidates, "
                    f"{stats['new_patterns_saved']} new inactive patterns saved for review"
                )

            elif job.job_type == "bulk":
                from app.services.collection import bulk_import_zefix

                def _progress(canton: str, prefix: str, created: int, updated: int) -> None:
                    _assert_not_cancelled()
                    msg = f"Canton {canton} prefix {prefix} — {created} created, {updated} updated"
                    stats_now = {"created": created, "updated": updated}
                    crud.update_progress(db, job, message=msg, stats=stats_now)
                    crud.create_event(db, job_id=job.id, level="info", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=stats_now, error=None, done=False)
                    _heartbeat()

                stats = bulk_import_zefix(
                    db,
                    cantons=params.get("cantons"),
                    active_only=params.get("active_only", True),
                    request_delay=float(params.get("delay", 0.5)),
                    resume=bool(params.get("resume", False)),
                    start_from_canton=params.get("start_from_canton"),
                    empty_abort_threshold=int(params.get("empty_abort_threshold", 100)),
                    progress_cb=_progress,
                    status_cb=lambda m: (
                        _assert_not_cancelled(),
                        crud.update_progress(db, job, message=str(m)),
                        crud.create_event(db, job_id=job.id, level="info", message=str(m)),
                        _maybe_sync(app, job_type=job.job_type, label=job.label, message=str(m), stats={}, error=None, done=False),
                    ),
                    abort_cb=_assert_not_cancelled,
                )
                done_msg = f"Done — {stats['created']} created, {stats['updated']} updated, {len(stats['errors'])} errors"

            elif job.job_type == "batch":
                from app.services.collection import run_batch_collect

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Processing {done}/{total} companies"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = run_batch_collect(
                    db,
                    limit=int(params.get("limit", 100)),
                    only_missing_website=bool(params.get("only_missing_website", True)),
                    refresh_zefix=bool(params.get("refresh_zefix", False)),
                    run_google=bool(params.get("run_google", True)),
                    resume_from=resume_from,
                    progress_cb=_progress,
                    canton=params.get("canton"),
                    min_flex_score=params.get("min_zefix_score"),
                    min_ai_score=params.get("min_claude_score"),
                    purpose_keywords=params.get("purpose_keywords"),
                    tfidf_cluster=params.get("tfidf_cluster"),
                    review_status=params.get("review_status"),
                    status_cb=lambda m: (
                        _assert_not_cancelled(),
                        crud.update_progress(db, job, message=str(m)),
                        crud.create_event(db, job_id=job.id, level="info", message=str(m)),
                        _maybe_sync(app, job_type=job.job_type, label=job.label, message=str(m), stats=json.loads(job.stats_json) if job.stats_json else {}, error=None, done=False),
                    ),
                    abort_cb=_assert_not_cancelled,
                )
                done_msg = (
                    f"Done — {stats['google_enriched']} enriched, "
                    f"{stats['google_no_result']} no result, {len(stats['errors'])} errors"
                )
                if stats.get("warnings"):
                    done_msg += f", {len(stats['warnings'])} warning(s)"
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "initial":
                from app.services.collection import initial_collect

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Collected {done}/{total} — {stats.get('created', 0)} created, "
                        f"{stats.get('updated', 0)} updated"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = initial_collect(
                    db,
                    names=params.get("names", []),
                    uids=params.get("uids", []),
                    canton=params.get("canton"),
                    legal_form=params.get("legal_form"),
                    active_only=bool(params.get("active_only", True)),
                    run_google=bool(params.get("run_google", True)),
                    resume_from=resume_from,
                    progress_cb=_progress,
                    status_cb=lambda m: (
                        _assert_not_cancelled(),
                        crud.update_progress(db, job, message=str(m)),
                        crud.create_event(db, job_id=job.id, level="info", message=str(m)),
                        _maybe_sync(app, job_type=job.job_type, label=job.label, message=str(m), stats=json.loads(job.stats_json) if job.stats_json else {}, error=None, done=False),
                    ),
                    abort_cb=_assert_not_cancelled,
                )
                done_msg = (
                    f"Done — {stats['created']} created, {stats['updated']} updated, "
                    f"{stats['google_enriched']} enriched, {len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "detail":
                from app.services.collection import run_zefix_detail_collect

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Processing {done}/{total}"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = run_zefix_detail_collect(
                    db,
                    cantons=params.get("cantons"),
                    uids=params.get("uids"),
                    score_if_missing=bool(params.get("score_if_missing", True)),
                    only_missing_details=bool(params.get("only_missing_details", False)),
                    resume_from=resume_from,
                    request_delay=float(params.get("delay", 0.3)),
                    progress_cb=_progress,
                    status_cb=lambda m: (
                        _assert_not_cancelled(),
                        crud.update_progress(db, job, message=str(m)),
                        crud.create_event(db, job_id=job.id, level="info", message=str(m)),
                        _maybe_sync(app, job_type=job.job_type, label=job.label, message=str(m), stats=json.loads(job.stats_json) if job.stats_json else {}, error=None, done=False),
                    ),
                    abort_cb=_assert_not_cancelled,
                )
                done_msg = f"Done — {stats['updated']} updated, {stats['scored']} scored, {stats.get('geocoded', 0)} geocoded, {len(stats['errors'])} errors"
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "tfidf_kmeans_cluster":
                from app.services.cluster_pipeline import PipelineConfig, run_pipeline

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    step = stats.get("step", "clustering")
                    msg = f"[{step}] {done}/{total} — {stats.get('classified', 0)} clustered, {stats.get('noise', 0)} noise"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                cfg = PipelineConfig(
                    n_clusters=int(params.get("n_clusters", 150)),
                    max_clusters_per_company=int(params.get("max_clusters_per_company", 7)),
                    min_similarity=float(params.get("min_similarity", 0.10)),
                    n_components=int(params.get("n_components", 50)),
                    top_terms_per_cluster=int(params.get("top_terms", 5)),
                    top_keywords_per_company=int(params.get("top_keywords_per_company", 10)),
                )
                stats = run_pipeline(
                    db, cfg,
                    canton=params.get("canton") or None,
                    min_zefix_score=int(params["min_zefix_score"]) if params.get("min_zefix_score") else None,
                    max_zefix_score=int(params["max_zefix_score"]) if params.get("max_zefix_score") else None,
                    limit=int(params["limit"]) if params.get("limit") else None,
                    use_keywords=bool(params.get("use_keywords", False)),
                    progress_cb=_progress,
                )
                n_c = stats.get("n_clusters", 0)
                classified = stats.get("classified", 0)
                noise = stats.get("noise", 0)
                done_msg = f"Done — {n_c} clusters, {classified} companies labelled, {noise} noise"

                # Auto-enqueue stopword discovery after every successful clustering run
                try:
                    sw_job = enqueue_job(
                        app,
                        job_type="discover_stopwords",
                        label="Stopword & boilerplate discovery (auto — post cluster)",
                        params={"use_ai": False},
                        db=db,
                    )
                    crud.create_event(db, job_id=job.id, level="info",
                                      message=f"Auto-enqueued discover_stopwords (job #{sw_job.id})")
                except Exception as _sw_exc:  # noqa: BLE001
                    logger.warning("Could not auto-enqueue discover_stopwords: %s", _sw_exc)

            elif job.job_type == "recompute_keywords":
                from app.services.cluster_pipeline import PipelineConfig, recompute_keywords

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"[{stats.get('step', 'keywords')}] {done}/{total} — {stats.get('updated', 0)} updated"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                cfg = PipelineConfig(
                    top_keywords_per_company=int(params.get("top_keywords_per_company", 10)),
                )
                stats = recompute_keywords(
                    db, cfg,
                    canton=params.get("canton") or None,
                    limit=int(params["limit"]) if params.get("limit") else None,
                    progress_cb=_progress,
                )
                done_msg = f"Done — {stats['updated']} keywords updated, {stats['skipped']} skipped"

            elif job.job_type == "reextract_keywords":
                from app.services.cluster_pipeline import PipelineConfig, reextract_keywords_all

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"[keywords] {done}/{total} — {stats.get('updated', 0)} updated, {stats.get('skipped_no_purpose', 0)} skipped"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                cfg = PipelineConfig(
                    top_keywords_per_company=int(params.get("top_keywords_per_company", 10)),
                )
                stats = reextract_keywords_all(
                    db, cfg,
                    only_missing=bool(params.get("only_missing", False)),
                    canton=params.get("canton") or None,
                    limit=int(params["limit"]) if params.get("limit") else None,
                    progress_cb=_progress,
                )
                if stats.get("skipped_no_artifacts") == -1:
                    done_msg = "Aborted — no S3 artifacts found. Run a full tfidf_kmeans_cluster job first."
                else:
                    done_msg = (
                        f"Done — {stats['updated']} updated, "
                        f"{stats['skipped_no_purpose']} skipped (no purpose), "
                        f"{len(stats['errors'])} errors"
                    )

            elif job.job_type == "cluster_analysis":
                from app.services.cluster_pipeline import PipelineConfig, analyze_cross_cluster_terms

                cfg = PipelineConfig(
                    analysis_top_clusters=int(params.get("top_n_clusters", 20)),
                    analysis_top_terms=int(params.get("top_n_terms", 10)),
                )
                analyze_cross_cluster_terms(db, cfg)
                stats = {"errors": []}
                done_msg = "Cross-cluster analysis written — download at /static/cluster_analysis.txt"

            elif job.job_type == "claude_classify":
                from app.services.collection import claude_classify_batch
                from app.services.claude import resolve_claude_api_key

                _org_id = job.org_id
                _eff = lambda key, default="": crud.get_effective_setting(db, key, org_id=_org_id, default=default)
                _api_key = resolve_claude_api_key(db, _org_id)
                _use_batch = bool(params.get("use_batch_api", False))

                if _use_batch:
                    # Two-phase: submit to Anthropic Batch API and exit immediately.
                    # A background poll thread in the api-worker will process results.
                    submit_stats = claude_classify_batch(
                        db,
                        canton=params.get("canton") or None,
                        min_flex_score=params.get("min_zefix_score"),
                        max_flex_score=params.get("max_zefix_score"),
                        min_web_score=params.get("min_google_score"),
                        purpose_keywords=params.get("purpose_keywords") or None,
                        rerun_classified=bool(params.get("rerun_classified", False)),
                        auto_filter_keywords=bool(params.get("auto_filter_keywords", False)),
                        use_fixed_categories=bool(params.get("use_fixed_categories", False)),
                        limit=int(params.get("limit", 500)),
                        system_prompt=params.get("system_prompt") or _eff("claude_classify_prompt") or None,
                        target_description=_eff("claude_target_description") or None,
                        api_key=_api_key,
                        org_id=_org_id,
                        use_batch_api=True,
                        submit_only=True,
                        companies_per_message=int(params.get("companies_per_message", 1)),
                    )
                    _batch_id = submit_stats.get("batch_id", "")
                    updated_params = {
                        **params,
                        "batch_id": _batch_id,
                        "chunk_company_ids": submit_stats.get("chunk_company_ids", {}),
                    }
                    crud.mark_waiting_external(
                        db, job,
                        message=f"Anthropic batch submitted: {_batch_id}",
                        params=updated_params,
                    )
                    crud.create_event(db, job_id=job.id, level="info", message=f"Batch submitted: {_batch_id}")
                    raise _JobWaitingExternalSignal()

                # Synchronous path
                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                    msg = f"Classified {done}/{total} — {stats['classified']} scored, ~{tokens} tokens used"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)
                    _heartbeat()

                stats = claude_classify_batch(
                    db,
                    canton=params.get("canton") or None,
                    min_flex_score=params.get("min_zefix_score"),
                    max_flex_score=params.get("max_zefix_score"),
                    min_web_score=params.get("min_google_score"),
                    purpose_keywords=params.get("purpose_keywords") or None,
                    rerun_classified=bool(params.get("rerun_classified", False)),
                    auto_filter_keywords=bool(params.get("auto_filter_keywords", False)),
                    use_fixed_categories=bool(params.get("use_fixed_categories", False)),
                    limit=int(params.get("limit", 500)),
                    system_prompt=params.get("system_prompt") or _eff("claude_classify_prompt") or None,
                    target_description=_eff("claude_target_description") or None,
                    api_key=_api_key,
                    org_id=_org_id,
                    resume_from=resume_from,
                    use_batch_api=False,
                    companies_per_message=int(params.get("companies_per_message", 1)),
                    progress_cb=_progress,
                )
                tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                done_msg = f"Done — {stats['classified']} classified, {stats['skipped']} skipped, ~{tokens} tokens, {len(stats['errors'])} errors"

            elif job.job_type in ("shab_daily", "shab_backfill"):
                from datetime import date as _date, timedelta as _td
                from app.services.shab_import import import_shab_publications, yesterday

                if job.job_type == "shab_daily":
                    date_str = params.get("date")
                    if date_str:
                        target_date = _date.fromisoformat(date_str)
                    else:
                        target_date = yesterday()
                    from_date = target_date
                    to_date = target_date
                else:
                    from_date = _date.fromisoformat(params["from_date"])
                    to_date_str = params.get("to_date")
                    to_date = _date.fromisoformat(to_date_str) if to_date_str else yesterday()

                def _progress(done: int, total: int, _stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processing {done}/{total} — "
                        f"{_stats.get('created', 0)} new, "
                        f"{_stats.get('updated', 0)} updated, "
                        f"{_stats.get('deleted', 0)} deleted"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=_stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(_stats), error=None, done=False)
                    _heartbeat()

                stats = import_shab_publications(
                    db,
                    from_date=from_date,
                    to_date=to_date,
                    app=app,
                    request_delay=float(params.get("request_delay", 0.15)),
                    resume_from=resume_from,
                    progress_cb=_progress,
                    status_cb=lambda m: (
                        _assert_not_cancelled(),
                        crud.update_progress(db, job, message=str(m)),
                        crud.create_event(db, job_id=job.id, level="info", message=str(m)),
                        _maybe_sync(app, job_type=job.job_type, label=job.label, message=str(m), stats=json.loads(job.stats_json) if job.stats_json else {}, error=None, done=False),
                    ),
                    abort_cb=_assert_not_cancelled,
                )
                done_msg = (
                    f"Done — {stats['created']} new, {stats['updated']} updated, "
                    f"{stats['deleted']} deleted, {stats['skipped']} skipped, "
                    f"{len(stats['errors'])} errors "
                    f"({stats['publications_fetched']} publications fetched)"
                )
                if stats.get("detail_jobs_queued"):
                    done_msg += f"; {stats['detail_jobs_queued']} detail job(s) queued for new UIDs"
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "csv_export":
                from app.services.csv_export import run_csv_export
                from app.services.s3_client import is_configured

                if not is_configured():
                    raise ValueError(
                        "S3 not configured: set S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, S3_ENDPOINT_URL"
                    )

                def _progress(done: int, total: int, _stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Exported {done:,}" + (f"/{total:,}" if total else "") + " rows…"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=_stats)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(_stats), error=None, done=False)
                    _heartbeat()

                stats = run_csv_export(
                    db,
                    params=params,
                    user_id=job.user_id,
                    org_id=job.org_id,
                    progress_cb=_progress,
                )
                done_msg = f"Done — {stats['row_count']:,} rows exported to S3"

            elif job.job_type == "billing_renewal":
                from app.services.billing_renewal import run_billing_renewal

                crud.update_progress(db, job, message="Running subscription billing renewal…")
                stats = run_billing_renewal(db)
                done_msg = (
                    f"Done — {stats['renewed']} renewed, "
                    f"{stats['cancelled']} cancelled, "
                    f"{stats['failed']} failed, "
                    f"{stats['skipped']} skipped"
                )

            elif job.job_type == "saved_view_alerts":
                from app.services.saved_view_alerts import run_saved_view_alerts
                crud.update_progress(db, job, message="Checking saved view alerts…")
                stats = run_saved_view_alerts(db)
                done_msg = (
                    f"Done — {stats['checked']} views checked, "
                    f"{stats['alerted']} alerted, "
                    f"{stats.get('errors', 0)} errors"
                )

            elif job.job_type == "sogc_preprocess":
                from app.services.sogc_preprocessor import run_sogc_preprocess_batch

                mode = params.get("mode", "missing")
                uids_raw = params.get("uids") or []
                uids = [u for u in uids_raw if u] or None
                batch_size = int(params.get("batch_size", 500))

                def _progress(done: int, total: int, _stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processing {done}/{total} — "
                        f"{_stats.get('processed', 0)} companies, "
                        f"{_stats.get('publications_written', 0)} publications"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=_stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(_stats), error=None, done=False)
                    _heartbeat()

                stats = run_sogc_preprocess_batch(
                    db,
                    mode=mode,
                    uids=uids,
                    batch_size=batch_size,
                    resume_from=resume_from,
                    progress_cb=_progress,
                    status_cb=lambda m: (
                        _assert_not_cancelled(),
                        crud.update_progress(db, job, message=str(m)),
                        crud.create_event(db, job_id=job.id, level="info", message=str(m)),
                    ),
                    abort_cb=_assert_not_cancelled,
                )
                uid_note = f" ({len(uids)} UID(s))" if uids else ""
                done_msg = (
                    f"Done{uid_note} — {stats['processed']} companies processed, "
                    f"{stats['publications_written']} publications written, "
                    f"{stats['skipped_no_pub']} skipped (no data), "
                    f"{len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from id={resume_from})"

            elif job.job_type == "extract_sogc_persons":
                from app.services.sogc_person_extractor import run_extract_sogc_persons_batch

                mode = params.get("mode", "missing")
                batch_size = int(params.get("batch_size", 1000))

                def _progress(done: int, total: int, _stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Processing {done}/{total} — "
                        f"{_stats.get('persons_written', 0)} persons, "
                        f"{_stats.get('auditors_written', 0)} auditors, "
                        f"{len(_stats.get('errors', []))} errors"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=_stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats=dict(_stats), error=None, done=False)
                    _heartbeat()

                stats = run_extract_sogc_persons_batch(
                    db,
                    mode=mode,
                    batch_size=batch_size,
                    resume_from=resume_from,
                    progress_cb=_progress,
                    status_cb=lambda m: (
                        _assert_not_cancelled(),
                        crud.update_progress(db, job, message=str(m)),
                        crud.create_event(db, job_id=job.id, level="info", message=str(m)),
                    ),
                    abort_cb=_assert_not_cancelled,
                )
                done_msg = (
                    f"Done — {stats['persons_written']} persons, "
                    f"{stats['auditors_written']} auditors extracted from "
                    f"{stats['processed']} changes, "
                    f"{stats['skipped_no_excerpt']} skipped, "
                    f"{len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from change id={resume_from})"

            elif job.job_type == "noga_test":
                from app.services.noga import classify_company_noga_v2, is_branch_office, _parent_uid_from_head_offices

                company_id = int(params["company_id"])
                company = crud.get_company(db, company_id)
                if company is None:
                    raise ValueError(f"Company {company_id} not found")
                crud.update_progress(db, job, message=f"Running NOGA v2 test for {company.name}…", done=0, total=1, stats={})
                stats = classify_company_noga_v2(db, company)
                stats["company_id"] = company_id
                stats["company_uid"] = company.uid
                stats["company_name"] = company.name
                stats["stored_noga_code"] = company.noga_code
                stats["stored_noga_label"] = company.noga_label
                stats["stored_noga_confidence"] = company.noga_confidence
                stats["stored_noga_path_labels"] = company.noga_path_labels
                done_msg = f"NOGA v2 test complete for {company.name}"

            elif job.job_type == "noga_explain":
                from app.services.noga import classify_company_noga_explain, is_branch_office, _parent_uid_from_head_offices

                company_id = int(params["company_id"])
                company = crud.get_company(db, company_id)
                if company is None:
                    raise ValueError(f"Company {company_id} not found")
                crud.update_progress(db, job, message=f"Running NOGA explain for {company.name}…", done=0, total=1, stats={})
                stats = classify_company_noga_explain(db, company)
                stats["company_id"] = company_id
                stats["company_uid"] = company.uid
                stats["company_name"] = company.name
                stats["stored_noga_code"] = company.noga_code
                stats["stored_noga_label"] = company.noga_label
                stats["stored_noga_confidence"] = company.noga_confidence
                stats["stored_noga_path_labels"] = company.noga_path_labels
                stats["is_branch_office"] = is_branch_office(company)
                stats["parent_uid"] = _parent_uid_from_head_offices(company) if stats["is_branch_office"] else None
                done_msg = f"NOGA explain complete for {company.name}"

            else:
                raise RuntimeError(f"Unsupported job type: {job.job_type}")

            crud.mark_completed(db, job, message=done_msg, stats=stats)
            crud.create_event(db, job_id=job.id, level="info", message=done_msg)
            for _w in (stats.get("warnings") or [])[:10]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_w))
            for _err in (stats.get("errors") or [])[:50]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_err))
            _maybe_sync(app, job_type=job.job_type, label=job.label, message=done_msg, stats=dict(stats), error=None, done=True)
            _publish_job_update(job.org_id)
            _maybe_send_job_notification(db, job=job, event="completed", stats=stats)
            # Bust taxonomy cache when any job that changes category/score data finishes.
            _TAXONOMY_INVALIDATING = {
                "claude_classify", "reclassify_noga", "recalculate_scores",
                "recalculate_google_scores", "reextract_purpose",
            }
            if job.job_type in _TAXONOMY_INVALIDATING:
                from app.crud.company import invalidate_taxonomy_cache, invalidate_category_stats_cache
                invalidate_taxonomy_cache()
                invalidate_category_stats_cache()

        except _JobWaitingExternalSignal:
            # Job transitioned to waiting_external — already committed above; nothing else needed.
            _publish_job_update(job.org_id)
            return

        except JobPausedError:
            current_stats = json.loads(job.stats_json) if job.stats_json else {}
            done_n = job.progress_done or 0
            total_n = job.progress_total
            pause_msg = f"Paused at {done_n}" + (f"/{total_n}" if total_n else "")
            crud.mark_paused(db, job, message=pause_msg, stats=current_stats)
            crud.create_event(db, job_id=job.id, level="info", message=pause_msg)
            _maybe_sync(app, job_type=job.job_type, label=job.label, message=pause_msg, stats=current_stats, error=None, done=True)
            _publish_job_update(job.org_id)

        except JobCancelledError:
            msg = "Cancelled by user"
            _refund_job_credits_if_needed(db, job=job, reason="cancelled")
            crud.mark_cancelled(db, job, message=msg)
            crud.create_event(db, job_id=job.id, level="warn", message=msg)
            _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats={}, error=None, done=True)
            _publish_job_update(job.org_id)

        except Exception as exc:  # noqa: BLE001
            err = traceback.format_exc()
            summary = f"{type(exc).__name__}: {exc}".strip()
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.error("Job %s (%s) failed:\n%s", job.id, job.job_type, err)
            _refund_job_credits_if_needed(db, job=job, reason="failed")
            crud.mark_failed(db, job, error=err, message=summary)
            crud.create_event(db, job_id=job.id, level="error", message=summary)
            crud.create_event(db, job_id=job.id, level="debug", message=err)
            _maybe_sync(app, job_type=job.job_type, label=job.label, message="Failed", stats={}, error=summary, done=True)
            _publish_job_update(job.org_id)
            _maybe_send_job_notification(db, job=job, event="failed", summary=summary)

        finally:
            _hb_stop.set()


# ── Job notification emails ───────────────────────────────────────────────────

def _maybe_send_job_notification(
    db: Session,
    *,
    job: "Any",
    event: str,
    stats: dict | None = None,
    summary: str | None = None,
) -> None:
    """Send a transactional email after job completion or failure.

    Rules:
    - Only fires when job.user_id is set (user-originated job).
    - Respects the org-level ``email_notifications`` setting (default on).
    - On completion: only emails for csv_export jobs (user explicitly queued it).
    - On failure: emails for any user-originated job.
    """
    try:
        if not job.user_id:
            return
        from app.crud.app_setting import get_effective_setting
        if get_effective_setting(db, "email_notifications", org_id=job.org_id, default="1") != "1":
            return
        from app.models.user import User
        user = db.get(User, job.user_id)
        if not user or not user.is_active:
            return
        from app.services import email as _email
        if event == "completed" and job.job_type == "csv_export":
            if get_effective_setting(db, "notif_export_ready", org_id=job.org_id, default="1") != "1":
                return
            _s = stats or {}
            _email.send_export_ready(
                to=user.email,
                row_count=int(_s.get("row_count") or 0),
                job_id=job.id,
                download_url=_s.get("download_url") or "",
            )
        elif event == "failed":
            if get_effective_setting(db, "notif_job_failed", org_id=job.org_id, default="1") != "1":
                return
            _email.send_job_failed(
                to=user.email,
                job_type=job.job_type,
                label=job.label or job.job_type,
                job_id=job.id,
                summary=summary or "Unknown error",
            )
    except Exception:  # noqa: BLE001
        pass  # never let email errors break job status reporting


# ── Worker loop ────────────────────────────────────────────────────────────────

def _get_job_type_whitelist() -> set[str] | None:
    """Read JOB_TYPE_WHITELIST from env. Returns None (= handle all types) when unset."""
    raw = os.environ.get("JOB_TYPE_WHITELIST", "").strip()
    if not raw:
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


def _get_job_type_blacklist() -> set[str] | None:
    """Read JOB_TYPE_BLACKLIST from env. Returns None (= block no types) when unset."""
    raw = os.environ.get("JOB_TYPE_BLACKLIST", "").strip()
    if not raw:
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


_JOB_POLL_INTERVAL = int(os.environ.get("JOB_POLL_INTERVAL", "5"))


_STALE_JOB_RECOVERY_INTERVAL = 180  # seconds between periodic stale-job sweeps


def _job_worker_loop(app) -> None:
    whitelist = _get_job_type_whitelist()
    blacklist = _get_job_type_blacklist()
    # In split-worker mode (JOB_TYPE_WHITELIST set) the web pod can't kick this
    # thread when new jobs arrives, so we poll continuously. In single-pod mode
    # we break when idle and let enqueue_job() kick us via _ensure_job_worker().
    continuous = bool(whitelist)
    if whitelist:
        logger.info("Job worker started (continuous poll) — handling job types: %s", ", ".join(sorted(whitelist)))
    elif blacklist:
        logger.info("Job worker started — handling all job types except: %s", ", ".join(sorted(blacklist)))
    else:
        logger.info("Job worker started — handling all job types")
    app.state.job_worker_running = True
    # Startup already ran requeue_interrupted_jobs; delay first periodic sweep so
    # we don't double-recover jobs whose heartbeat hasn't gone stale yet.
    _last_recovery = time.monotonic()
    try:
        while True:
            with SessionLocal() as db:
                next_job = crud.get_next_queued_job(db, job_type_whitelist=whitelist, job_type_blacklist=blacklist)
                if next_job is None:
                    if not continuous:
                        break
                    # Periodically recover jobs whose heartbeat went stale after
                    # startup (e.g. OOMKilled pods where the startup sweep ran
                    # before the 120 s stale window elapsed).
                    if time.monotonic() - _last_recovery >= _STALE_JOB_RECOVERY_INTERVAL:
                        recovered = crud.requeue_interrupted_jobs(db)
                        if recovered:
                            logger.info("Periodic stale-job sweep: recovered %d interrupted job(s)", recovered)
                        _last_recovery = time.monotonic()
                    time.sleep(_JOB_POLL_INTERVAL)
                    continue
                next_id = next_job.id
            _run_job(app, next_id)
    finally:
        app.state.job_worker_running = False
        with SessionLocal() as db:
            if crud.get_next_queued_job(db, job_type_whitelist=whitelist, job_type_blacklist=blacklist) is not None:
                _ensure_job_worker(app)


_LLM_POLL_INTERVAL = 300  # 5 minutes


def _llm_poll_loop() -> None:
    """Daemon thread: poll Anthropic Batch API jobs every 5 minutes."""
    while True:
        time.sleep(_LLM_POLL_INTERVAL)
        try:
            poll_llm_batches()
        except Exception:  # noqa: BLE001
            logger.error("LLM poll loop error", exc_info=True)


def _ensure_job_worker(app) -> None:
    if getattr(app.state, "disable_job_worker", False):
        return
    if not getattr(app.state, "llm_poll_running", False):
        app.state.llm_poll_running = True
        threading.Thread(target=_llm_poll_loop, daemon=True, name="llm-batch-poller").start()
    if getattr(app.state, "job_worker_running", False):
        return
    threading.Thread(target=_job_worker_loop, args=(app,), daemon=True).start()


def kick_job_worker(app) -> None:
    """Ensure all DB-queued jobs are being processed by the in-process daemon thread."""
    _ensure_job_worker(app)


# ── Enqueue helpers (used by REST routes) ─────────────────────────────────────

def _enqueue_job_in_session(
    db: Session,
    *,
    job_type: str,
    label: str,
    params: dict,
    org_id: int | None = None,
    user_id: int | None = None,
) -> object:
    # ── Dedup check ──────────────────────────────────────────────────────────
    # If an active job of the same type+org already exists, return it without
    # charging credits or creating a duplicate.
    dedup_key = _compute_dedup_key(job_type, org_id, params)
    if dedup_key is not None:
        existing = crud.find_active_by_dedup_key(db, dedup_key)
        if existing is not None:
            logger.info(
                "Dedup hit: returning existing job %s (type=%s key=%s status=%s)",
                existing.id, job_type, dedup_key, existing.status,
            )
            db.expunge(existing)
            return existing
    # ── Normal enqueue path ──────────────────────────────────────────────────
    preflight_params, warnings = _preflight_job(db, job_type=job_type, params=params)
    deduction = _apply_credit_deduction_if_needed(
        db,
        job_type=job_type,
        params=preflight_params,
        org_id=org_id,
        user_id=user_id,
    )
    # Embed the deduction record into initial stats so the runner can refund on failure.
    initial_stats: dict = {}
    if deduction is not None:
        from app.services.credits import CREDIT_COSTS
        action, count = deduction
        initial_stats["_credit_deduction"] = {
            "action": action,
            "count": count,
            "cost": CREDIT_COSTS.get(action, 0) * count,
        }
    job = crud.create_job(db, job_type=job_type, label=label, params=preflight_params, org_id=org_id, user_id=user_id, dedup_key=dedup_key, initial_stats=initial_stats if initial_stats else None)
    crud.create_event(db, job_id=job.id, level="info", message="Job queued")
    if warnings:
        for w in warnings:
            crud.create_event(db, job_id=job.id, level="warn", message=f"Preflight: {w}")
        crud.update_progress(db, job, message=f"Queued — {warnings[0]}")
    # `create_event()` commits, which expires ORM attributes by default.
    # Refresh + expunge so the returned `job` can be serialized safely
    # after the session context closes (avoids DetachedInstanceError).
    db.refresh(job)
    db.expunge(job)
    return job

def enqueue_job(
    app,
    *,
    job_type: str,
    label: str,
    params: dict,
    db: Session | None = None,
    org_id: int | None = None,
    user_id: int | None = None,
) -> object:
    if db is None:
        with SessionLocal() as session:
            job = _enqueue_job_in_session(session, job_type=job_type, label=label, params=params, org_id=org_id, user_id=user_id)
    else:
        job = _enqueue_job_in_session(db, job_type=job_type, label=label, params=params, org_id=org_id, user_id=user_id)

    if app is None:
        raise JobEnqueueError("Thread worker mode requires a FastAPI app instance")
    if getattr(app.state, "disable_job_worker", False):
        # Split-worker mode: job is persisted to DB; the api-worker pod will pick it up.
        return job
    _ensure_job_worker(app)
    return job




def poll_llm_batches() -> None:
    """Check all waiting Anthropic Batch API jobs and process any that have completed.

    Called periodically by the api-worker background thread.  Safe to call from any
    context — silently no-ops if there are no waiting jobs or if the anthropic
    package is unavailable.
    """
    from app import crud as _crud
    from app.config import settings as _settings

    try:
        with SessionLocal() as db:
            waiting = _crud.list_waiting_llm_batches(db)
            if not waiting:
                return

            from app.services.collection import resume_claude_batch

            for job in waiting:
                params = json.loads(job.params_json or "{}")
                batch_id = params.get("batch_id")
                chunk_company_ids = params.get("chunk_company_ids") or {}
                if not batch_id:
                    logger.warning("poll_llm_batches: job %s has no batch_id; skipping", job.id)
                    continue

                # Use org-effective API key so per-org keys are honoured
                api_key = (
                    _crud.get_effective_setting(db, "anthropic_api_key", org_id=job.org_id, default="")
                    or _settings.anthropic_api_key
                    or ""
                ).strip()
                if not api_key:
                    logger.warning("poll_llm_batches: job %s has no Anthropic API key; skipping", job.id)
                    continue

                try:
                    status, stats = resume_claude_batch(
                        db,
                        batch_id=batch_id,
                        chunk_company_ids=chunk_company_ids,
                        api_key=api_key,
                        org_id=job.org_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("poll_llm_batches: job %s raised %s: %s", job.id, type(exc).__name__, exc)
                    _crud.mark_failed(db, job, error=str(exc), message=f"{type(exc).__name__}: {exc}")
                    _crud.create_event(db, job_id=job.id, level="error", message=str(exc))
                    continue

                if status == "ended":
                    tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                    done_msg = (
                        f"Done — {stats['classified']} classified, {stats['skipped']} skipped, "
                        f"~{tokens} tokens, {len(stats['errors'])} errors"
                    )
                    _crud.mark_completed(db, job, message=done_msg, stats=stats)
                    _crud.create_event(db, job_id=job.id, level="info", message=done_msg)
                    for err in (stats.get("errors") or [])[:50]:
                        _crud.create_event(db, job_id=job.id, level="warn", message=str(err))
                    logger.info("poll_llm_batches: job %s completed (%s)", job.id, done_msg)
                    _publish_job_update(job.org_id)
                elif status == "error":
                    err_msg = stats.get("error", "Unknown Anthropic API error")
                    _crud.mark_failed(db, job, error=err_msg, message=err_msg)
                    _crud.create_event(db, job_id=job.id, level="error", message=err_msg)
                    _publish_job_update(job.org_id)
                # else: still processing — do nothing, retry next poll cycle

    except Exception as exc:  # noqa: BLE001
        logger.error("poll_llm_batches: unexpected error: %s", exc)
