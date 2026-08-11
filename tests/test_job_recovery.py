"""Job claim, pause and crash-recovery semantics.

Replaces the previous tests for `requeue_recent_abandoned_jobs`, which matched
on an `AbandonedJobError` string produced only by the long-removed RQ worker —
they exercised a function nothing could ever trigger.
"""
import json
from datetime import datetime, timedelta, timezone

from app import crud


def _claim(db, **kwargs):
    return crud.claim_next_job(db, **kwargs)


# ── claim_next_job ────────────────────────────────────────────────────────────

def test_claim_next_job_claims_oldest_and_marks_running(db):
    first = crud.create_job(db, job_type="detail", label="First", params={})
    crud.create_job(db, job_type="detail", label="Second", params={})

    claimed_id = _claim(db)
    assert claimed_id == first.id

    refreshed = crud.get_job(db, claimed_id)
    assert refreshed.status == "running"
    assert refreshed.started_at is not None
    # Stamped on claim so the job is not instantly "stale" to the recovery sweep.
    assert refreshed.last_heartbeat_at is not None


def test_claim_next_job_never_returns_the_same_job_twice(db):
    job = crud.create_job(db, job_type="detail", label="Only", params={})

    assert _claim(db) == job.id
    # The claim flips status in the same statement, so a second poll must not
    # see it — this is what previously let the fill loop re-draw a pooled job.
    assert _claim(db) is None


def test_claim_next_job_skips_cancel_requested(db):
    cancelled = crud.create_job(db, job_type="detail", label="Cancelled", params={})
    crud.mark_cancel_requested(db, cancelled)
    later = crud.create_job(db, job_type="detail", label="Later", params={})

    # The cancel must not be silently cleared by the claim; the job is skipped.
    assert _claim(db) == later.id
    assert crud.get_job(db, cancelled.id).status == "queued"
    assert crud.get_job(db, cancelled.id).cancel_requested is True


def test_claim_next_job_respects_whitelist_and_blacklist(db):
    crud.create_job(db, job_type="reclassify_noga", label="ML", params={})
    api_job = crud.create_job(db, job_type="detail", label="API", params={})

    assert _claim(db, job_type_whitelist={"detail"}) == api_job.id

    other = crud.create_job(db, job_type="detail", label="API2", params={})
    assert _claim(db, job_type_blacklist={"reclassify_noga"}) == other.id


def test_claim_next_job_returns_none_when_queue_empty(db):
    assert _claim(db) is None


def test_get_job_flags_reports_current_state(db):
    job = crud.create_job(db, job_type="detail", label="Flags", params={})
    crud.mark_cancel_requested(db, job)

    status, cancel_requested, pause_requested, _started = crud.get_job_flags(db, job.id)
    assert status == "queued"
    assert cancel_requested is True
    assert pause_requested is False

    assert crud.get_job_flags(db, 999_999) is None


# ── pause semantics ───────────────────────────────────────────────────────────

def test_user_pause_is_not_auto_resumed(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={})
    crud.mark_paused(db, job, message="Paused at 10", reason="user")

    assert crud.resume_all_paused_jobs(db) == 0

    refreshed = crud.get_job(db, job.id)
    assert refreshed.status == "paused"
    assert refreshed.pause_reason == "user"


def test_shutdown_pause_is_auto_resumed(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={})
    crud.mark_paused(db, job, message="Paused at 10", reason="shutdown")

    assert crud.resume_all_paused_jobs(db) == 1

    refreshed = crud.get_job(db, job.id)
    assert refreshed.status == "queued"
    assert refreshed.pause_reason is None


def test_legacy_pause_without_reason_is_auto_resumed(db):
    """Rows predating the pause_reason column keep the old behaviour."""
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={})
    job.status = "paused"
    job.pause_reason = None
    db.commit()

    assert crud.resume_all_paused_jobs(db) == 1
    assert crud.get_job(db, job.id).status == "queued"


