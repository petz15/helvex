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

# ── Tunables ───────────────────────────────────────────────────────────────────

# Seconds a worker sleeps when the queue is empty.  An enqueue on this pod wakes
# the poller early via `_wake_event`, so this is only the ceiling for work queued
# by *another* pod.
_JOB_POLL_INTERVAL = int(os.environ.get("JOB_POLL_INTERVAL", "5"))

_JOB_WORKER_CONCURRENCY = int(os.environ.get("JOB_WORKER_CONCURRENCY", "1"))

# Worker slots available for the HTTP crawler across the crawler-http pods,
# i.e. replicaCount x JOB_WORKER_CONCURRENCY. Injected by Helm into the *web*
# pod (which serves the trigger route but runs no crawl jobs itself), because
# nothing else lets the API size a crawl fan-out to the fleet actually running it.
#
# One job row is claimed by exactly ONE worker slot — `claim_next_job` is a
# single-row UPDATE. So a single enqueue leaves every other slot idle no matter
# how many pods are up; the fan-out has to happen at enqueue time. This is what
# makes `web_crawl_http` being in NO_DEDUP actually pay off.
_CRAWLER_HTTP_SLOTS = int(os.environ.get("CRAWLER_HTTP_SLOTS", "0"))

# Guard rail: each instance costs real memory on a crawler pod, so an operator
# typo (or a bad env value) must not enqueue hundreds of concurrent crawls.
MAX_CRAWL_INSTANCES = 16


def crawler_http_slots() -> int:
    """How many parallel web_crawl_http jobs the crawler fleet can actually run.

    Falls back to 1 when unset (single-pod / local dev), which reproduces the
    previous one-job-per-trigger behaviour exactly.
    """
    return max(1, min(_CRAWLER_HTTP_SLOTS or 1, MAX_CRAWL_INSTANCES))

# How often a running job stamps last_heartbeat_at.  Must stay well below
# `requeue_interrupted_jobs(stale_after_seconds=...)` (300 s) or live jobs get
# re-queued onto a second pod — the two constants are a pair.
_HEARTBEAT_INTERVAL = 30

# Seconds between stale-job recovery sweeps.
_STALE_JOB_RECOVERY_INTERVAL = 180

# Minimum seconds between cancel/pause flag polls inside a running job.
_FLAG_POLL_INTERVAL = 2.0

# Upper bound on how long shutdown waits for running jobs to hit a checkpoint
# and persist themselves as paused.  Kept under the K8s default 30 s
# terminationGracePeriodSeconds so the pod is not SIGKILLed mid-write.
_SHUTDOWN_JOIN_TIMEOUT = float(os.environ.get("JOB_SHUTDOWN_JOIN_TIMEOUT", "25"))


# ── Graceful shutdown ──────────────────────────────────────────────────────────

# An Event rather than a bare bool so it can be cleared: as a module global that
# was only ever set, one lifespan shutdown poisoned the whole process, and every
# job started afterwards paused at its first checkpoint.
_shutdown_event = threading.Event()

# Set by the worker loop while it owns in-flight jobs; `request_shutdown` waits
# on it so jobs get to their next checkpoint before the process dies.
_jobs_drained = threading.Event()
_jobs_drained.set()


def reset_shutdown() -> None:
    """Clear the shutdown flag. Called from lifespan startup."""
    _shutdown_event.clear()
    _jobs_drained.set()


def request_shutdown(timeout: float | None = None) -> None:
    """Signal running jobs to pause at their next checkpoint, then wait for them.

    Called from app/main.py lifespan shutdown.  Previously this only set the
    flag and returned, so uvicorn tore the process down mid-batch and the jobs
    were killed rather than paused — they stayed `running` in the DB until the
    5-minute recovery sweep noticed the dead heartbeat.  Waiting here lets each
    job persist itself as `paused` with its progress intact.
    """
    _shutdown_event.set()
    # Break an idle poller out of its park immediately rather than letting it
    # burn the remaining poll interval before it notices the shutdown.
    _wake_event.set()
    logger.info("Graceful shutdown requested — running jobs will pause at next checkpoint")

    # Slightly longer than the loop's own join budget: the loop may take up to a
    # poll interval to notice the flag, and warning before it has had its full
    # window would report a clean drain as a timeout.
    wait_for = (_SHUTDOWN_JOIN_TIMEOUT + _JOB_POLL_INTERVAL) if timeout is None else timeout
    if not _jobs_drained.wait(wait_for):
        logger.warning(
            "Shutdown drain timed out after %.0fs — remaining jobs will be recovered "
            "by the stale-job sweep on the next pod",
            wait_for,
        )
    else:
        logger.info("All in-flight jobs checkpointed — shutdown clean")


