"""Background job worker — runs jobs from the DB queue in a daemon thread.

Extracted from app/ui/routes.py so that main.py can import kick_job_worker
without pulling in Jinja2 or any UI dependencies.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback

from app import crud
from app.database import SessionLocal
from app.services.collection import (
    bulk_import_zefix,
    claude_classify_batch,
    re_geocode_all_companies,
    recalculate_google_scores,
    recalculate_zefix_scores,
    run_batch_collect,
    run_zefix_detail_collect,
    initial_collect,
)

logger = logging.getLogger(__name__)


class JobCancelledError(Exception):
    """Raised when a running job receives a cancellation request."""


class JobPausedError(Exception):
    """Raised when a running job receives a pause request."""


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


# ── Job runner ─────────────────────────────────────────────────────────────────

def _run_job(app, job_id: int) -> None:
    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        if not job:
            return

        if job.status == "cancelled" or job.cancel_requested:
            crud.mark_cancelled(db, job, message="Cancelled before start")
            crud.create_event(db, job_id=job.id, level="info", message="Job cancelled before execution started")
            return

        crud.mark_running(db, job, message="Starting…")
        crud.create_event(db, job_id=job.id, level="info", message="Job started")
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
                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Geocoded {done}/{total} — {stats['geocoded']} updated, {stats['failed']} no match"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

                stats = re_geocode_all_companies(db, resume_from=resume_from, progress_cb=_progress)
                done_msg = (
                    f"Done — {stats['geocoded']} geocoded, {stats['failed']} no match, "
                    f"{len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"
                crud.set_setting(db, "geocoding_building_level_done", "true")

            elif job.job_type == "recalculate_scores":
                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Recalculated {done}/{total} companies"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

                stats = recalculate_zefix_scores(db, resume_from=resume_from, progress_cb=_progress)
                done_msg = f"Done — {stats['updated']} recalculated, {stats.get('geocoded', 0)} geocoded, {len(stats['errors'])} errors"
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "recalculate_google_scores":
                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Recalculated Google scores for {done}/{total} companies"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

                stats = recalculate_google_scores(db, resume_from=resume_from, progress_cb=_progress)
                done_msg = (
                    f"Done — {stats['updated']} updated, {stats['skipped']} skipped, "
                    f"{len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "bulk":
                def _progress(canton: str, prefix: str, created: int, updated: int) -> None:
                    _assert_not_cancelled()
                    msg = f"Canton {canton} prefix {prefix} — {created} created, {updated} updated"
                    stats_now = {"created": created, "updated": updated}
                    crud.update_progress(db, job, message=msg, stats=stats_now)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=stats_now, error=None, done=False)

                stats = bulk_import_zefix(
                    db,
                    cantons=params.get("cantons"),
                    active_only=params.get("active_only", True),
                    request_delay=float(params.get("delay", 0.5)),
                    resume=True,
                    progress_cb=_progress,
                )
                done_msg = f"Done — {stats['created']} created, {stats['updated']} updated, {len(stats['errors'])} errors"

            elif job.job_type == "batch":
                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Processing {done}/{total} companies"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

                stats = run_batch_collect(
                    db,
                    limit=int(params.get("limit", 100)),
                    only_missing_website=bool(params.get("only_missing_website", True)),
                    refresh_zefix=bool(params.get("refresh_zefix", False)),
                    run_google=bool(params.get("run_google", True)),
                    resume_from=resume_from,
                    progress_cb=_progress,
                    canton=params.get("canton"),
                    min_zefix_score=params.get("min_zefix_score"),
                    min_claude_score=params.get("min_claude_score"),
                    purpose_keywords=params.get("purpose_keywords"),
                    tfidf_cluster=params.get("tfidf_cluster"),
                    review_status=params.get("review_status"),
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
                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = (
                        f"Collected {done}/{total} — {stats.get('created', 0)} created, "
                        f"{stats.get('updated', 0)} updated"
                    )
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

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
                )
                done_msg = (
                    f"Done — {stats['created']} created, {stats['updated']} updated, "
                    f"{stats['google_enriched']} enriched, {len(stats['errors'])} errors"
                )
                if resume_from:
                    done_msg += f" (resumed from {resume_from})"

            elif job.job_type == "detail":
                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    msg = f"Processing {done}/{total}"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

                stats = run_zefix_detail_collect(
                    db,
                    cantons=params.get("cantons"),
                    uids=params.get("uids"),
                    score_if_missing=bool(params.get("score_if_missing", True)),
                    only_missing_details=bool(params.get("only_missing_details", False)),
                    resume_from=resume_from,
                    request_delay=float(params.get("delay", 0.3)),
                    progress_cb=_progress,
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
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

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
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

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

                def _progress(done: int, total: int, stats: dict) -> None:
                    _assert_not_cancelled()
                    tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                    batch_id = stats.get("batch_id", "")
                    batch_hint = f" · batch {batch_id}" if batch_id and done == 0 else ""
                    msg = f"Classified {done}/{total} — {stats['classified']} scored, ~{tokens} tokens used{batch_hint}"
                    crud.update_progress(db, job, message=msg, done=done, total=total, stats=stats)
                    crud.create_event(db, job_id=job.id, level="debug", message=msg)
                    _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats=dict(stats), error=None, done=False)

                stats = claude_classify_batch(
                    db,
                    canton=params.get("canton") or None,
                    min_zefix_score=params.get("min_zefix_score"),
                    max_zefix_score=params.get("max_zefix_score"),
                    min_google_score=params.get("min_google_score"),
                    purpose_keywords=params.get("purpose_keywords") or None,
                    rerun_classified=bool(params.get("rerun_classified", False)),
                    auto_filter_keywords=bool(params.get("auto_filter_keywords", False)),
                    use_fixed_categories=bool(params.get("use_fixed_categories", False)),
                    limit=int(params.get("limit", 500)),
                    system_prompt=params.get("system_prompt") or None,
                    target_description=crud.get_setting(db, "claude_target_description", "") or None,
                    api_key=crud.get_setting(db, "anthropic_api_key", "") or app_settings.anthropic_api_key,
                    resume_from=resume_from,
                    use_batch_api=bool(params.get("use_batch_api", False)),
                    companies_per_message=int(params.get("companies_per_message", 1)),
                    progress_cb=_progress,
                )
                tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                done_msg = f"Done — {stats['classified']} classified, {stats['skipped']} skipped, ~{tokens} tokens, {len(stats['errors'])} errors"

            else:
                raise RuntimeError(f"Unsupported job type: {job.job_type}")

            crud.mark_completed(db, job, message=done_msg, stats=stats)
            crud.create_event(db, job_id=job.id, level="info", message=done_msg)
            for _w in (stats.get("warnings") or [])[:10]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_w))
            for _err in (stats.get("errors") or [])[:50]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_err))
            _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=done_msg, stats=dict(stats), error=None, done=True)

        except JobPausedError:
            current_stats = json.loads(job.stats_json) if job.stats_json else {}
            done_n = job.progress_done or 0
            total_n = job.progress_total
            pause_msg = f"Paused at {done_n}" + (f"/{total_n}" if total_n else "")
            crud.mark_paused(db, job, message=pause_msg, stats=current_stats)
            crud.create_event(db, job_id=job.id, level="info", message=pause_msg)
            _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=pause_msg, stats=current_stats, error=None, done=True)

        except JobCancelledError:
            msg = "Cancelled by user"
            crud.mark_cancelled(db, job, message=msg)
            crud.create_event(db, job_id=job.id, level="warn", message=msg)
            _sync_active_task(app.state, job_type=job.job_type, label=job.label, message=msg, stats={}, error=None, done=True)

        except Exception:  # noqa: BLE001
            err = traceback.format_exc()
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.error("Job %s (%s) failed:\n%s", job.id, job.job_type, err)
            crud.mark_failed(db, job, error=err)
            crud.create_event(db, job_id=job.id, level="error", message=err)
            _sync_active_task(app.state, job_type=job.job_type, label=job.label, message="Failed", stats={}, error=err, done=True)


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
    if getattr(app.state, "job_worker_running", False):
        return
    threading.Thread(target=_job_worker_loop, args=(app,), daemon=True).start()


def kick_job_worker(app) -> None:
    """Public wrapper used by app startup to ensure queued jobs begin processing."""
    _ensure_job_worker(app)


# ── Enqueue helpers (used by REST routes) ─────────────────────────────────────

def enqueue_job(app, *, job_type: str, label: str, params: dict) -> object:
    with SessionLocal() as db:
        job = crud.create_job(db, job_type=job_type, label=label, params=params)
        crud.create_event(db, job_id=job.id, level="info", message="Job queued")
    _ensure_job_worker(app)
    return job