def test_resume_paused_job_clears_reason(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={})
    crud.mark_paused(db, job, message="Paused", reason="user")

    crud.resume_paused_job(db, job)

    refreshed = crud.get_job(db, job.id)
    assert refreshed.status == "queued"
    assert refreshed.pause_reason is None


def test_resume_paused_job_sets_bulk_resume_flag(db):
    """A graceful pause (shutdown/preempt/manual) must continue the existing
    CollectionRun checkpoint, not restart the Zefix sweep from canton A —
    the regression this test guards was a deploy silently restarting bulk
    imports from scratch because only the crash-recovery path set this flag.
    """
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={"cantons": ["ZH"]})
    crud.mark_paused(db, job, message="Paused at 10", reason="shutdown")

    crud.resume_paused_job(db, job)

    refreshed = crud.get_job(db, job.id)
    assert json.loads(refreshed.params_json or "{}").get("resume") is True


def test_resume_paused_job_bump_queued_at_yields_priority(db):
    """Regression for the self-preemption livelock (job #12741, 2026-08-01):
    a crawler job that pauses to yield to a higher-priority queued ML job
    must not keep re-winning claim_next_job's oldest-queued_at-first race
    against that very job — otherwise it re-claims itself, re-observes the
    ML job still queued, and re-preempts forever, starving the ML job.
    """
    old_job = crud.create_job(db, job_type="web_crawl_http", label="Crawler", params={})
    crud.mark_paused(db, old_job, message="Preempted", reason="preempt")

    new_job = crud.create_job(db, job_type="reclassify_noga", label="ML", params={})

    crud.resume_paused_job(db, old_job, bump_queued_at=True)

    assert _claim(db) == new_job.id


def test_resume_all_paused_jobs_sets_bulk_resume_flag(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={"cantons": ["ZH"]})
    crud.mark_paused(db, job, message="Paused at 10", reason="shutdown")

    assert crud.resume_all_paused_jobs(db) == 1

    refreshed = crud.get_job(db, job.id)
    assert json.loads(refreshed.params_json or "{}").get("resume") is True


def test_resume_all_paused_jobs_skips_recent_heartbeat(db):
    """Rolling-deploy guard: the dying pod may still be mid-batch."""
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={})
    crud.mark_paused(db, job, message="Paused", reason="shutdown")
    job.last_heartbeat_at = datetime.now(tz=timezone.utc)
    db.commit()

    assert crud.resume_all_paused_jobs(db, min_heartbeat_age_seconds=120) == 0
    assert crud.get_job(db, job.id).status == "paused"


# ── crash recovery ────────────────────────────────────────────────────────────

def test_requeue_interrupted_jobs_requeues_stale_heartbeat(db):
    job = crud.create_job(db, job_type="batch", label="Batch", params={"limit": 10})
    claimed_id = _claim(db)
    assert claimed_id == job.id

    job.last_heartbeat_at = datetime.now(tz=timezone.utc) - timedelta(seconds=600)
    db.commit()

    assert crud.requeue_interrupted_jobs(db) == 1

    refreshed = crud.get_job(db, job.id)
    assert refreshed.status == "queued"
    assert refreshed.error is None
    assert refreshed.started_at is None
    assert refreshed.restart_count == 1


def test_requeue_interrupted_jobs_leaves_live_jobs_alone(db):
    job = crud.create_job(db, job_type="batch", label="Batch", params={})
    _claim(db)
    job.last_heartbeat_at = datetime.now(tz=timezone.utc)
    db.commit()

    assert crud.requeue_interrupted_jobs(db) == 0
    assert crud.get_job(db, job.id).status == "running"


def test_requeue_interrupted_jobs_sets_bulk_resume_flag(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={"cantons": ["ZH"]})
    _claim(db)
    job.last_heartbeat_at = None
    db.commit()

    assert crud.requeue_interrupted_jobs(db) == 1

    refreshed = crud.get_job(db, job.id)
    assert json.loads(refreshed.params_json or "{}").get("resume") is True