class JobCancelledError(Exception):
    """Raised when a running job receives a cancellation request."""


class JobPausedError(Exception):
    """Raised when a running job receives a pause request.

    `reason` is persisted to `job_runs.pause_reason` and decides whether the
    recovery sweep may auto-resume the job: only `'user'` pauses are left alone,
    because a person deliberately stopped that job and expects it to stay
    stopped.  `'shutdown'` and `'preempt'` pauses are infrastructure-driven and
    resume on their own.

    Set requeue=True for preemption: the job is immediately re-queued instead
    of staying paused, so another worker pod can pick it up right away.
    """
    def __init__(
        self,
        message: str = "",
        *,
        requeue: bool = False,
        reason: str = "shutdown",
    ) -> None:
        super().__init__(message)
        self.requeue = requeue
        self.reason = "preempt" if requeue else reason


class JobEnqueueError(RuntimeError):
    """Raised when a job cannot be enqueued or processed due to configuration."""


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
    # web_crawl_http / web_crawl_playwright / web_crawl_content: claim_crawl_batch
    # uses SELECT FOR UPDATE SKIP LOCKED, so concurrent jobs claim disjoint rows —
    # safe to run in parallel and essential for utilising multiple HTTP pods.
    # web_crawl_content additionally scopes its claim to crawl_phase='content', so
    # it never contends with the identity crawler over the same companies.
    NO_DEDUP = {"csv_export", "web_select_url", "web_crawl_single",
                "web_crawl_http", "web_crawl_playwright", "web_crawl_content",
                "web_crawl_content_playwright", "web_crawl_external"}

    # Per-entity dedup (not per-org-type).
    if job_type == "noga_v2_explain":
        company_id = params.get("company_id")
        return f"{job_type}:{company_id}" if company_id is not None else None

    # rescore_scope: per (org, user) scope, not per-org — otherwise two different
    # users in the same org rescoring their own materialized scope would
    # collide on the default "{type}:{org_id}" key and one would be skipped as
    # "already running" despite targeting a different scope entirely.
    if job_type == "rescore_scope":
        user_id = params.get("user_id")
        return f"rescore_scope:{org_id}:{user_id if user_id is not None else '-'}"

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


def _preflight_job(db: Session, *, job_type: str, params: dict) -> tuple[dict, list[str]]:
    """Validate prerequisites for a job and optionally rewrite params.

    Returns:
        (new_params, warnings)

    Raises:
        ValueError: If the job cannot run as requested.
    """
    warnings: list[str] = []
    new_params = dict(params or {})

    if job_type in {"web_search_batch", "initial"}:
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

    if job_type in {"web_search_batch", "initial"}:
        if not bool(params.get("run_google", True)):
            return None
        if job_type == "web_search_batch":
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


# ── Job runner ─────────────────────────────────────────────────────────────────

