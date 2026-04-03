import json
from datetime import datetime, timedelta, timezone

from app import crud


def test_requeue_recent_abandoned_jobs_requeues_matching_failures(db):
    job = crud.create_job(db, job_type="batch", label="Batch", params={"limit": 10})
    crud.mark_running(db, job, message="Running")
    crud.mark_failed(
        db,
        job,
        message="AbandonedJobError: moved to failed",
        error="Traceback... AbandonedJobError: Worker died",
    )

    recovered = crud.requeue_recent_abandoned_jobs(db)
    assert recovered == 1

    refreshed = crud.get_job(db, job.id)
    assert refreshed is not None
    assert refreshed.status == "queued"
    assert refreshed.error is None
    assert refreshed.started_at is None
    assert refreshed.completed_at is None


def test_requeue_recent_abandoned_jobs_sets_bulk_resume_flag(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={"cantons": ["ZH"]})
    crud.mark_running(db, job, message="Running")
    crud.mark_failed(
        db,
        job,
        message="AbandonedJobError",
        error="AbandonedJobError: stale started job",
    )

    recovered = crud.requeue_recent_abandoned_jobs(db)
    assert recovered == 1

    refreshed = crud.get_job(db, job.id)
    assert refreshed is not None
    params = json.loads(refreshed.params_json or "{}")
    assert params.get("resume") is True


def test_requeue_recent_abandoned_jobs_skips_old_failures(db):
    job = crud.create_job(db, job_type="detail", label="Detail", params={})
    crud.mark_running(db, job, message="Running")
    crud.mark_failed(
        db,
        job,
        message="AbandonedJobError",
        error="AbandonedJobError: stale started job",
    )

    job.completed_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    db.commit()

    recovered = crud.requeue_recent_abandoned_jobs(db, lookback_seconds=1800)
    assert recovered == 0

    refreshed = crud.get_job(db, job.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
