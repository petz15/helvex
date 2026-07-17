"""Tests for prorated + idempotent job credit refunds (spend-side abuse guard)."""

import json

from app.models.job_run import JobRun
from app.models.organization import Organization
from app.services.jobs.job_worker import _refund_job_credits_if_needed


def _seed(db, *, org_id, job_id, cost, done, total, balance=0):
    org = Organization(id=org_id, name=f"o{org_id}", slug=f"o{org_id}", tier="free", credits_balance=balance)
    db.add(org)
    db.flush()
    job = JobRun(
        id=job_id,
        job_type="claude_classify",
        label="test job",
        status="cancelled",
        org_id=org_id,
        stats_json=json.dumps({"_credit_deduction": {"action": "immediate_llm", "count": cost, "cost": cost}}),
        progress_done=done,
        progress_total=total,
    )
    db.add(job)
    db.commit()
    return org, job


def test_refund_full_when_not_started(db):
    org, job = _seed(db, org_id=1, job_id=1, cost=500, done=0, total=0)
    _refund_job_credits_if_needed(db, job=job, reason="cancelled_before_start")
    db.refresh(org)
    assert org.credits_balance == 500  # never ran → full refund


def test_refund_prorated_after_partial_work(db):
    # Cancelled at 450/500 → only the undone 50 units (10%) are refunded.
    org, job = _seed(db, org_id=2, job_id=2, cost=500, done=450, total=500)
    _refund_job_credits_if_needed(db, job=job, reason="cancelled")
    db.refresh(org)
    assert org.credits_balance == 50  # 500 * (500-450)/500


def test_refund_zero_when_fully_consumed(db):
    org, job = _seed(db, org_id=3, job_id=3, cost=500, done=500, total=500)
    _refund_job_credits_if_needed(db, job=job, reason="cancelled")
    db.refresh(org)
    assert org.credits_balance == 0  # all work done → no refund


def test_refund_idempotent(db):
    org, job = _seed(db, org_id=4, job_id=4, cost=500, done=0, total=0, balance=0)
    _refund_job_credits_if_needed(db, job=job, reason="cancelled")
    _refund_job_credits_if_needed(db, job=job, reason="failed")  # second call must be a no-op
    db.refresh(org)
    assert org.credits_balance == 500  # refunded once, not twice
