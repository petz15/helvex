"""Tests for prorated + idempotent job credit refunds (spend-side abuse guard)."""

import json

from app.models.job_run import JobRun
from app.models.organization import Organization
from app.services.jobs.job_worker import _refund_job_credits_if_needed


def _seed(db, *, org_id, job_id, cost, done, total, balance=0, prorate=True, action="immediate_llm"):
    org = Organization(id=org_id, name=f"o{org_id}", slug=f"o{org_id}", tier="free", credits_balance=balance)
    db.add(org)
    db.flush()
    job = JobRun(
        id=job_id,
        job_type="claude_classify",
        label="test job",
        status="cancelled",
        org_id=org_id,
        stats_json=json.dumps({"_credit_deduction": {"action": action, "count": cost, "cost": cost, "prorate": prorate}}),
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


def test_refund_full_when_not_prorated(db):
    # CSV export style (prorate=False): a job that failed at 450/500 still gets a
    # FULL refund because a failed/cancelled export delivers no file.
    org, job = _seed(db, org_id=9, job_id=9, cost=500, done=450, total=500,
                     prorate=False, action="bulk_export_basic")
    _refund_job_credits_if_needed(db, job=job, reason="failed")
    db.refresh(org)
    assert org.credits_balance == 500  # full, not prorated to 50


def test_csv_export_not_double_charged_at_enqueue(db):
    # csv_export is charged at the route (by tier cap); the enqueue path must not
    # charge it again (previous double-charge bug).
    from app.services.jobs.job_worker import _resolve_credit_action_and_count
    assert _resolve_credit_action_and_count(db, job_type="csv_export", params={"row_limit": 5000}) is None


def test_refund_idempotent(db):
    org, job = _seed(db, org_id=4, job_id=4, cost=500, done=0, total=0, balance=0)
    _refund_job_credits_if_needed(db, job=job, reason="cancelled")
    _refund_job_credits_if_needed(db, job=job, reason="failed")  # second call must be a no-op
    db.refresh(org)
    assert org.credits_balance == 500  # refunded once, not twice


# ---------------------------------------------------------------------------
# refund_action — inline (synchronous) action refund on failure
# ---------------------------------------------------------------------------

def test_refund_action_restores_deducted_credits(db):
    from app.services.billing.credits import check_and_deduct, refund_action
    org = Organization(id=10, name="o10", slug="o10", tier="simple", credits_balance=100)
    db.add(org); db.commit()

    assert check_and_deduct(db, 10, "web_search", 1, reference_id="ws-10") is True
    db.refresh(org)
    assert org.credits_balance == 80  # 100 - 20

    assert refund_action(db, 10, "web_search", 1, reference_id="ws-10") is True
    db.refresh(org)
    assert org.credits_balance == 100  # fully restored — failed search costs nothing net


def test_refund_action_is_idempotent(db):
    from app.services.billing.credits import refund_action
    org = Organization(id=11, name="o11", slug="o11", tier="simple", credits_balance=80)
    db.add(org); db.commit()

    assert refund_action(db, 11, "web_search", 1, reference_id="ws-11") is True
    assert refund_action(db, 11, "web_search", 1, reference_id="ws-11") is False  # no-op
    db.refresh(org)
    assert org.credits_balance == 100  # +20 exactly once


def test_refund_action_noop_for_unlimited_org(db):
    from app.services.billing.credits import refund_action
    org = Organization(id=12, name="o12", slug="o12", tier="free", credits_balance=0, credits_unlimited=True)
    db.add(org); db.commit()
    assert refund_action(db, 12, "web_search", 1, reference_id="ws-12") is False
    db.refresh(org)
    assert org.credits_balance == 0  # unlimited orgs were never charged


def test_refund_action_noop_for_free_entitlement(db):
    from app.services.billing.credits import refund_action
    # explorer+ flex_rescore is free → nothing was deducted → nothing to refund.
    org = Organization(id=13, name="o13", slug="o13", tier="explorer", credits_balance=50)
    db.add(org); db.commit()
    assert refund_action(db, 13, "flex_rescore", 5, reference_id="fr-13") is False
    db.refresh(org)
    assert org.credits_balance == 50
