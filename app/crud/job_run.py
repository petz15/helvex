import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
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
    return db.query(JobRun).order_by(JobRun.queued_at.desc()).limit(limit).all()


def list_jobs_for_user(db: Session, *, user_id: int, org_id: int | None, limit: int = 100) -> list[JobRun]:
    q = db.query(JobRun)
    if org_id is not None:
        q = q.filter(or_(JobRun.user_id == user_id, JobRun.org_id == org_id))
    else:
        q = q.filter(JobRun.user_id == user_id)
    return q.order_by(JobRun.queued_at.desc()).limit(limit).all()


def list_org_jobs(db: Session, org_id: int, limit: int = 100) -> list[JobRun]:
    """List jobs scoped to a specific org (excludes catalog/superadmin jobs with org_id=None)."""
    return (
        db.query(JobRun)
        .filter(JobRun.org_id == org_id)
        .order_by(JobRun.queued_at.desc())
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


def get_next_queued_job(db: Session) -> JobRun | None:
    return (
        db.query(JobRun)
        .filter(JobRun.status == "queued")
        .order_by(JobRun.queued_at.asc())
        .first()
    )


def list_queued_jobs(db: Session) -> list[JobRun]:
    return (
        db.query(JobRun)
        .filter(JobRun.status == "queued")
        .order_by(JobRun.queued_at.asc())
        .all()
    )


def requeue_interrupted_jobs(
    db: Session,
    *,
    message: str = "Recovered after application restart",
    stale_after_seconds: int = 120,
) -> int:
    """Move interrupted running jobs back to queued so they can resume.

    Only re-queues jobs whose heartbeat is stale (older than *stale_after_seconds*)
    or missing entirely.  Jobs with a recent heartbeat are still alive on a worker
    pod and must not be double-executed.
    """
    import json as _json
    from datetime import timedelta
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
        job.status = "queued"
        # Clear control flags so a recovered job doesn't instantly cancel/pause.
        job.cancel_requested = False
        job.pause_requested = False
        job.started_at = None
        job.completed_at = None
        # Preserve original queued_at so API created_at remains immutable.
        job.message = _job_message(message)
        job.error = None
        # For bulk jobs mark resume=True so the worker knows to continue the
        # existing CollectionRun checkpoint rather than starting a fresh sweep.
        if job.job_type == "bulk":
            try:
                p = _json.loads(job.params_json or "{}")
            except Exception:
                p = {}
            p["resume"] = True
            job.params_json = _json.dumps(p)
    if jobs:
        db.commit()
    return len(jobs)


def requeue_recent_abandoned_jobs(
    db: Session,
    *,
    message: str = "Recovered after worker restart (RQ abandoned job)",
    lookback_seconds: int = 1800,
) -> int:
    """Re-queue jobs recently failed with RQ AbandonedJobError.

    During a full deploy restart, RQ can mark in-flight work as abandoned and
    fail it with AbandonedJobError before our in-process runner can pause it.
    This helper converts those recent, infrastructure-induced failures back to
    queued so they can resume from their existing progress checkpoint.
    """
    import json as _json
    from datetime import timedelta

    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=lookback_seconds)
    jobs = (
        db.query(JobRun)
        .filter(
            JobRun.status == "failed",
            JobRun.completed_at.is_not(None),
            JobRun.completed_at >= cutoff,
            JobRun.error.is_not(None),
            JobRun.error.ilike("%AbandonedJobError%"),
        )
        .all()
    )
    for job in jobs:
        job.status = "queued"
        job.cancel_requested = False
        job.pause_requested = False
        job.started_at = None
        job.completed_at = None
        job.message = _job_message(message)
        job.error = None
        # For bulk jobs force checkpoint resume semantics on restart recovery.
        if job.job_type == "bulk":
            try:
                p = _json.loads(job.params_json or "{}")
            except Exception:
                p = {}
            p["resume"] = True
            job.params_json = _json.dumps(p)
    if jobs:
        db.commit()
    return len(jobs)


def mark_running(db: Session, job: JobRun, *, message: str) -> JobRun:
    job.status = "running"
    job.cancel_requested = False
    job.started_at = datetime.now(tz=timezone.utc)
    job.message = _job_message(message)
    db.commit()
    db.refresh(job)
    return job


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


def mark_paused(db: Session, job: JobRun, *, message: str, stats: dict[str, Any] | None = None) -> JobRun:
    """Set job status to 'paused', preserving progress_done as the resume point."""
    job.status = "paused"
    job.pause_requested = False
    job.message = _job_message(message)
    if stats is not None:
        job.stats_json = json.dumps(stats)
    db.commit()
    db.refresh(job)
    return job


def resume_paused_job(db: Session, job: JobRun) -> JobRun:
    """Re-queue a paused job so the worker picks it up from progress_done."""
    job.status = "queued"
    job.pause_requested = False
    job.started_at = None
    job.completed_at = None
    job.message = _job_message(f"Resuming from {job.progress_done or 0}…")
    db.commit()
    db.refresh(job)
    return job


def resume_all_paused_jobs(db: Session) -> int:
    """Re-queue all paused jobs after a restart so they resume automatically.

    Called on startup after requeue_interrupted_jobs().  Jobs paused by the
    graceful shutdown handler are re-queued here; jobs paused by user action
    before the restart are also re-queued (documented behaviour — users who
    want a job to stay paused should cancel it instead).
    """
    jobs = db.query(JobRun).filter(JobRun.status == "paused").all()
    for job in jobs:
        job.status = "queued"
        job.pause_requested = False
        job.started_at = None
        job.completed_at = None
        # Preserve original queued_at so API created_at remains immutable.
        job.message = _job_message(f"Auto-resumed from {job.progress_done or 0} after restart")
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
