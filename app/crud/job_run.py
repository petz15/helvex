import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, update as _sql_update
from sqlalchemy.orm import Session

from app.models.job_run_event import JobRunEvent
from app.models.job_run import JobRun


FINAL_STATUSES = {"completed", "failed", "cancelled"}
JOB_MESSAGE_MAX_LEN = 512


def _job_message(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= JOB_MESSAGE_MAX_LEN:
        return value
    return value[: JOB_MESSAGE_MAX_LEN - 3] + "..."


def create_job(
    db: Session,
    *,
    job_type: str,
    label: str,
    params: dict[str, Any] | None = None,
    org_id: int | None = None,
    user_id: int | None = None,
    dedup_key: str | None = None,
    initial_stats: dict[str, Any] | None = None,
) -> JobRun:
    job = JobRun(
        job_type=job_type,
        label=label,
        status="queued",
        message=_job_message("Queued"),
        params_json=json.dumps(params or {}),
        stats_json=json.dumps(initial_stats) if initial_stats else None,
        org_id=org_id,
        user_id=user_id,
        dedup_key=dedup_key,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def find_active_by_dedup_key(db: Session, dedup_key: str) -> "JobRun | None":
    """Return the first active job with this dedup key, or None.

    'Active' means not yet in a terminal state.  Callers use this to avoid
    creating a duplicate job when one is already queued/running/paused.
    """
    return (
        db.query(JobRun)
        .filter(
            JobRun.dedup_key == dedup_key,
            JobRun.status.in_(["queued", "running", "paused", "waiting_external"]),
        )
        .order_by(JobRun.queued_at.asc())
        .first()
    )


def update_heartbeat(db: Session, job_id: int) -> None:
    """Stamp last_heartbeat_at = now() for the given job.

    Called by the per-job heartbeat daemon thread every ~30 s while the
    job is running.  Used by requeue_interrupted_jobs() to distinguish
    live jobs from truly interrupted ones.
    """
    now = datetime.now(tz=timezone.utc)
    db.query(JobRun).filter(JobRun.id == job_id).update(
        {"last_heartbeat_at": now},
        synchronize_session=False,
    )
    db.commit()


def get_job(db: Session, job_id: int) -> JobRun | None:
    return db.get(JobRun, job_id)


def list_jobs(db: Session, limit: int = 50) -> list[JobRun]:
    # NULLS FIRST puts active jobs (no completed_at) above finished ones; finished jobs sort by end time
    return db.query(JobRun).order_by(JobRun.completed_at.desc().nullsfirst(), JobRun.queued_at.desc()).limit(limit).all()


def list_jobs_for_user(db: Session, *, user_id: int, org_id: int | None, limit: int = 100) -> list[JobRun]:
    q = db.query(JobRun)
    if org_id is not None:
        q = q.filter(or_(JobRun.user_id == user_id, JobRun.org_id == org_id))
    else:
        q = q.filter(JobRun.user_id == user_id)
    return q.order_by(JobRun.completed_at.desc().nullsfirst(), JobRun.queued_at.desc()).limit(limit).all()


def list_org_jobs(db: Session, org_id: int, limit: int = 100) -> list[JobRun]:
    """List jobs scoped to a specific org (excludes catalog/superadmin jobs with org_id=None)."""
    return (
        db.query(JobRun)
        .filter(JobRun.org_id == org_id)
        .order_by(JobRun.completed_at.desc().nullsfirst(), JobRun.queued_at.desc())
        .limit(limit)
        .all()
    )


def list_active_jobs(db: Session) -> list[JobRun]:
    return (
        db.query(JobRun)
        .filter(JobRun.status.in_(["queued", "running", "paused"]))
        .order_by(JobRun.queued_at.asc())
        .all()
    )


def list_active_jobs_for_user(db: Session, *, user_id: int, org_id: int | None) -> list[JobRun]:
    q = db.query(JobRun).filter(JobRun.status.in_(["queued", "running", "paused", "waiting_external"]))
    if org_id is not None:
        q = q.filter(or_(JobRun.user_id == user_id, JobRun.org_id == org_id))
    else:
        q = q.filter(JobRun.user_id == user_id)
    return q.order_by(JobRun.queued_at.asc()).all()


def get_next_queued_job(
    db: Session,
    job_type_whitelist: set[str] | None = None,
    job_type_blacklist: set[str] | None = None,
) -> JobRun | None:
    """Peek at the next queued job WITHOUT claiming it.

    For diagnostics and tests only. Workers must use `claim_next_job()` — a
    peek-then-claim pair hands the same row to every polling worker and lets
    all but one waste a round trip losing the claim.
    """
    q = db.query(JobRun).filter(JobRun.status == "queued")
    if job_type_whitelist:
        q = q.filter(JobRun.job_type.in_(job_type_whitelist))
    if job_type_blacklist:
        q = q.filter(JobRun.job_type.notin_(job_type_blacklist))
    return q.order_by(JobRun.queued_at.asc()).first()


def claim_next_job(
    db: Session,
    *,
    job_type_whitelist: set[str] | None = None,
    job_type_blacklist: set[str] | None = None,
    message: str = "Starting…",
) -> int | None:
    """Atomically select AND claim the oldest eligible queued job.

    Returns the claimed job id, or None when nothing is available.

    Select and claim happen in ONE statement, so a claimed row is never handed
    to a second caller — within this pod or across pods. The previous design
    selected in one session (`FOR UPDATE SKIP LOCKED`, whose lock was dropped
    the moment that session closed, making SKIP LOCKED a no-op) and claimed in
    another. That left two costs: every losing pod burned a poll+claim round
    trip, and locally a job stayed `queued` between `pool.submit()` and its
    thread actually starting, so the next poll re-drew the same row, tripped the
    in-flight guard and aborted slot filling — one slot per poll interval.

    `NOT cancel_requested` is in the WHERE clause rather than being cleared in
    the SET: clearing it unconditionally could silently swallow a cancel that
    arrived while the job was pausing for shutdown and was then re-queued by the
    recovery sweep.
    """
    now = datetime.now(tz=timezone.utc)

    inner = db.query(JobRun.id).filter(
        JobRun.status == "queued",
        or_(JobRun.cancel_requested.is_(False), JobRun.cancel_requested.is_(None)),
    )
    if job_type_whitelist:
        inner = inner.filter(JobRun.job_type.in_(job_type_whitelist))
    if job_type_blacklist:
        inner = inner.filter(JobRun.job_type.notin_(job_type_blacklist))
    inner = (
        inner.order_by(JobRun.queued_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )

    result = db.execute(
        _sql_update(JobRun)
        .where(JobRun.id == inner)
        .values(
            status="running",
            started_at=now,
            last_heartbeat_at=now,
            pause_requested=False,
            pause_reason=None,
            message=_job_message(message),
        )
        .returning(JobRun.id)
        .execution_options(synchronize_session=False)
    )
    row = result.first()
    db.commit()
    return int(row[0]) if row else None


def get_job_flags(db: Session, job_id: int) -> tuple[str, bool, bool] | None:
    """Return (status, cancel_requested, pause_requested), or None if gone.

    Reads three columns instead of loading the row, because a running job's
    cancel checkpoint calls this per unit of work. Callers inside a job MUST
    pass a short-lived session, not the handler's own — reading through the
    handler's session would autoflush its pending work at an arbitrary point
    mid-batch.
    """
    row = (
        db.query(JobRun.status, JobRun.cancel_requested, JobRun.pause_requested)
        .filter(JobRun.id == job_id)
        .first()
    )
    if row is None:
        return None
    return str(row[0]), bool(row[1]), bool(row[2])


MAX_RESTART_COUNT = 5


def _force_bulk_resume(job: JobRun) -> None:
    """Mark a re-queued 'bulk' job to continue its CollectionRun checkpoint.

    `bulk_import_zefix` tracks progress in a separate `CollectionRun` row
    (last_canton/last_offset), not `job_runs.progress_done` — the generic
    `resume_from` plumbing every other handler uses does nothing for it. Its
    only switch is `params['resume']`: without this, every automatic
    re-queue (crash recovery, graceful shutdown pause, preemption) restarts
    the whole Zefix sweep from canton A instead of continuing.
    """
    try:
        p = json.loads(job.params_json or "{}")
    except Exception:
        p = {}
    p["resume"] = True
    job.params_json = json.dumps(p)


def requeue_interrupted_jobs(
    db: Session,
    *,
    message: str = "Recovered after application restart",
    stale_after_seconds: int = 300,
) -> int:
    """Move interrupted running jobs back to queued so they can resume.

    Only re-queues jobs whose heartbeat is stale (older than *stale_after_seconds*)
    or missing entirely.  Jobs with a recent heartbeat are still alive on a worker
    pod and must not be double-executed.

    Threshold is 300 s (5 min): heartbeat daemon fires every 30 s, so a live job
    is never more than ~30 s stale.  The old 120 s threshold was too tight — a
    transient DB connection hiccup during a heavy batch job could silently fail
    several consecutive heartbeats, crossing the threshold and re-queuing a still-
    running job onto a second pod.

    Jobs that have been restarted more than MAX_RESTART_COUNT times are killed
    instead of re-queued to prevent infinite crash loops.
    """
    import logging as _logging
    from datetime import timedelta
    _logger = _logging.getLogger(__name__)
    stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=stale_after_seconds)
    jobs = (
        db.query(JobRun)
        .filter(
            JobRun.status == "running",
            or_(
                JobRun.last_heartbeat_at.is_(None),
                JobRun.last_heartbeat_at < stale_cutoff,
            ),
        )
        .all()
    )
    for job in jobs:
        job.restart_count = (job.restart_count or 0) + 1
        if job.restart_count > MAX_RESTART_COUNT:
            error_msg = f"Max retries exceeded ({job.restart_count - 1}/{MAX_RESTART_COUNT} restarts)"
            job.status = "failed"
            job.error = error_msg
            job.completed_at = datetime.now(tz=timezone.utc)
            job.message = _job_message(error_msg)
            _logger.error(
                "Job %s (type=%s) killed after %d restarts — max retries exceeded",
                job.id, job.job_type, job.restart_count - 1,
            )
            continue
        if job.cancel_requested:
            job.status = "cancelled"
            job.completed_at = datetime.now(tz=timezone.utc)
            job.message = _job_message("Cancelled (honoured during crash recovery)")
            _logger.info(
                "Job %s (type=%s) cancelled during crash recovery — cancel was pending",
                job.id, job.job_type,
            )
            continue
        job.status = "queued"
        # Clear pause flag only; cancel was already checked above.
        job.pause_requested = False
        job.started_at = None
        job.completed_at = None
        # Preserve original queued_at so API created_at remains immutable.
        job.message = _job_message(message)
        job.error = None
        _logger.warning(
            "Job %s (type=%s) requeued after crash (restart %d/%d)",
            job.id, job.job_type, job.restart_count, MAX_RESTART_COUNT,
        )
        if job.job_type == "bulk":
            _force_bulk_resume(job)
    if jobs:
        db.commit()
    return len(jobs)


def mark_cancel_requested(db: Session, job: JobRun) -> JobRun:
    job.cancel_requested = True
    db.commit()
    db.refresh(job)
    return job


def mark_cancelled(db: Session, job: JobRun, *, message: str) -> JobRun:
    job.status = "cancelled"
    job.message = _job_message(message)
    job.completed_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def create_event(db: Session, *, job_id: int, level: str, message: str) -> JobRunEvent:
    event = JobRunEvent(job_id=job_id, level=level, message=message)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_events(
    db: Session,
    *,
    job_id: int,
    limit: int = 50,
    exclude_debug: bool = False,
) -> list[JobRunEvent]:
    q = db.query(JobRunEvent).filter(JobRunEvent.job_id == job_id)
    if exclude_debug:
        q = q.filter(JobRunEvent.level != "debug")
    return q.order_by(JobRunEvent.created_at.desc(), JobRunEvent.id.desc()).limit(limit).all()


def update_progress(
    db: Session,
    job: JobRun,
    *,
    message: str | None = None,
    done: int | None = None,
    total: int | None = None,
    stats: dict[str, Any] | None = None,
) -> JobRun:
    if message is not None:
        job.message = _job_message(message)
    if done is not None:
        job.progress_done = done
    if total is not None:
        job.progress_total = total
    if stats is not None:
        job.stats_json = json.dumps(stats)
    db.commit()
    db.refresh(job)
    return job


def mark_completed(db: Session, job: JobRun, *, message: str, stats: dict[str, Any] | None = None) -> JobRun:
    job.status = "completed"
    job.message = _job_message(message)
    job.completed_at = datetime.now(tz=timezone.utc)
    if stats is not None:
        job.stats_json = json.dumps(stats)
    db.commit()
    db.refresh(job)
    return job


def mark_failed(
    db: Session,
    job: JobRun,
    *,
    error: str,
    message: str = "Failed",
    stats: dict[str, Any] | None = None,
) -> JobRun:
    job.status = "failed"
    job.message = _job_message(message)
    job.error = error
    job.completed_at = datetime.now(tz=timezone.utc)
    if stats is not None:
        job.stats_json = json.dumps(stats)
    db.commit()
    db.refresh(job)
    return job


def mark_pause_requested(db: Session, job: JobRun) -> JobRun:
    job.pause_requested = True
    db.commit()
    db.refresh(job)
    return job


#: Pause reasons that the recovery sweep is allowed to auto-resume. A pause the
#: user asked for is deliberately excluded — see `resume_all_paused_jobs`.
AUTO_RESUMABLE_PAUSE_REASONS = ("shutdown", "preempt")


def mark_paused(
    db: Session,
    job: JobRun,
    *,
    message: str,
    stats: dict[str, Any] | None = None,
    reason: str = "shutdown",
) -> JobRun:
    """Set job status to 'paused', preserving progress_done as the resume point.

    ``reason`` records WHO paused the job ('user' | 'shutdown' | 'preempt') and
    is what stops `resume_all_paused_jobs` from restarting a job a person
    deliberately stopped.
    """
    job.status = "paused"
    job.pause_requested = False
    job.pause_reason = reason
    job.message = _job_message(message)
    if stats is not None:
        job.stats_json = json.dumps(stats)
    db.commit()
    db.refresh(job)
    return job


def resume_paused_job(db: Session, job: JobRun) -> JobRun:
    """Re-queue a paused job so the worker picks it up from progress_done.

    Used both for the immediate preempt-requeue path and the manual
    "Resume" API action — in both cases the intent is to continue, never to
    restart, so bulk jobs get the same `params['resume']` patch as the
    crash-recovery path (see `_force_bulk_resume`).
    """
    job.status = "queued"
    job.pause_requested = False
    job.pause_reason = None
    job.started_at = None
    job.completed_at = None
    job.message = _job_message(f"Resuming from {job.progress_done or 0}…")
    if job.job_type == "bulk":
        _force_bulk_resume(job)
    db.commit()
    db.refresh(job)
    return job


def resume_all_paused_jobs(
    db: Session,
    *,
    min_heartbeat_age_seconds: int = 0,
) -> int:
    """Re-queue jobs that were paused by infrastructure, so they self-heal.

    Only pauses with ``pause_reason`` in ``AUTO_RESUMABLE_PAUSE_REASONS`` are
    touched. A job paused from the UI (``pause_reason='user'``) is left alone:
    this sweep runs at boot and every few minutes, so previously any job a user
    paused restarted itself within ~3 minutes, and there was no way to keep a
    job stopped. Rows predating the ``pause_reason`` column have NULL and are
    treated as auto-resumable, preserving the old behaviour for them.

    ``min_heartbeat_age_seconds`` guards against the K8s rolling-deploy race:
    a dying pod pauses its job and Pod 2 starts almost simultaneously.  If the
    paused job still has a recent heartbeat (< ``min_heartbeat_age_seconds``
    old) the dying pod is still mid-batch, so we skip it here and let the next
    sweep pick it up once the heartbeat goes stale.
    Pass 0 (default) to re-queue all eligible paused jobs immediately.
    """
    from datetime import timedelta
    query = db.query(JobRun).filter(
        JobRun.status == "paused",
        or_(
            JobRun.pause_reason.is_(None),
            JobRun.pause_reason.in_(AUTO_RESUMABLE_PAUSE_REASONS),
        ),
    )
    if min_heartbeat_age_seconds > 0:
        stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=min_heartbeat_age_seconds)
        query = query.filter(
            or_(
                JobRun.last_heartbeat_at.is_(None),
                JobRun.last_heartbeat_at < stale_cutoff,
            )
        )
    jobs = query.all()
    for job in jobs:
        job.status = "queued"
        job.pause_requested = False
        job.pause_reason = None
        job.started_at = None
        job.completed_at = None
        job.message = _job_message(f"Auto-resumed from {job.progress_done or 0} after restart")
        if job.job_type == "bulk":
            _force_bulk_resume(job)
    if jobs:
        db.commit()
    return len(jobs)


def mark_waiting_external(
    db: Session,
    job: JobRun,
    *,
    message: str,
    params: dict[str, Any] | None = None,
) -> JobRun:
    """Set job status to 'waiting_external' (e.g. Anthropic Batch API submitted, awaiting results)."""
    job.status = "waiting_external"
    job.message = _job_message(message)
    if params is not None:
        job.params_json = json.dumps(params)
    db.commit()
    db.refresh(job)
    return job


def get_latest_csv_export(db: Session, user_id: int) -> JobRun | None:
    """Return the most recent csv_export job for this user (any status)."""
    return (
        db.query(JobRun)
        .filter(JobRun.job_type == "csv_export", JobRun.user_id == user_id)
        .order_by(JobRun.queued_at.desc())
        .first()
    )


def cancel_active_csv_exports(db: Session, user_id: int) -> None:
    """Cancel any queued/running csv_export jobs for this user before creating a new one."""
    active = (
        db.query(JobRun)
        .filter(
            JobRun.job_type == "csv_export",
            JobRun.user_id == user_id,
            JobRun.status.in_(["queued", "running", "paused"]),
        )
        .all()
    )
    now = datetime.now(tz=timezone.utc)
    for job in active:
        job.status = "cancelled"
        job.message = _job_message("Superseded by new export")
        job.completed_at = now
    if active:
        db.commit()


def delete_old_finished_jobs(db: Session, *, keep_days: int = 30) -> int:
    """Delete completed/failed/cancelled jobs older than *keep_days*.

    Keeps the most recent history (default 30 days) to prevent unbounded
    table growth.  Active jobs (queued/running/paused/waiting_external) and
    jobs without a completion timestamp are never deleted.
    """
    from datetime import timedelta
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=keep_days)
    deleted = (
        db.query(JobRun)
        .filter(
            JobRun.status.in_(["completed", "failed", "cancelled"]),
            JobRun.completed_at < cutoff,
        )
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
    return deleted


def list_waiting_llm_batches(db: Session) -> list[JobRun]:
    """Return all claude_classify jobs currently waiting for an external batch to complete."""
    return (
        db.query(JobRun)
        .filter(JobRun.job_type == "claude_classify", JobRun.status == "waiting_external")
        .order_by(JobRun.queued_at.asc())
        .all()
    )


def has_noga_nightly_run_today(db: Session) -> bool:
    """Return True if a noga_nightly reclassify_noga job is active or completed today (UTC).

    Failed and cancelled jobs are excluded so a crash doesn't block the next attempt.
    """
    from datetime import timedelta

    today_start = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_end = today_start + timedelta(days=1)
    return (
        db.query(JobRun)
        .filter(
            JobRun.job_type == "reclassify_noga",
            JobRun.queued_at >= today_start,
            JobRun.queued_at < today_end,
            JobRun.status.notin_(["failed", "cancelled"]),
        )
        .first()
        is not None
    )


def has_shab_daily_run_today(db: Session) -> bool:
    """Return True if a shab_daily job is active or successfully completed today (UTC).

    Failed and cancelled jobs are excluded so a fresh run is enqueued after
    a crash rather than the scheduler staying blocked by a dead job.
    Used by the nightly scheduler to avoid duplicate enqueues.
    """
    from datetime import timedelta

    today_start = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_end = today_start + timedelta(days=1)
    return (
        db.query(JobRun)
        .filter(
            JobRun.job_type == "shab_daily",
            JobRun.queued_at >= today_start,
            JobRun.queued_at < today_end,
            JobRun.status.notin_(["failed", "cancelled"]),
        )
        .first()
        is not None
    )


def has_simap_daily_run_today(db: Session) -> bool:
    """Return True if a simap_daily job is active or successfully completed today (UTC).

    Mirrors ``has_shab_daily_run_today`` — used by the 04:00 Zurich scheduler.
    """
    from datetime import timedelta

    today_start = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_end = today_start + timedelta(days=1)
    return (
        db.query(JobRun)
        .filter(
            JobRun.job_type == "simap_daily",
            JobRun.queued_at >= today_start,
            JobRun.queued_at < today_end,
            JobRun.status.notin_(["failed", "cancelled"]),
        )
        .first()
        is not None
    )