def _run_job(app, job_id: int) -> None:
    """Execute one already-claimed job.

    The caller (`_job_worker_loop`) claims the row atomically via
    `crud.claim_next_job()`, which flips it to `status='running'` in the same
    statement that selects it.  This function therefore does no claiming of its
    own — never call it with an id that was not claimed that way, or two pods
    can execute the same job.
    """
    from app.metrics import record_job_duration
    from app.services.jobs.job_handlers import (
        JOB_HANDLERS as _JOB_HANDLERS,
        JobContext,
        JobWaitingExternalSignal,
    )

    job_start_time = time.monotonic()
    outcome = "failed"

    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        if not job:
            return

        job_type = job.job_type
        crud.create_event(db, job_id=job.id, level="info", message="Job started")

        # Heartbeat daemon: stamps last_heartbeat_at every 30 s so that
        # requeue_interrupted_jobs() can tell this job is still alive and
        # must NOT be re-queued when the web pod restarts.
        _hb_stop = threading.Event()

        def _hb_daemon() -> None:
            while not _hb_stop.wait(_HEARTBEAT_INTERVAL):
                try:
                    with SessionLocal() as _hb_db:
                        crud.update_heartbeat(_hb_db, job_id)
                except Exception as _hb_exc:  # noqa: BLE001
                    logger.warning("Heartbeat update failed for job %d: %s", job_id, _hb_exc)

        _hb_thread = threading.Thread(target=_hb_daemon, daemon=True, name=f"hb-job-{job_id}")
        _hb_thread.start()

        params = json.loads(job.params_json or "{}")
        resume_from = max(0, int(job.progress_done or 0))

        # Throttle state for the checkpoint below.
        _last_flag_poll = 0.0

        def _assert_not_cancelled() -> None:
            """Checkpoint called between units of work by every handler.

            Shutdown is a local flag, so it is honoured immediately.  The DB
            flags are polled at most every `_FLAG_POLL_INTERVAL` seconds: on a
            700k-row job this checkpoint runs per company, and the previous
            `db.refresh(job)` made that one full-row SELECT per row — on the
            handler's own session, so it could also autoflush the handler's
            pending state at an arbitrary point mid-batch.  The trade is that a
            cancel or pause lands within a couple of seconds instead of
            instantly.
            """
            nonlocal _last_flag_poll

            if _shutdown_event.is_set():
                raise JobPausedError("Worker shutdown — job paused for restart", reason="shutdown")

            now = time.monotonic()
            if now - _last_flag_poll < _FLAG_POLL_INTERVAL:
                return
            _last_flag_poll = now

            # Short-lived session, never the handler's own — see get_job_flags.
            with SessionLocal() as _flag_db:
                flags = crud.get_job_flags(_flag_db, job_id)
            if flags is None:
                return
            status, cancel_requested, pause_requested = flags
            if cancel_requested:
                raise JobCancelledError("Cancellation requested")
            if pause_requested:
                raise JobPausedError("Pause requested", reason="user")
            # If recovery on a sibling pod re-queued this job (due to a
            # heartbeat gap), our status is no longer 'running'.  Pause so
            # the re-queued instance can start cleanly instead of two threads
            # executing the same job in parallel.
            if status != "running":
                raise JobPausedError(
                    f"Job evicted by recovery (status='{status}') — yielding to re-queued instance",
                    reason="shutdown",
                )

        try:
            handler = _JOB_HANDLERS.get(job_type)
            if handler is None:
                raise RuntimeError(f"Unsupported job type: {job_type}")

            _ctx = JobContext(
                db=db,
                job=job,
                params=params,
                resume_from=resume_from,
                app=app,
                _assert_not_cancelled=_assert_not_cancelled,
                _enqueue_job=enqueue_job,
            )
            stats, done_msg = handler(_ctx)

            crud.mark_completed(db, job, message=done_msg, stats=stats)
            crud.create_event(db, job_id=job.id, level="info", message=done_msg)
            for _w in (stats.get("warnings") or [])[:10]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_w))
            for _err in (stats.get("errors") or [])[:50]:
                crud.create_event(db, job_id=job.id, level="warn", message=str(_err))
            _maybe_send_job_notification(db, job=job, event="completed", stats=stats)
            outcome = "success"

        except JobWaitingExternalSignal:
            # Job transitioned to waiting_external — already committed by the
            # handler; nothing else to do.
            outcome = "waiting_external"

        except JobPausedError as _pause_exc:
            current_stats = json.loads(job.stats_json) if job.stats_json else {}
            done_n = job.progress_done or 0
            total_n = job.progress_total
            if _pause_exc.requeue:
                pause_msg = f"Preempted at {done_n}" + (f"/{total_n}" if total_n else "") + " — requeued"
                crud.mark_paused(db, job, message=pause_msg, stats=current_stats, reason=_pause_exc.reason)
                crud.resume_paused_job(db, job, bump_queued_at=True)
            else:
                pause_msg = f"Paused at {done_n}" + (f"/{total_n}" if total_n else "")
                crud.mark_paused(db, job, message=pause_msg, stats=current_stats, reason=_pause_exc.reason)
            crud.create_event(db, job_id=job.id, level="info", message=pause_msg)
            outcome = "paused"

        except JobCancelledError:
            msg = "Cancelled by user"
            _refund_job_credits_if_needed(db, job=job, reason="cancelled")
            crud.mark_cancelled(db, job, message=msg)
            crud.create_event(db, job_id=job.id, level="warn", message=msg)
            outcome = "cancelled"

        except Exception as exc:  # noqa: BLE001
            err = traceback.format_exc()
            summary = f"{type(exc).__name__}: {exc}".strip()
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.error("Job %s (%s) failed:\n%s", job.id, job_type, err)
            _refund_job_credits_if_needed(db, job=job, reason="failed")
            crud.mark_failed(db, job, error=err, message=summary)
            crud.create_event(db, job_id=job.id, level="error", message=summary)
            crud.create_event(db, job_id=job.id, level="debug", message=err)
            _maybe_send_job_notification(db, job=job, event="failed", summary=summary)
            outcome = "failed"

        finally:
            _hb_stop.set()
            # Previously only the cancelled-before-start branch recorded this,
            # so job_duration_seconds observed nothing for real runs.
            record_job_duration(job_type, time.monotonic() - job_start_time, outcome)


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


