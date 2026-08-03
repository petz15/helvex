"""Concurrent-crawl-job isolation.

`web_crawl_http` / `web_crawl_content` are in NO_DEDUP so several can run at once
across the crawler pods — that is the whole mechanism by which more than one pod
does crawl work. It only pays off if a job's recovery/cleanup paths touch its OWN
claimed rows and nobody else's. These tests pin that boundary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.crud import crawler as crawler_crud
from app.models.company_crawl_state import CompanyCrawlState
from app.models.company_url_candidate import CompanyUrlCandidate


def _state(db, company_id: int, *, status: str, phase: str = "identity", tier: str = "http"):
    cand = CompanyUrlCandidate(
        company_id=company_id, url=f"https://c{company_id}.ch", status="selected"
    )
    db.add(cand)
    db.flush()
    st = CompanyCrawlState(
        company_id=company_id, selected_url_id=cand.id,
        crawl_status=status, tier=tier, crawl_phase=phase,
    )
    db.add(st)
    db.flush()
    return st


# ── Precise release: only this job's rows ─────────────────────────────────────

def test_release_by_id_leaves_a_sibling_jobs_rows_alone(db):
    """The core multi-pod invariant.

    Job A holds 1 and 2; job B holds 3. Job A exiting must not hand back 3 —
    doing so lets a third worker claim a company job B is actively crawling.
    """
    for cid in (1, 2, 3):
        _state(db, cid, status="in_progress")

    released = crawler_crud.release_crawl_states_by_id(db, {1, 2})
    db.commit()

    assert released == 2
    assert db.get(CompanyCrawlState, 1).crawl_status == "pending"
    assert db.get(CompanyCrawlState, 2).crawl_status == "pending"
    assert db.get(CompanyCrawlState, 3).crawl_status == "in_progress"


def test_release_by_id_does_not_resurrect_finished_rows(db):
    """Rows the last batch already completed keep their terminal status — the
    job's in-flight set can still name them if the commit landed first."""
    _state(db, 10, status="crawled")
    _state(db, 11, status="bot_blocked")
    _state(db, 12, status="in_progress")

    released = crawler_crud.release_crawl_states_by_id(db, {10, 11, 12})
    db.commit()

    assert released == 1
    assert db.get(CompanyCrawlState, 10).crawl_status == "crawled"
    assert db.get(CompanyCrawlState, 11).crawl_status == "bot_blocked"
    assert db.get(CompanyCrawlState, 12).crawl_status == "pending"


def test_release_by_id_is_a_noop_for_an_empty_set(db):
    _state(db, 20, status="in_progress")
    assert crawler_crud.release_crawl_states_by_id(db, set()) == 0
    assert db.get(CompanyCrawlState, 20).crawl_status == "in_progress"


# ── Staleness-gated sweep: only genuinely orphaned rows ───────────────────────

def test_startup_sweep_spares_a_freshly_claimed_row(db):
    """A sibling job's just-claimed batch must survive another job starting up.

    Without the staleness gate this sweep resets every in_progress row of the
    tier+phase, so two pods end up crawling the same companies.
    """
    _state(db, 30, status="in_progress")
    db.commit()

    released = crawler_crud.release_in_progress_states(
        db, tier="http", phase="identity", stale_after_seconds=900,
    )
    db.commit()

    assert released == 0
    assert db.get(CompanyCrawlState, 30).crawl_status == "in_progress"


def test_startup_sweep_reclaims_a_row_orphaned_by_a_dead_pod(db):
    _state(db, 31, status="in_progress")
    db.commit()
    # Backdate the claim clock past the threshold.
    db.query(CompanyCrawlState).filter(CompanyCrawlState.company_id == 31).update(
        {"updated_at": datetime.now(timezone.utc) - timedelta(hours=2)},
        synchronize_session=False,
    )
    db.commit()

    released = crawler_crud.release_in_progress_states(
        db, tier="http", phase="identity", stale_after_seconds=900,
    )
    db.commit()

    assert released == 1
    assert db.get(CompanyCrawlState, 31).crawl_status == "pending"


def test_startup_sweep_stays_within_its_phase(db):
    """Identity and content jobs share tier='http'. An unscoped sweep from one
    resets the other's in-flight batch."""
    _state(db, 40, status="in_progress", phase="identity")
    _state(db, 41, status="in_progress", phase="content")
    db.commit()
    db.query(CompanyCrawlState).update(
        {"updated_at": datetime.now(timezone.utc) - timedelta(hours=2)},
        synchronize_session=False,
    )
    db.commit()

    released = crawler_crud.release_in_progress_states(
        db, tier="http", phase="identity", stale_after_seconds=900,
    )
    db.commit()

    assert released == 1
    assert db.get(CompanyCrawlState, 40).crawl_status == "pending"
    assert db.get(CompanyCrawlState, 41).crawl_status == "in_progress"


# ── Enqueue fan-out sizing ────────────────────────────────────────────────────

def test_crawler_http_slots_defaults_to_one_when_unset(monkeypatch):
    """Local dev / single pod must behave exactly as before: one job per trigger."""
    import app.services.jobs.job_worker as jw

    monkeypatch.setattr(jw, "_CRAWLER_HTTP_SLOTS", 0)
    assert jw.crawler_http_slots() == 1


def test_crawler_http_slots_reports_the_fleet_size(monkeypatch):
    import app.services.jobs.job_worker as jw

    monkeypatch.setattr(jw, "_CRAWLER_HTTP_SLOTS", 4)
    assert jw.crawler_http_slots() == 4


def test_crawler_http_slots_is_capped(monkeypatch):
    """Each instance costs memory on a crawler pod — a bad env value must not
    enqueue hundreds of concurrent crawls."""
    import app.services.jobs.job_worker as jw

    monkeypatch.setattr(jw, "_CRAWLER_HTTP_SLOTS", 9999)
    assert jw.crawler_http_slots() == jw.MAX_CRAWL_INSTANCES
