"""Background job worker.

Two modes:
- Thread mode (default): a daemon thread polls the DB queue and runs jobs
  sequentially in-process. Zero external dependencies.
- RQ mode (USE_RQ=true + REDIS_URL set): jobs are pushed to a Redis queue
  and executed by a separate `rq worker` process (app/worker_entrypoint.py).
  The web pod sets DISABLE_JOB_WORKER=true so it never starts a thread.

`enqueue_job()` is the only public entry point used by REST routes — it
handles both modes transparently.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback

from app import crud
from app.database import SessionLocal
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class JobCancelledError(Exception):
    """Raised when a running job receives a cancellation request."""


class JobPausedError(Exception):
    """Raised when a running job receives a pause request."""


class JobEnqueueError(RuntimeError):
    """Raised when a job cannot be enqueued or processed due to configuration."""


class _JobWaitingExternalSignal(Exception):
    """Internal signal: job transitioned to waiting_external — skip mark_completed."""


# ── Queue routing ───────────────────────────────────────────────────────────────

_QUEUE_FOR_JOB_TYPE: dict[str, str] = {
    "bulk":                      "helvex-zefix",
    "detail":                    "helvex-zefix",
    "initial":                   "helvex-zefix",
    "batch":                     "helvex-api",
    "re_geocode":                "helvex-api",
    "recalculate_scores":        "helvex-api",
    "recalculate_google_scores": "helvex-api",
    "reextract_purpose":         "helvex-api",
    "reclassify_noga":           "helvex-api",
    "claude_classify":           "helvex-api",
    "csv_export":                "helvex-api",
    "hdbscan_cluster":           "helvex-ml",
    "recompute_keywords":        "helvex-ml",
    "cluster_analysis":          "helvex-ml",
}


def _heartbeat() -> None:
    """Renew the RQ started-registry TTL so clean_registries won't mark this job stale.

    Safe to call from any context — no-ops outside RQ workers.
    """
    try:
        from datetime import datetime, timezone as _tz
        from rq import get_current_job as _get_rq_job
        _rq_job = _get_rq_job()
        if _rq_job is not None:
            _rq_job.heartbeat(datetime.now(tz=_tz.utc), 3600)
    except Exception:  # noqa: BLE001
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
    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        if not job:
            return

        # Guard against double-dispatch: another worker may have already picked this up
        if job.status not in ("queued", "paused"):
            return

        if job.status == "cancelled" or job.cancel_requested:
            crud.mark_cancelled(db, job, message="Cancelled before start")
            crud.create_event(db, job_id=job.id, level="info", message="Job cancelled before execution started")
            return

        crud.mark_running(db, job, message="Starting…")
        crud.create_event(db, job_id=job.id, level="info", message="Job started")
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

            elif job.job_type == "hdbscan_cluster":
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
                from app.config import settings as app_settings
                from app.services.collection import claude_classify_batch

                _org_id = job.org_id
                _eff = lambda key, default="": crud.get_effective_setting(db, key, org_id=_org_id, default=default)
                _api_key = _eff("anthropic_api_key") or app_settings.anthropic_api_key
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

            else:
                raise RuntimeError(f"Unsupported job type: {job.job_type}")

            crud.mark_completed(db, job, message=done_msg, stats=stats)
            crud.create_event(db, job_id=job.id, level="info", message=done_msg)
            for _w in (stats.get("warnings") or [])[:10]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_w))
            for _err in (stats.get("errors") or [])[:50]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_err))
            _maybe_sync(app, job_type=job.job_type, label=job.label, message=done_msg, stats=dict(stats), error=None, done=True)

        except _JobWaitingExternalSignal:
            # Job transitioned to waiting_external — already committed above; nothing else needed.
            return

        except JobPausedError:
            current_stats = json.loads(job.stats_json) if job.stats_json else {}
            done_n = job.progress_done or 0
            total_n = job.progress_total
            pause_msg = f"Paused at {done_n}" + (f"/{total_n}" if total_n else "")
            crud.mark_paused(db, job, message=pause_msg, stats=current_stats)
            crud.create_event(db, job_id=job.id, level="info", message=pause_msg)
            _maybe_sync(app, job_type=job.job_type, label=job.label, message=pause_msg, stats=current_stats, error=None, done=True)

        except JobCancelledError:
            msg = "Cancelled by user"
            crud.mark_cancelled(db, job, message=msg)
            crud.create_event(db, job_id=job.id, level="warn", message=msg)
            _maybe_sync(app, job_type=job.job_type, label=job.label, message=msg, stats={}, error=None, done=True)

        except Exception as exc:  # noqa: BLE001
            err = traceback.format_exc()
            summary = f"{type(exc).__name__}: {exc}".strip()
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.error("Job %s (%s) failed:\n%s", job.id, job.job_type, err)
            crud.mark_failed(db, job, error=err, message=summary)
            crud.create_event(db, job_id=job.id, level="error", message=summary)
            crud.create_event(db, job_id=job.id, level="debug", message=err)
            _maybe_sync(app, job_type=job.job_type, label=job.label, message="Failed", stats={}, error=summary, done=True)


# ── Worker loop ────────────────────────────────────────────────────────────────

def _job_worker_loop(app) -> None:
    app.state.job_worker_running = True
    try:
        while True:
            with SessionLocal() as db:
                next_job = crud.get_next_queued_job(db)
                if next_job is None:
                    break
                next_id = next_job.id
            _run_job(app, next_id)
    finally:
        app.state.job_worker_running = False
        with SessionLocal() as db:
            if crud.get_next_queued_job(db) is not None:
                _ensure_job_worker(app)


def _ensure_job_worker(app) -> None:
    if getattr(app.state, "disable_job_worker", False):
        return
    if getattr(app.state, "job_worker_running", False):
        return
    threading.Thread(target=_job_worker_loop, args=(app,), daemon=True).start()


def kick_job_worker(app) -> None:
    """Ensure all DB-queued jobs are being processed.

    RQ mode: push every queued job ID onto Redis. Safe to call multiple times —
    _run_job() guards against double-execution by checking job.status on pickup.
    Jobs in `waiting_external` status are skipped — they are being polled by the
    api-worker background thread, not re-enqueued.

    Thread mode: start/wake the in-process daemon thread.
    """
    from app.config import settings as _settings
    if _settings.use_rq and _settings.redis_url:
        with SessionLocal() as db:
            queued = crud.list_queued_jobs(db)
        for job in queued:
            _enqueue_rq(job.id, job_type=job.job_type)
    else:
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
    preflight_params, warnings = _preflight_job(db, job_type=job_type, params=params)
    job = crud.create_job(db, job_type=job_type, label=label, params=preflight_params, org_id=org_id, user_id=user_id)
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

    from app.config import settings as _settings
    if _settings.use_rq:
        if not _settings.redis_url:
            raise JobEnqueueError("USE_RQ=true but REDIS_URL is not set — jobs cannot be processed")
        try:
            _enqueue_rq(job.id, job_type=job_type)
        except Exception as exc:  # noqa: BLE001
            raise JobEnqueueError(f"Failed to enqueue job onto Redis: {type(exc).__name__}: {exc}") from exc
    else:
        if app is None:
            raise JobEnqueueError("Thread worker mode requires a FastAPI app instance")
        if getattr(app.state, "disable_job_worker", False):
            raise JobEnqueueError("DISABLE_JOB_WORKER=true but USE_RQ is not enabled — no worker is available")
        _ensure_job_worker(app)
    return job


def _enqueue_rq(job_id: int, *, job_type: str = "") -> None:
    """Push job_id onto the appropriate Redis queue for the RQ worker to pick up.

    All jobs use job_timeout=-1 (no SIGALRM wall-clock limit).  The _progress
    heartbeat keeps the started-registry TTL alive; cancel_requested is the only
    kill switch.  Queue routing is determined by _QUEUE_FOR_JOB_TYPE.
    """
    from redis import Redis
    from rq import Queue as RQueue
    from app.config import settings as _settings

    queue_name = _QUEUE_FOR_JOB_TYPE.get(job_type, "helvex-api")
    conn = Redis.from_url(_settings.redis_url)
    q = RQueue(queue_name, connection=conn)
    q.enqueue(run_job_task, job_id, job_timeout=-1, on_failure=_rq_job_failed)


def run_job_task(job_id: int) -> None:
    """RQ task function — called by the worker process for each job."""
    _run_job(None, job_id)


def _rq_job_failed(rq_job, connection, type, value, traceback) -> None:  # noqa: A002
    """RQ on_failure callback — fired by the worker after a work horse dies.

    Covers cases the in-process except handler cannot reach, e.g. SIGKILL from
    a job timeout.  Marks the JobRun as failed so it doesn't stay stuck as
    'running' in the DB.
    """
    import traceback as _tb
    from app.database import SessionLocal
    from app import crud

    job_id: int = rq_job.args[0] if rq_job.args else None
    if job_id is None:
        return

    error_msg = f"{type.__name__}: {value}" if type else "Killed by RQ worker (timeout or signal)"

    # Robustly stringify traceback — RQ may pass a real tb, a StackSummary, or None
    tb_str = ""
    if traceback:
        try:
            tb_str = "".join(_tb.format_exception(type, value, traceback))
        except Exception:
            try:
                # StackSummary has .format(); real tb objects do not
                if hasattr(traceback, "format"):
                    tb_str = "".join(traceback.format())
                else:
                    tb_str = "".join(_tb.format_tb(traceback))
            except Exception:
                tb_str = str(traceback)
    full_error = f"{error_msg}\n{tb_str}".strip()

    try:
        with SessionLocal() as db:
            job = crud.get_job(db, job_id)
            if job and job.status == "running":
                crud.mark_failed(db, job, error=full_error, message=error_msg)
                crud.create_event(db, job_id=job_id, level="error", message=error_msg)
    except Exception:  # noqa: BLE001
        pass


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
                elif status == "error":
                    err_msg = stats.get("error", "Unknown Anthropic API error")
                    _crud.mark_failed(db, job, error=err_msg, message=err_msg)
                    _crud.create_event(db, job_id=job.id, level="error", message=err_msg)
                # else: still processing — do nothing, retry next poll cycle

    except Exception as exc:  # noqa: BLE001
        logger.error("poll_llm_batches: unexpected error: %s", exc)
