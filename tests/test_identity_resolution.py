"""Identity phase: batched fallback chain and terminal "no match" reporting.

Covers the two things that decide how fast — and how legibly — a company's real
website gets established:
  * the fallback to the next URL candidate is a re-queue of the company's own
    crawl state, not a per-company job; and
  * every way the identity phase can conclude produces a website_status a user
    can read, instead of a NULL that looks identical to "not started".
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.crud import crawler as crawler_crud
from app.models.company_crawl_state import CompanyCrawlState
from app.models.company_url_candidate import CompanyUrlCandidate
from app.models.company_web_page import CompanyWebPage
from app.services.enrichment import website_status as ws


def _candidate(db, company_id: int, url: str, status: str = "pending") -> CompanyUrlCandidate:
    c = CompanyUrlCandidate(company_id=company_id, url=url, status=status)
    db.add(c)
    db.flush()
    return c


def _state(db, company_id: int, cand_id: int | None, **kw) -> CompanyCrawlState:
    st = CompanyCrawlState(
        company_id=company_id,
        selected_url_id=cand_id,
        crawl_status=kw.get("crawl_status", "crawled"),
        tier=kw.get("tier", "http"),
        crawl_phase=kw.get("crawl_phase", "identity"),
    )
    db.add(st)
    db.flush()
    return st


# ── Batched fallback ──────────────────────────────────────────────────────────

def test_retarget_points_the_state_at_the_next_candidate_and_requeues(db):
    """The fallback must be a re-queue the BATCH crawler can claim.

    It used to enqueue a web_crawl_single job per company, so every retry took a
    whole worker slot for one company — and the right site being candidate #2 or
    #3 is the common case, not the exception.
    """
    first = _candidate(db, 1, "https://wrong.ch")
    second = _candidate(db, 1, "https://right.ch")
    _state(db, 1, first.id, crawl_status="crawled")

    assert crawler_crud.retarget_crawl_state_to_candidate(db, 1, second.id) is True
    db.commit()

    st = db.get(CompanyCrawlState, 1)
    assert st.selected_url_id == second.id
    assert st.crawl_status == "pending"   # claimable by the normal batch crawler
    assert st.tier == "http"
    assert st.next_crawl_at is None       # no backoff standing in the way
    assert st.consecutive_failures == 0


def test_retarget_refuses_to_drag_a_company_back_out_of_phase_b(db):
    """Only identity-phase rows may be retargeted — a confirmed company that has
    moved to content must never be pulled back into identity resolution."""
    cand = _candidate(db, 2, "https://x.ch")
    other = _candidate(db, 2, "https://y.ch")
    _state(db, 2, cand.id, crawl_phase="content", crawl_status="pending")

    assert crawler_crud.retarget_crawl_state_to_candidate(db, 2, other.id) is False
    db.commit()
    assert db.get(CompanyCrawlState, 2).selected_url_id == cand.id


def test_page_deletion_is_scoped_to_one_candidate(db):
    """Wiping every page on re-crawl would break the fallback chain: the
    'already attempted' test keys on a candidate having pages, so the chain
    would re-pick the same candidate forever."""
    a = _candidate(db, 3, "https://a.ch")
    b = _candidate(db, 3, "https://b.ch")
    now = datetime.now(timezone.utc)
    for cand in (a, b):
        db.add(CompanyWebPage(
            company_id=3, url_candidate_id=cand.id, page_type="homepage",
            url=cand.url, crawled=True, crawled_at=now,
        ))
    db.flush()

    removed = crawler_crud.delete_web_pages_for_candidate(db, 3, b.id)
    db.commit()

    assert removed == 1
    remaining = db.query(CompanyWebPage).filter_by(company_id=3).all()
    assert [p.url_candidate_id for p in remaining] == [a.id]


# ── Terminal identity outcomes ────────────────────────────────────────────────

def test_no_candidates_is_reported_not_left_blank(db):
    """A company search turned up nothing crawlable for. That is an answer."""
    _state(db, 10, None, crawl_status="no_website")
    db.commit()

    assert crawler_crud.get_identity_outcome(db, 10) == "no_candidates"
    verdict = ws.compute_verdict(db, 10, ws.load_thresholds(db))
    assert verdict.status == ws.NONE
    assert verdict.website_url is None


def test_exhausted_candidates_is_reported(db):
    """Every candidate was tried and none was them."""
    cand = _candidate(db, 11, "https://x.ch")
    _state(db, 11, cand.id, crawl_phase="done", crawl_status="crawled")
    db.commit()

    assert crawler_crud.get_identity_outcome(db, 11) == "exhausted"
    assert ws.compute_verdict(db, 11, ws.load_thresholds(db)).status == ws.NONE


def test_bot_blocked_is_unreachable_not_no_website(db):
    """Being blocked disproves nothing — reporting it as 'no website' would be
    a false negative, and would stop anyone retrying it."""
    cand = _candidate(db, 12, "https://x.ch")
    _state(db, 12, cand.id, crawl_status="bot_blocked")
    db.commit()

    assert crawler_crud.get_identity_outcome(db, 12) == "unreachable"
    verdict = ws.compute_verdict(db, 12, ws.load_thresholds(db))
    assert verdict.status == ws.UNREACHABLE
    # No score is asserted either way: we genuinely do not know.
    assert verdict.web_score is None


def test_in_flight_company_stays_unknown(db):
    """Still pending — must remain NULL so it is not mistaken for a finding."""
    cand = _candidate(db, 13, "https://x.ch")
    _state(db, 13, cand.id, crawl_status="pending")
    db.commit()

    assert crawler_crud.get_identity_outcome(db, 13) is None
    assert ws.compute_verdict(db, 13, ws.load_thresholds(db)).status is None


def test_company_with_no_crawl_state_at_all_stays_unknown(db):
    """Never entered the pipeline — not a negative finding."""
    assert crawler_crud.get_identity_outcome(db, 999) is None
    assert ws.compute_verdict(db, 999, ws.load_thresholds(db)).status is None


# ── Terminal status actually reaches the companies table ──────────────────────

def _company(db, cid: int):
    from app.models.company import Company
    c = Company(id=cid, name=f"Firma {cid}", uid=f"CHE-{cid:09d}")
    db.add(c)
    db.flush()
    return c


def test_sync_writes_none_for_a_company_with_no_candidates(db):
    """The path that has no other writer.

    web_extract only claims companies that HAVE pages, so a 'no_website' company
    is never processed by it — without this sync its verdict stays NULL forever
    (or until someone runs recompute_website_status by hand).
    """
    from app.models.company import Company

    _company(db, 30)
    _state(db, 30, None, crawl_status="no_website")
    db.commit()

    assert crawler_crud.sync_terminal_website_status(db, [30]) == 1
    db.commit()
    assert db.get(Company, 30).website_status == "none"


def test_sync_writes_unreachable_for_a_blocked_company(db):
    from app.models.company import Company

    _company(db, 31)
    cand = _candidate(db, 31, "https://x.ch")
    _state(db, 31, cand.id, crawl_status="bot_blocked")
    db.commit()

    assert crawler_crud.sync_terminal_website_status(db, [31]) == 1
    db.commit()
    assert db.get(Company, 31).website_status == "unreachable"


def test_sync_leaves_in_flight_companies_alone(db):
    from app.models.company import Company

    _company(db, 32)
    cand = _candidate(db, 32, "https://x.ch")
    _state(db, 32, cand.id, crawl_status="pending")
    db.commit()

    assert crawler_crud.sync_terminal_website_status(db, [32]) == 0
    db.commit()
    assert db.get(Company, 32).website_status is None


def test_sync_is_idempotent(db):
    """Runs after every crawl batch — a second call must be a no-op, not churn."""
    _company(db, 33)
    _state(db, 33, None, crawl_status="no_website")
    db.commit()

    assert crawler_crud.sync_terminal_website_status(db, [33]) == 1
    db.commit()
    assert crawler_crud.sync_terminal_website_status(db, [33]) == 0


def test_sync_never_overwrites_a_real_crawl_verdict(db):
    """A company WITH an extract is owned by compute_verdict — this must not
    stomp a 'verified' result just because its crawl row reached phase done."""
    from app.models.company import Company
    from app.models.company_web_extract import CompanyWebExtract

    _company(db, 34)
    cand = _candidate(db, 34, "https://x.ch")
    _state(db, 34, cand.id, crawl_phase="done", crawl_status="crawled")
    db.add(CompanyWebExtract(
        company_id=34, url_candidate_id=cand.id, confidence=0.9,
        extracted_at=datetime.now(timezone.utc),
    ))
    db.query(Company).filter(Company.id == 34).update({"website_status": "verified"})
    db.commit()

    assert crawler_crud.sync_terminal_website_status(db, [34]) == 0
    db.commit()
    assert db.get(Company, 34).website_status == "verified"


# ── External tier escalation ──────────────────────────────────────────────────

def test_escalate_to_external_requeues_on_the_paid_tier(db):
    cand = _candidate(db, 20, "https://hard.ch")
    st = _state(db, 20, cand.id, crawl_status="bot_blocked", tier="playwright")

    crawler_crud.escalate_to_external(db, st)
    db.commit()

    row = db.get(CompanyCrawlState, 20)
    assert row.tier == "external"
    assert row.crawl_status == "pending"
    assert crawler_crud.count_pending_external(db) == 1


def test_external_errors_never_carry_the_api_key(db):
    """These strings are persisted to crawl_error_detail and shown in the admin
    failures table — the key is a QUERY PARAM, so provider errors can echo it."""
    from app.clients.scrapingdog_scrape_client import _scrub

    key = "sk_live_supersecret123"
    leaked = f"HTTP 400 for https://api.scrapingdog.com/scrape?api_key={key}&url=x"
    scrubbed = _scrub(leaked, key)

    assert key not in scrubbed
    assert "***" in scrubbed
    # Non-matching text is left intact so the message stays diagnosable.
    assert _scrub("plain failure", key) == "plain failure"


def test_external_tier_is_off_without_an_api_key(db, monkeypatch):
    """No key configured ⇒ the ladder simply ends at Playwright; nothing is
    silently queued for a tier that cannot run."""
    from app.config import settings
    from app.services.enrichment import crawler_external as ext

    monkeypatch.setattr(settings, "scrapingdog_api_key", "")
    assert ext.is_external_tier_enabled(db) is False


# ── Extract memory bound ──────────────────────────────────────────────────────

def test_extract_page_order_puts_identity_pages_first():
    """The byte cap drops the tail, so the UID/address pages must lead.

    DB order is (crawled desc, priority, id); after a 60-page phase-B crawl that
    can put arbitrary content pages ahead of the impressum, which is exactly
    where resolve_company_extract reads the UID from.
    """
    from app.services.jobs.job_handlers.web_crawl import _order_pages_for_extract

    class _P:
        def __init__(self, t):
            self.page_type = t

    ordered = [
        p.page_type
        for p in _order_pages_for_extract(
            [_P("news"), _P("impressum"), _P("shop"), _P("homepage"), _P("contact")]
        )
    ]
    assert ordered[:3] == ["homepage", "impressum", "contact"]


def test_extract_blob_cap_is_far_above_the_identity_page_set():
    """The cap must never bite on a normal identity extract (~3 pages)."""
    from app.services.enrichment.crawler_common import MAX_PAGE_BYTES
    from app.services.jobs.job_handlers.web_crawl import _MAX_EXTRACT_BLOB_BYTES

    assert _MAX_EXTRACT_BLOB_BYTES >= 3 * MAX_PAGE_BYTES
    # ...but well under what an unbounded 60-page phase-B candidate would hold.
    assert _MAX_EXTRACT_BLOB_BYTES < 60 * MAX_PAGE_BYTES
