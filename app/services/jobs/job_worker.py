"""Background job worker.

A daemon thread polls the DB queue and runs jobs sequentially in-process.
Zero external dependencies. `enqueue_job()` is the only public entry point
used by REST routes.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from typing import TYPE_CHECKING, Any

from app import crud
from app.database import SessionLocal
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.job_run import JobRun

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
    """Raised when a running job receives a pause request.

    Set requeue=True for preemption: the job is immediately re-queued instead
    of staying paused, so another worker pod can pick it up right away.
    """
    def __init__(self, message: str = "", *, requeue: bool = False) -> None:
        super().__init__(message)
        self.requeue = requeue


class JobEnqueueError(RuntimeError):
    """Raised when a job cannot be enqueued or processed due to configuration."""


class _JobWaitingExternalSignal(Exception):
    """Internal signal: job transitioned to waiting_external — skip mark_completed."""



def _compute_dedup_key(job_type: str, org_id: int | None, params: dict) -> str | None:
    """Return a dedup key for this job, or None if dedup is not enforced.

    Default behaviour: one active job per type per org (key = "{type}:{org_id}").
    The DB enforces this via a partial unique index on job_runs.dedup_key for
    active statuses, so even simultaneous enqueues from multiple pods can only
    produce one row.

    Opt-out (NO_DEDUP): job types that are explicitly safe to run concurrently
    — e.g. batch exports, per-URL crawls.  New job types get dedup automatically
    without any code change here.

    Special cases override the default key shape:
    - claude_classify: content-hash key so distinct configs run concurrently
    - noga_v2_explain: per-company key
    """
    # Types that genuinely support multiple concurrent runs per org.
    # Everything else is deduplicated by default — add here to opt out.
    # web_crawl_http / web_crawl_playwright: claim_crawl_batch uses SELECT FOR UPDATE
    # SKIP LOCKED, so concurrent jobs claim disjoint rows — safe to run in parallel
    # and essential for utilising multiple HTTP pods.
    NO_DEDUP = {"batch", "csv_export", "web_select_url", "web_crawl_single",
                "web_crawl_http", "web_crawl_playwright"}

    # Per-entity dedup (not per-org-type).
    if job_type == "noga_v2_explain":
        company_id = params.get("company_id")
        return f"{job_type}:{company_id}" if company_id is not None else None

    if job_type in NO_DEDUP:
        return None

    # claude_classify: distinct prompt configs may run concurrently.
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

    # Default: one active job per type per org.
    return f"{job_type}:{org_id}"


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
            from app.services.ingestion.collection import _google_search_ready

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

    # NOTE: csv_export is charged at the ROUTE level (by tier cap), which then
    # passes the deduction to enqueue_job(credit_deduction=...). It must NOT be
    # charged here as well — doing so double-charged and, worse, counted *all*
    # matching rows ignoring the tier cap.

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

    from app.services.billing.credits import check_and_deduct

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
    job: JobRun,
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
        prorate = bool(deduction.get("prorate", True))
        if cost <= 0:
            return
    except (KeyError, ValueError, TypeError):
        return

    from app.services.billing.credits import grant_credits
    from app.models.organization import Organization
    from app.models.org_credit_transaction import OrgCreditTransaction

    org = db.get(Organization, job.org_id)
    if org is None or org.credits_unlimited:
        return

    # Idempotency: never refund the same job twice (e.g. cancel then a recovery sweep).
    already_refunded = (
        db.query(OrgCreditTransaction.id)
        .filter(
            OrgCreditTransaction.reference_id == f"refund:job:{job.id}",
            OrgCreditTransaction.type == "refund",
        )
        .first()
    )
    if already_refunded is not None:
        return

    # Prorate: refund only the portion of the charged work that was NOT performed.
    # Otherwise a user could enqueue a large metered job, let it process almost all
    # companies (results persist via per-batch commits), cancel just before completion,
    # and get a full refund while keeping the work. Jobs that never started (done=0) or
    # that don't track progress get a full refund.
    done = int(job.progress_done or 0)
    total = int(job.progress_total or 0)
    if prorate and total > 0 and done > 0:
        # Metered jobs persist partial work → refund only the undone fraction.
        undone = max(0, total - done)
        refund_amount = int(round(cost * undone / total))
    else:
        # Non-prorated (e.g. CSV export: atomic deliverable), never-started, or
        # untracked jobs → full refund.
        refund_amount = cost
    if refund_amount <= 0:
        logger.info(
            "credit_refund_skipped job_id=%d org_id=%d action=%s done=%d/%d reason=%s (work fully consumed)",
            job.id, job.org_id, action, done, total, reason,
        )
        return

    try:
        grant_credits(
            db,
            org_id=job.org_id,
            amount=refund_amount,
            tx_type="refund",
            action_type=action,
            reference_id=f"refund:job:{job.id}",
        )
        logger.info(
            "credit_refund job_id=%d org_id=%d action=%s charged=%d refunded=%d done=%d/%d reason=%s",
            job.id, job.org_id, action, cost, refund_amount, done, total, reason,
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

def _run_job(app, job_id: int) -> None:
    """Execute one job."""
    from app.metrics import record_job_duration
    from app.services.jobs.job_handlers import JOB_HANDLERS as _JOB_HANDLERS
    
    job_start_time = time.monotonic()

    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        if not job:
            return

        if job.status == "cancelled" or job.cancel_requested:
            _refund_job_credits_if_needed(db, job=job, reason="cancelled_before_start")
            crud.mark_cancelled(db, job, message="Cancelled before start")
            crud.create_event(db, job_id=job.id, level="info", message="Job cancelled before execution started")
            duration = time.monotonic() - job_start_time
            record_job_duration(job.job_type, duration, "cancelled")
            return

        # Atomic claim: UPDATE WHERE status IN ('queued','paused') — only one pod wins
        if not crud.atomic_claim_job(db, job_id, message="Starting…"):
            return  # another pod already claimed this job

        db.refresh(job)  # sync in-memory state after the atomic UPDATE
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
                except Exception as _hb_exc:  # noqa: BLE001
                    logger.warning("Heartbeat update failed for job %d: %s", job_id, _hb_exc)

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
            # If recovery on a sibling pod re-queued this job (due to a
            # heartbeat gap), our status is no longer 'running'.  Pause so
            # the re-queued instance can start cleanly instead of two threads
            # executing the same job in parallel.
            if job.status != "running":
                raise JobPausedError(f"Job evicted by recovery (status='{job.status}') — yielding to re-queued instance")

        try:
            if job.job_type in _JOB_HANDLERS:
                from app.services.jobs.job_handlers import JobContext, JOB_HANDLERS as _JH, JobWaitingExternalSignal
                _ctx = JobContext(
                    db=db,
                    job=job,
                    params=params,
                    resume_from=resume_from,
                    app=app,
                    _assert_not_cancelled=_assert_not_cancelled,
                    _maybe_sync=lambda **kw: _maybe_sync(app, **kw),
                    _heartbeat=_heartbeat,
                    _enqueue_job=enqueue_job,
                )
                try:
                    stats, done_msg = _JH[job.job_type](_ctx)
                except JobWaitingExternalSignal:
                    raise _JobWaitingExternalSignal()

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

        except _JobWaitingExternalSignal:
            # Job transitioned to waiting_external — already committed above; nothing else needed.
            _publish_job_update(job.org_id)
            return

        except JobPausedError as _pause_exc:
            current_stats = json.loads(job.stats_json) if job.stats_json else {}
            done_n = job.progress_done or 0
            total_n = job.progress_total
            if _pause_exc.requeue:
                pause_msg = f"Preempted at {done_n}" + (f"/{total_n}" if total_n else "") + " — requeued"
                crud.mark_paused(db, job, message=pause_msg, stats=current_stats)
                crud.resume_paused_job(db, job)
            else:
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
    job: Any,
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
        from app.services.notifications import email as _email
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


_JOB_WORKER_CONCURRENCY = int(os.environ.get("JOB_WORKER_CONCURRENCY", "1"))


def _job_worker_loop(app) -> None:
    from concurrent.futures import ThreadPoolExecutor, wait as _fut_wait, FIRST_COMPLETED

    whitelist = _get_job_type_whitelist()
    blacklist = _get_job_type_blacklist()
    continuous = bool(whitelist)
    concurrency = _JOB_WORKER_CONCURRENCY

    if whitelist:
        logger.info(
            "Job worker started (continuous poll, concurrency=%d) — handling job types: %s",
            concurrency, ", ".join(sorted(whitelist)),
        )
    elif blacklist:
        logger.info("Job worker started (concurrency=%d) — handling all job types except: %s", concurrency, ", ".join(sorted(blacklist)))
    else:
        logger.info("Job worker started (concurrency=%d) — handling all job types", concurrency)

    app.state.job_worker_running = True
    _last_recovery = time.monotonic()

    def _poll_next():
        with SessionLocal() as db:
            return crud.get_next_queued_job(
                db,
                job_type_whitelist=whitelist,
                job_type_blacklist=blacklist,
                skip_locked=True,
            )

    def _periodic_recovery(db_session):
        nonlocal _last_recovery
        if time.monotonic() - _last_recovery >= _STALE_JOB_RECOVERY_INTERVAL:
            recovered = crud.requeue_interrupted_jobs(db_session)
            if recovered:
                logger.info("Periodic stale-job sweep: recovered %d interrupted job(s)", recovered)
            resumed = crud.resume_all_paused_jobs(db_session, min_heartbeat_age_seconds=120)
            if resumed:
                logger.info("Periodic stale-job sweep: resumed %d paused job(s) with stale heartbeat", resumed)
            _last_recovery = time.monotonic()

    try:
        if concurrency == 1:
            # Single-threaded fast path — zero overhead, original behaviour.
            while True:
                next_job = _poll_next()
                if next_job is None:
                    if not continuous:
                        break
                    with SessionLocal() as db:
                        _periodic_recovery(db)
                    time.sleep(_JOB_POLL_INTERVAL)
                    continue
                _run_job(app, next_job.id)
        else:
            # Multi-threaded path: fill up to `concurrency` slots, wait for any
            # to finish, then refill.
            # in_flight maps job_id → Future so we never submit the same job_id
            # twice within one pod (which would happen because _poll_next() can
            # return the same row twice before atomic_claim_job flips it to
            # 'running').
            from concurrent.futures import Future as _Future
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job-worker") as pool:
                in_flight: dict[int, _Future] = {}
                while True:
                    # Drain completed futures.
                    for job_id, f in list(in_flight.items()):
                        if f.done():
                            del in_flight[job_id]
                            try:
                                f.result()
                            except Exception as exc:
                                logger.error("Unexpected error from job thread: %s", exc, exc_info=True)

                    with SessionLocal() as db:
                        _periodic_recovery(db)

                    # Fill empty slots — skip IDs already in-flight.
                    while len(in_flight) < concurrency:
                        nj = _poll_next()
                        if nj is None or nj.id in in_flight:
                            break
                        in_flight[nj.id] = pool.submit(_run_job, app, nj.id)

                    if not in_flight:
                        if not continuous:
                            break
                        time.sleep(_JOB_POLL_INTERVAL)
                        continue

                    # Block until a slot opens or the poll interval elapses,
                    # then loop to refill.
                    _fut_wait(list(in_flight.values()), timeout=_JOB_POLL_INTERVAL, return_when=FIRST_COMPLETED)
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
    credit_deduction: dict | None = None,
) -> object:
    # ── Dedup check ──────────────────────────────────────────────────────────
    # If an active job of the same type+org already exists, return it without
    # charging credits or creating a duplicate.
    dedup_key = _compute_dedup_key(job_type, org_id, params)
    if dedup_key is not None:
        existing = crud.find_active_by_dedup_key(db, dedup_key)
        if existing is not None:
            # Self-heal: a job that has already blown through the restart cap
            # should have been killed by requeue_interrupted_jobs(), but if that
            # somehow didn't stick (e.g. missed sweep, race), don't let it block
            # this job type forever. Fail it now and fall through to enqueue.
            if (existing.restart_count or 0) > crud.MAX_RESTART_COUNT:
                logger.warning(
                    "Dedup-blocking job %s (type=%s key=%s) exceeds restart cap "
                    "(%d/%d) — force-failing so a new job can be queued",
                    existing.id, job_type, dedup_key, existing.restart_count, crud.MAX_RESTART_COUNT,
                )
                crud.mark_failed(
                    db, existing,
                    error=f"Force-failed — exceeded restart cap ({existing.restart_count}/{crud.MAX_RESTART_COUNT})",
                    message="Force-failed (runaway restarts)",
                )
                crud.create_event(
                    db, job_id=existing.id, level="warn",
                    message="Force-failed by dedup self-heal — restart cap exceeded",
                )
            else:
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
        from app.services.billing.credits import CREDIT_COSTS
        action, count = deduction
        initial_stats["_credit_deduction"] = {
            "action": action,
            "count": count,
            "cost": CREDIT_COSTS.get(action, 0) * count,
            "prorate": True,   # metered jobs persist partial work → prorate refunds
        }
    elif credit_deduction is not None:
        # The caller already charged (e.g. the CSV export route, charged by tier
        # cap). Record it so the runner can refund on failure. Exports produce an
        # atomic deliverable (an S3 file on completion), so a failed/cancelled
        # export keeps nothing — hence prorate defaults to False for these.
        initial_stats["_credit_deduction"] = {
            "action": str(credit_deduction.get("action")),
            "count": int(credit_deduction.get("count") or 0),
            "cost": int(credit_deduction.get("cost") or 0),
            "prorate": bool(credit_deduction.get("prorate", False)),
            # Marks a charge made outside the enqueue path (at the route). rerun_job
            # re-charges these; the auto-deduction path above is left unmarked so
            # reruns of metered jobs are charged by _apply_credit_deduction_if_needed.
            "source": str(credit_deduction.get("source") or "route"),
        }
    from sqlalchemy.exc import IntegrityError as _IntegrityError
    try:
        job = crud.create_job(db, job_type=job_type, label=label, params=preflight_params, org_id=org_id, user_id=user_id, dedup_key=dedup_key, initial_stats=initial_stats if initial_stats else None)
    except _IntegrityError:
        # Two pods raced past the soft dedup check above and both tried to INSERT
        # with the same dedup_key.  The DB partial unique index rejected the second
        # insert.  Roll back and return the row the winning pod committed.
        db.rollback()
        if dedup_key is not None:
            existing = crud.find_active_by_dedup_key(db, dedup_key)
            if existing is not None:
                logger.info(
                    "Dedup race resolved via DB constraint: returning existing job %s (key=%s)",
                    existing.id, dedup_key,
                )
                db.expunge(existing)
                return existing
        raise
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
    credit_deduction: dict | None = None,
) -> object:
    if db is None:
        with SessionLocal() as session:
            job = _enqueue_job_in_session(session, job_type=job_type, label=label, params=params, org_id=org_id, user_id=user_id, credit_deduction=credit_deduction)
    else:
        job = _enqueue_job_in_session(db, job_type=job_type, label=label, params=params, org_id=org_id, user_id=user_id, credit_deduction=credit_deduction)

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

            from app.services.ingestion.collection import resume_claude_batch

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