def test_requeue_interrupted_jobs_kills_runaway_restarts(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={})
    _claim(db)
    job.restart_count = crud.MAX_RESTART_COUNT
    job.last_heartbeat_at = None
    db.commit()

    crud.requeue_interrupted_jobs(db)

    refreshed = crud.get_job(db, job.id)
    assert refreshed.status == "failed"
    assert "Max retries exceeded" in (refreshed.error or "")


def test_requeue_interrupted_jobs_honours_pending_cancel(db):
    job = crud.create_job(db, job_type="bulk", label="Bulk", params={})
    _claim(db)
    crud.mark_cancel_requested(db, job)
    job.last_heartbeat_at = None
    db.commit()

    crud.requeue_interrupted_jobs(db)
    assert crud.get_job(db, job.id).status == "cancelled"


def test_mark_failed_truncates_oversized_message(db):
    job = crud.create_job(db, job_type="shab_backfill", label="SHAB", params={})
    _claim(db)

    long_message = "InternalError: " + ("x" * 2000)
    crud.mark_failed(db, job, message=long_message, error="traceback")

    refreshed = crud.get_job(db, job.id)
    assert refreshed.status == "failed"
    assert refreshed.message is not None
    assert len(refreshed.message) <= 512
    assert refreshed.message.endswith("...")


# ── Zombie queued jobs (queued + cancel_requested) ────────────────────────────
#
# Regression suite for the preempt hot-loop of 2026-08-01. Job #12744
# (web_crawl_playwright) sat `queued` with `cancel_requested = true`:
# claim_next_job refused it, nothing else moved it out of `queued`, and
# has_queued_ml_job counted it as work worth yielding to — so #12746
# (web_extract) preempted, requeued, was re-claimed and preempted again,
# several times a second, indefinitely. Queue *ordering* could not fix it:
# no position makes an unclaimable job claimable.

def test_has_queued_ml_job_ignores_cancel_requested_jobs(db):
    """A crawler must never yield to work that can't take the slot."""
    from app.crud.crawler import has_queued_ml_job

    job = crud.create_job(db, job_type="reclassify_noga", label="ML", params={})
    assert has_queued_ml_job(db, {"reclassify_noga"}) is True

    crud.mark_cancel_requested(db, job)
    assert has_queued_ml_job(db, {"reclassify_noga"}) is False


def test_resume_paused_job_honours_a_pending_cancel(db):
    """Requeueing a cancel-requested job is what mints the zombie."""
    job = crud.create_job(db, job_type="web_crawl_playwright", label="PW", params={})
    crud.mark_cancel_requested(db, job)
    crud.mark_paused(db, job, message="Preempted", reason="preempt")

    crud.resume_paused_job(db, job, bump_queued_at=True)

    refreshed = crud.get_job(db, job.id)
    assert refreshed.status == "cancelled"
    assert refreshed.completed_at is not None
    assert _claim(db) is None


def test_cancel_zombie_queued_jobs_clears_existing_rows(db):
    """Self-heals queues that already contain a zombie (prod had one)."""
    zombie = crud.create_job(db, job_type="web_crawl_playwright", label="PW", params={})
    crud.mark_cancel_requested(db, zombie)
    healthy = crud.create_job(db, job_type="reclassify_noga", label="ML", params={})

    assert crud.cancel_zombie_queued_jobs(db) == 1

    assert crud.get_job(db, zombie.id).status == "cancelled"
    assert crud.get_job(db, healthy.id).status == "queued"
    # The queue is usable again.
    assert _claim(db) == healthy.id


def test_cancel_zombie_queued_jobs_is_a_noop_on_a_clean_queue(db):
    crud.create_job(db, job_type="reclassify_noga", label="ML", params={})
    assert crud.cancel_zombie_queued_jobs(db) == 0
