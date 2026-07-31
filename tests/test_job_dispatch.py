"""Dispatch-level tests for `_run_job`.

`_run_job` had no coverage at all, which is how a broken progress callback in
the csv_export handler (a positional arg passed to a keyword-only lambda) went
unnoticed even though it failed every export on its first batch.
"""
import json
from unittest.mock import patch

import pytest

from app import crud
from app.services.jobs import job_worker
from app.services.jobs.job_handlers import JobContext, JobWaitingExternalSignal
from app.services.jobs.job_worker import JobCancelledError, JobPausedError


@pytest.fixture
def worker_db(db, monkeypatch):
    """Point job_worker's SessionLocal at the in-memory test session.

    `_run_job` opens its own sessions, so it needs the test engine rather than
    the configured PostgreSQL one.
    """
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(job_worker, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.crud.job_run.SessionLocal", TestingSessionLocal, raising=False)
    return db


def _run(job_id):
    job_worker._run_job(None, job_id)


def _reload(db, job_id):
    """Re-read a job after `_run_job` wrote it through its own session."""
    db.expire_all()
    return crud.get_job(db, job_id)


# ── dispatch ──────────────────────────────────────────────────────────────────

def test_unknown_job_type_marks_job_failed(worker_db):
    job = crud.create_job(worker_db, job_type="does_not_exist", label="Bogus", params={})
    crud.claim_next_job(worker_db)

    _run(job.id)

    refreshed = _reload(worker_db, job.id)
    assert refreshed.status == "failed"
    assert "Unsupported job type" in (refreshed.error or "")


def test_successful_handler_marks_completed_with_stats(worker_db):
    def _handler(ctx: JobContext):
        return {"processed": 7}, "Done — 7 processed"

    job = crud.create_job(worker_db, job_type="detail", label="Detail", params={})
    crud.claim_next_job(worker_db)

    with patch.dict(job_worker_handlers(), {"detail": _handler}):
        _run(job.id)

    refreshed = _reload(worker_db, job.id)
    assert refreshed.status == "completed"
    assert refreshed.message == "Done — 7 processed"
    assert json.loads(refreshed.stats_json)["processed"] == 7


def test_handler_receives_resume_from_progress_done(worker_db):
    seen = {}

    def _handler(ctx: JobContext):
        seen["resume_from"] = ctx.resume_from
        seen["params"] = ctx.params
        return {}, "ok"

    job = crud.create_job(worker_db, job_type="detail", label="Detail", params={"limit": 5})
    crud.update_progress(worker_db, job, done=42, total=100)
    crud.claim_next_job(worker_db)

    with patch.dict(job_worker_handlers(), {"detail": _handler}):
        _run(job.id)

    assert seen["resume_from"] == 42
    assert seen["params"] == {"limit": 5}


def test_waiting_external_signal_leaves_status_untouched(worker_db):
    def _handler(ctx: JobContext):
        crud.mark_waiting_external(ctx.db, ctx.job, message="Batch submitted")
        raise JobWaitingExternalSignal()

    job = crud.create_job(worker_db, job_type="claude_classify", label="Claude", params={})
    crud.claim_next_job(worker_db)

    with patch.dict(job_worker_handlers(), {"claude_classify": _handler}):
        _run(job.id)

    refreshed = _reload(worker_db, job.id)
    assert refreshed.status == "waiting_external"


# ── pause / cancel ────────────────────────────────────────────────────────────

def test_user_pause_records_user_reason(worker_db):
    def _handler(ctx: JobContext):
        raise JobPausedError("Pause requested", reason="user")

    job = crud.create_job(worker_db, job_type="bulk", label="Bulk", params={})
    crud.claim_next_job(worker_db)

    with patch.dict(job_worker_handlers(), {"bulk": _handler}):
        _run(job.id)

    refreshed = _reload(worker_db, job.id)
    assert refreshed.status == "paused"
    assert refreshed.pause_reason == "user"
    # A user pause must survive the recovery sweep.
    worker_db.expire_all()
    assert crud.resume_all_paused_jobs(worker_db) == 0


def test_preemption_requeues_immediately_as_preempt(worker_db):
    def _handler(ctx: JobContext):
        raise JobPausedError("Yielding to ML job", requeue=True)

    job = crud.create_job(worker_db, job_type="web_crawl_http", label="Crawl", params={})
    crud.claim_next_job(worker_db)

    with patch.dict(job_worker_handlers(), {"web_crawl_http": _handler}):
        _run(job.id)

    refreshed = _reload(worker_db, job.id)
    # Preemption re-queues rather than parking the job.
    assert refreshed.status == "queued"


def test_cancelled_job_marks_cancelled(worker_db):
    def _handler(ctx: JobContext):
        raise JobCancelledError("Cancellation requested")

    job = crud.create_job(worker_db, job_type="detail", label="Detail", params={})
    crud.claim_next_job(worker_db)

    with patch.dict(job_worker_handlers(), {"detail": _handler}):
        _run(job.id)

    assert _reload(worker_db, job.id).status == "cancelled"


def test_shutdown_flag_pauses_running_job_as_shutdown(worker_db):
    def _handler(ctx: JobContext):
        job_worker._shutdown_event.set()
        ctx.assert_not_cancelled()  # must raise
        raise AssertionError("checkpoint did not trip on shutdown")

    job = crud.create_job(worker_db, job_type="bulk", label="Bulk", params={})
    crud.claim_next_job(worker_db)

    try:
        with patch.dict(job_worker_handlers(), {"bulk": _handler}):
            _run(job.id)
    finally:
        job_worker.reset_shutdown()

    refreshed = _reload(worker_db, job.id)
    assert refreshed.status == "paused"
    assert refreshed.pause_reason == "shutdown"


# ── JobContext surface ────────────────────────────────────────────────────────

def test_job_context_progress_callbacks_persist_and_do_not_raise(worker_db):
    """Guards the class of bug that broke csv_export: a progress callback whose
    signature no longer matched what the handler passed it."""
    def _handler(ctx: JobContext):
        ctx.progress(10, 100, {"written": 10}, "Exported 10/100 rows…")
        ctx.progress_no_event(20, 100, {"written": 20}, "Exported 20/100 rows…")
        ctx.status("Working…")
        ctx.status_with_stats("Still working…")
        ctx.event("info", "hello")
        return {"written": 20}, "Done"

    job = crud.create_job(worker_db, job_type="csv_export", label="Export", params={})
    crud.claim_next_job(worker_db)

    with patch.dict(job_worker_handlers(), {"csv_export": _handler}):
        _run(job.id)

    refreshed = _reload(worker_db, job.id)
    assert refreshed.status == "completed"
    assert refreshed.progress_done == 20


def test_csv_export_progress_callback_signature_matches_exporter(worker_db):
    """The real csv_export handler's `_progress` must accept what
    `run_csv_export` calls it with: (done, total, stats)."""
    from app.services.jobs.job_handlers import export

    captured = {}

    def _fake_run_csv_export(db, *, params, user_id, org_id, progress_cb):
        progress_cb(5, 10, {"written": 5})  # exactly how csv_export.py calls it
        captured["called"] = True
        return {"row_count": 10, "download_url": "s3://x"}

    job = crud.create_job(worker_db, job_type="csv_export", label="Export", params={})
    crud.claim_next_job(worker_db)

    with patch("app.services.platform.csv_export.run_csv_export", _fake_run_csv_export), \
         patch("app.services.platform.s3_client.is_configured", lambda: True), \
         patch.dict(job_worker_handlers(), {"csv_export": export.handle_csv_export}):
        _run(job.id)

    assert captured.get("called") is True
    refreshed = _reload(worker_db, job.id)
    assert refreshed.status == "completed", refreshed.error


def job_worker_handlers():
    """The live handler registry `_run_job` dispatches through."""
    from app.services.jobs import job_handlers

    return job_handlers.JOB_HANDLERS