# Set by `kick_job_worker` to wake an idle poller immediately instead of making
# a freshly enqueued job wait out `_JOB_POLL_INTERVAL`.
_wake_event = threading.Event()


def _run_recovery_sweep() -> None:
    """Re-queue jobs orphaned by a dead pod.

    Runs on a timer from the worker loop regardless of queue depth.  It used to
    live inside the "queue is empty" branch, which meant it never ran at all on
    a pod without a JOB_TYPE_WHITELIST, and on whitelist pods a sustained
    backlog starved it indefinitely — exactly when orphan recovery matters most.
    """
    try:
        with SessionLocal() as db:
            recovered = crud.requeue_interrupted_jobs(db)
            if recovered:
                logger.info("Stale-job sweep: recovered %d interrupted job(s)", recovered)
            resumed = crud.resume_all_paused_jobs(db, min_heartbeat_age_seconds=120)
            if resumed:
                logger.info("Stale-job sweep: resumed %d auto-paused job(s) with stale heartbeat", resumed)
    except Exception:  # noqa: BLE001
        logger.error("Stale-job sweep failed", exc_info=True)


def _job_worker_loop(app) -> None:
    """Long-lived daemon: claim queued jobs and run them, up to `concurrency`.

    One loop shape for every concurrency level — at concurrency=1 the pool
    simply holds a single slot.  The loop never exits on an empty queue; it
    parks on `_wake_event` so a local enqueue starts work immediately and a
    remote one is picked up within `_JOB_POLL_INTERVAL`.
    """
    from concurrent.futures import ThreadPoolExecutor, wait as _fut_wait, FIRST_COMPLETED

    whitelist = _get_job_type_whitelist()
    blacklist = _get_job_type_blacklist()
    concurrency = max(1, _JOB_WORKER_CONCURRENCY)

    if whitelist:
        logger.info(
            "Job worker started (concurrency=%d) — handling job types: %s",
            concurrency, ", ".join(sorted(whitelist)),
        )
    elif blacklist:
        logger.info(
            "Job worker started (concurrency=%d) — handling all job types except: %s",
            concurrency, ", ".join(sorted(blacklist)),
        )
    else:
        logger.info("Job worker started (concurrency=%d) — handling all job types", concurrency)

    last_recovery = 0.0  # sweep once on the first iteration

    try:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job-worker") as pool:
            in_flight: set = set()
            while not _shutdown_event.is_set():
                # Drain finished slots.
                for fut in [f for f in in_flight if f.done()]:
                    in_flight.discard(fut)
                    try:
                        fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Unexpected error from job thread: %s", exc, exc_info=True)

                if in_flight:
                    _jobs_drained.clear()
                else:
                    _jobs_drained.set()

                now = time.monotonic()
                if now - last_recovery >= _STALE_JOB_RECOVERY_INTERVAL:
                    last_recovery = now
                    _run_recovery_sweep()

                # Fill free slots.  claim_next_job() flips the row to 'running'
                # in the same statement that selects it, so a claimed job can
                # never be handed out twice — no in-flight id bookkeeping, and
                # slots fill in one pass instead of one per poll interval.
                claimed_any = False
                while len(in_flight) < concurrency and not _shutdown_event.is_set():
                    try:
                        with SessionLocal() as db:
                            job_id = crud.claim_next_job(
                                db,
                                job_type_whitelist=whitelist,
                                job_type_blacklist=blacklist,
                            )
                    except Exception:  # noqa: BLE001
                        logger.error("Job claim failed", exc_info=True)
                        break
                    if job_id is None:
                        break
                    claimed_any = True
                    _jobs_drained.clear()
                    in_flight.add(pool.submit(_run_job, app, job_id))

                if in_flight:
                    # Wake as soon as a slot frees up so the queue keeps moving.
                    _fut_wait(list(in_flight), timeout=_JOB_POLL_INTERVAL, return_when=FIRST_COMPLETED)
                elif not claimed_any:
                    # Idle: park until something is enqueued locally or the poll
                    # interval elapses (work queued by another pod).
                    _wake_event.wait(_JOB_POLL_INTERVAL)
                    _wake_event.clear()

            # Shutdown: stop claiming, let in-flight jobs reach a checkpoint and
            # persist themselves as paused.  `request_shutdown` is waiting on
            # `_jobs_drained`, and the pool's __exit__ joins the threads.
            if in_flight:
                logger.info("Shutdown — waiting for %d in-flight job(s) to checkpoint", len(in_flight))
                _fut_wait(list(in_flight), timeout=_SHUTDOWN_JOIN_TIMEOUT)
    finally:
        _jobs_drained.set()
        with _worker_lock:
            _worker_threads.pop("job", None)
        logger.info("Job worker loop exited")


_LLM_POLL_INTERVAL = 300  # 5 minutes


def _llm_poll_loop() -> None:
    """Daemon thread: poll Anthropic Batch API jobs every 5 minutes.

    Polls first, then sleeps — sleeping first left `waiting_external` batches
    unchecked for 5 minutes after every restart.
    """
    while not _shutdown_event.is_set():
        try:
            poll_llm_batches()
        except Exception:  # noqa: BLE001
            logger.error("LLM poll loop error", exc_info=True)
        time.sleep(_LLM_POLL_INTERVAL)


# Guards worker-thread startup.  Without it two concurrent kick_job_worker()
# calls — routes, schedulers and enqueue_job all call it — could both observe
# "not running" and start a second loop, after which the first to exit cleared
# the shared flag while the other was still polling.
_worker_lock = threading.Lock()
_worker_threads: dict[str, threading.Thread] = {}


def _ensure_thread(name: str, target, thread_name: str, args: tuple = ()) -> None:
    """Start `target` in a daemon thread unless one is already alive for `name`."""
    with _worker_lock:
        existing = _worker_threads.get(name)
        if existing is not None and existing.is_alive():
            return
        t = threading.Thread(target=target, args=args, daemon=True, name=thread_name)
        _worker_threads[name] = t
        t.start()


def _ensure_job_worker(app) -> None:
    if getattr(app.state, "disable_job_worker", False):
        return
    if _shutdown_event.is_set():
        return
    _ensure_thread("llm", _llm_poll_loop, "llm-batch-poller")
    _ensure_thread("job", _job_worker_loop, "job-worker-loop", args=(app,))


def kick_job_worker(app) -> None:
    """Ensure queued jobs are being processed, and wake an idle poller now."""
    _ensure_job_worker(app)
    _wake_event.set()


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
            elif existing.status == "paused":
                # A paused job is "active" for dedup, so it blocks every new
                # enqueue of its type — and a `pause_reason='user'` pause is
                # deliberately skipped by resume_all_paused_jobs. Together those
                # made a permanent silent dead end: the trigger returns 202 with
                # this job's id, the UI reads that as "started", and nothing ever
                # runs. Someone pressing the button IS the instruction to run it,
                # so honour that and re-queue rather than hand back a corpse.
                logger.info(
                    "Dedup hit on PAUSED job %s (type=%s key=%s reason=%s) — "
                    "resuming it instead of returning it idle",
                    existing.id, job_type, dedup_key, existing.pause_reason,
                )
                crud.resume_paused_job(db, existing, bump_queued_at=False)
                crud.create_event(
                    db, job_id=existing.id, level="info",
                    message="Resumed by a new trigger for the same job type",
                )
                # resume_paused_job commits, which EXPIRES every attribute.
                # Expunging an expired instance detaches it mid-flight, and the
                # caller's JobOut.from_orm_obj() then raises DetachedInstanceError
                # trying to reload — a 500 on the trigger route. Reload first.
                db.refresh(existing)
                db.expunge(existing)
                return existing
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
                elif status == "error":
                    err_msg = stats.get("error", "Unknown Anthropic API error")
                    _crud.mark_failed(db, job, error=err_msg, message=err_msg)
                    _crud.create_event(db, job_id=job.id, level="error", message=err_msg)
                # else: still processing — do nothing, retry next poll cycle

    except Exception as exc:  # noqa: BLE001
        logger.error("poll_llm_batches: unexpected error: %s", exc)
