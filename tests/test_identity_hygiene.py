"""Identity-evidence hygiene and the remediation paths built on top of it.

Covers four changes that all serve one goal — stop throwing away a company's
correct URL on weak evidence:

  * UID evidence is asymmetric (prove from anywhere, disprove only from a page
    we trust, and never from a listing page);
  * a UID mismatch no longer blacklists a candidate whose domain IS the company;
  * exhausted companies can be re-opened once the extractor that failed them is
    fixed;
  * "block from selection" and "harvest profile data" are separate decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.crud import crawler as crawler_crud
from app.models.company import Company
from app.models.company_crawl_state import CompanyCrawlState
from app.models.company_url_candidate import CompanyUrlCandidate
from app.models.company_web_page import CompanyWebPage
from app.services.enrichment.crawler_extract import (
    _uid_may_disprove,
    domain_matches_company_name,
    resolve_company_extract,
)

# A real, checksum-valid UID pair (the extractor rejects invalid ones).
OURS = "CHE-130.637.800"
THEIRS = "CHE-182.187.419"


# ── UID evidence asymmetry ────────────────────────────────────────────────────

def test_target_uid_counts_from_any_page():
    """Positive evidence needs no page-type gate: an exact match to the Zefix UID
    cannot be a false positive, and many sites print it in a site-wide footer."""
    assert _uid_may_disprove({"services": [THEIRS]}, THEIRS) is False
    # ...but the *match* path does not go through _uid_may_disprove at all:
    page = f"<html><body><p>{OURS}</p></body></html>".encode()
    data = resolve_company_extract(
        [("services", page)],
        company_name="Muster AG", zefix_uid=OURS,
        site_url="https://muster.ch", page_types=["services"],
    )
    assert data["uid_matches_zefix"] is True


def test_foreign_uid_on_a_content_page_does_not_disprove():
    """A foreign UID on an arbitrary page is weak grounds for MISMATCH — and
    MISMATCH permanently rejects the candidate. Leave it unproven instead."""
    page = f"<html><body><p>{THEIRS}</p></body></html>".encode()
    data = resolve_company_extract(
        [("services", page)],
        company_name="Muster AG", zefix_uid=OURS,
        site_url="https://muster.ch", page_types=["services"],
    )
    assert data["uid_matches_zefix"] is None, "content-page UID must not contradict"


def test_foreign_uid_on_impressum_still_disproves():
    """No regression: the impressum is the legally mandated operator statement."""
    page = f"<html><body><p>{THEIRS}</p></body></html>".encode()
    data = resolve_company_extract(
        [("impressum", page)],
        company_name="Muster AG", zefix_uid=OURS,
        site_url="https://muster.ch", page_types=["impressum"],
    )
    assert data["uid_matches_zefix"] is False


def test_listing_page_uids_never_disprove():
    """A page carrying several UIDs is a directory listing, not a company page.

    Catches aggregator profiles and SHAB-notice PDFs without having to classify
    the whole SITE first — which only happens later, at extract time, after the
    crawl has already been paid for.
    """
    many = {"impressum": ["CHE-130.637.800", "CHE-182.187.419", "CHE-116.281.710"]}
    assert _uid_may_disprove(many, "CHE-182.187.419") is False
    # Two is still a plausible parent/subsidiary impressum, so it may disprove.
    two = {"impressum": ["CHE-130.637.800", "CHE-182.187.419"]}
    assert _uid_may_disprove(two, "CHE-182.187.419") is True


# ── Domain-match guard on rejection ───────────────────────────────────────────

def test_domain_match_requires_every_distinctive_token():
    """Strict on purpose: this guard must NOT rescue the directory/PDF cohort,
    which is ~91% of mismatches."""
    assert domain_matches_company_name("https://www.taxware.ch/de", "TaxWare AG") is True
    assert domain_matches_company_name("https://elektro-hunziker.ch", "Elektro Hunziker AG") is True
    # A SHAB-notice PDF shares no name token with the company it wrongly matched.
    assert domain_matches_company_name(
        "https://www.sshv.ch/x/SHAB_Meldungen.pdf", "Muster Treuhand AG"
    ) is False
    assert domain_matches_company_name("https://eur-lex.europa.eu/x", "TaxWare AG") is False


def test_generic_only_names_can_never_match_a_domain():
    """"Swiss Solutions" is every second Swiss company; matching on it would
    rescue arbitrary wrong sites."""
    assert domain_matches_company_name(
        "https://www.swiss-solutions.ch", "Swiss Solutions AG"
    ) is False


# ── Reopen remediation ────────────────────────────────────────────────────────

def _company(db, cid: int) -> Company:
    c = Company(id=cid, name=f"Firma {cid}", uid=f"CHE-{cid:09d}")
    db.add(c)
    db.flush()
    return c


def _cand(db, cid: int, url: str, status: str = "pending") -> CompanyUrlCandidate:
    c = CompanyUrlCandidate(company_id=cid, url=url, status=status, score=10)
    db.add(c)
    db.flush()
    return c


def _state(db, cid: int, cand_id: int | None, phase: str, status: str = "crawled"):
    st = CompanyCrawlState(
        company_id=cid, selected_url_id=cand_id,
        crawl_status=status, tier="http", crawl_phase=phase,
    )
    db.add(st)
    db.flush()
    return st


def test_reopen_requires_an_actually_untried_candidate(db):
    """Idempotent by construction: 'untried' means no company_web_pages row
    references the candidate, so a genuinely exhausted company stays retired."""
    # Company 1 — retired, but candidate B was never crawled.
    _company(db, 1)
    a = _cand(db, 1, "https://a.ch")
    b = _cand(db, 1, "https://b.ch")
    _state(db, 1, a.id, phase="done")
    db.add(CompanyWebPage(
        company_id=1, url_candidate_id=a.id, page_type="homepage",
        url=a.url, crawled=True, crawled_at=datetime.now(timezone.utc),
    ))

    # Company 2 — retired and every candidate really was tried.
    _company(db, 2)
    c = _cand(db, 2, "https://c.ch")
    _state(db, 2, c.id, phase="done")
    db.add(CompanyWebPage(
        company_id=2, url_candidate_id=c.id, page_type="homepage",
        url=c.url, crawled=True, crawled_at=datetime.now(timezone.utc),
    ))
    db.commit()

    assert crawler_crud.reopen_exhausted_identity(db, batch_size=100) == 1
    db.commit()

    reopened = db.get(CompanyCrawlState, 1)
    assert reopened.crawl_phase == "identity"
    assert reopened.crawl_status == "pending"
    assert reopened.selected_url_id == b.id, "must point at the untried candidate"

    assert db.get(CompanyCrawlState, 2).crawl_phase == "done", "genuinely exhausted"


def test_reopen_is_idempotent(db):
    """Runs in batches until it returns 0 — a second pass must find nothing."""
    _company(db, 3)
    a = _cand(db, 3, "https://a.ch")
    _cand(db, 3, "https://b.ch")
    _state(db, 3, a.id, phase="done")
    db.add(CompanyWebPage(
        company_id=3, url_candidate_id=a.id, page_type="homepage",
        url=a.url, crawled=True, crawled_at=datetime.now(timezone.utc),
    ))
    db.commit()

    assert crawler_crud.reopen_exhausted_identity(db, batch_size=100) == 1
    db.commit()
    assert crawler_crud.reopen_exhausted_identity(db, batch_size=100) == 0


def test_reopen_ignores_rejected_candidates(db):
    """A candidate rejected as a directory/PDF must not be re-selected."""
    _company(db, 4)
    a = _cand(db, 4, "https://a.ch")
    _cand(db, 4, "https://notice.pdf", status="rejected")
    _state(db, 4, a.id, phase="done")
    db.add(CompanyWebPage(
        company_id=4, url_candidate_id=a.id, page_type="homepage",
        url=a.url, crawled=True, crawled_at=datetime.now(timezone.utc),
    ))
    db.commit()

    assert crawler_crud.reopen_exhausted_identity(db, batch_size=100) == 0
    assert db.get(CompanyCrawlState, 4).crawl_phase == "done"


# ── Extraction sharding ───────────────────────────────────────────────────────

def test_extraction_shards_are_disjoint_and_complete(db):
    """Sharding replaces locking: FOR UPDATE is illegal with DISTINCT, and locks
    would be held across the batch's S3 downloads. Disjointness is what makes
    concurrent workers safe."""
    now = datetime.now(timezone.utc)
    for cid in range(1, 21):
        _company(db, cid)
        db.add(CompanyWebPage(
            company_id=cid, url_candidate_id=None, page_type="homepage",
            url=f"https://c{cid}.ch", crawled=True, crawled_at=now,
            needs_extraction=True, s3_key_html=f"crawl/{cid}/homepage.html",
        ))
    db.commit()

    shards = [
        set(crawler_crud.claim_companies_for_extraction(
            db, batch_size=100, shard=i, shard_count=4))
        for i in range(4)
    ]
    union: set[int] = set()
    for i, sh in enumerate(shards):
        for other in shards[i + 1:]:
            assert not (sh & other), "shards overlap — two workers would collide"
        union |= sh
    assert union == set(range(1, 21)), "sharding lost companies"


def test_single_shard_is_the_unsharded_default(db):
    """shard_count=1 must reproduce the original behaviour for every caller."""
    now = datetime.now(timezone.utc)
    for cid in range(1, 6):
        _company(db, cid)
        db.add(CompanyWebPage(
            company_id=cid, url_candidate_id=None, page_type="homepage",
            url=f"https://c{cid}.ch", crawled=True, crawled_at=now,
            needs_extraction=True, s3_key_html=f"crawl/{cid}/homepage.html",
        ))
    db.commit()

    assert crawler_crud.claim_companies_for_extraction(db, batch_size=100) == [1, 2, 3, 4, 5]


# ── Directory domains: block vs harvest are independent ───────────────────────

def test_block_and_harvest_are_separate_decisions(db):
    """`status='approved'` used to mean BOTH "never a company website" and
    "harvest profile data from it" — so kompass.ch, which is worth blocking and
    not worth crawling, could not be expressed."""
    from app.crud import directory_crawl_domain as dcd
    from app.models.directory_crawl_domain import DirectoryCrawlDomain

    db.add(DirectoryCrawlDomain(value="local.ch", status="approved", harvest=True))
    db.add(DirectoryCrawlDomain(value="kompass.ch", status="approved", harvest=False))
    db.add(DirectoryCrawlDomain(value="unreviewed.ch", status="pending_review", harvest=True))
    db.commit()

    blocked = dcd.get_approved_directory_crawl_domains(db)
    harvestable = dcd.get_harvestable_directory_domains(db)

    assert blocked == {"local.ch", "kompass.ch"}, "both are approved => both blocked"
    assert harvestable == {"local.ch"}, "kompass.ch must never be crawled"
    assert "unreviewed.ch" not in blocked, "pending_review is an unreviewed guess"
    assert "unreviewed.ch" not in harvestable


def test_hardcoded_harvest_floor_matches_intent():
    """The two lists disagreed: kompass.ch sat in the harvest floor while being
    a domain we never want to fetch, and moneyland.ch was in neither list — so
    it was selectable as a company's own website."""
    from app.services.jobs.job_handlers.web_crawl import DIRECTORY_CRAWL_DOMAINS
    from app.services.scoring.scoring import CRAWL_BLOCKED_DOMAINS

    for never in ("kompass.ch", "kompass.com", "moneyland.ch", "business-monitor.ch"):
        assert never in CRAWL_BLOCKED_DOMAINS, f"{never} must not be selectable"
        assert never not in DIRECTORY_CRAWL_DOMAINS, f"{never} must not be harvested"

    for valuable in ("local.ch", "treuhandvergleich.ch"):
        assert valuable in CRAWL_BLOCKED_DOMAINS
        assert valuable in DIRECTORY_CRAWL_DOMAINS


def test_purge_rejects_non_page_candidates(db):
    """PDFs and assets were stored as candidate "websites" and crawled as
    homepages; crawling a SHAB-notice PDF harvests other companies' UIDs."""
    _company(db, 50)
    _cand(db, 50, "https://x.ch/")
    _cand(db, 50, "https://x.ch/SHAB_Meldungen.pdf")
    _cand(db, 50, "https://x.ch/logo.svg")
    db.commit()

    n, last = crawler_crud.purge_non_page_candidates(db, batch_size=100)
    assert n == 2 and last is not None
    db.commit()

    alive = {
        c.url for c in db.query(CompanyUrlCandidate)
        .filter(CompanyUrlCandidate.company_id == 50,
                CompanyUrlCandidate.status != "rejected").all()
    }
    assert alive == {"https://x.ch/"}
    # Idempotent — a second full pass finds nothing left to reject.
    n2, _ = crawler_crud.purge_non_page_candidates(db, batch_size=100)
    assert n2 == 0


# ── Extraction concurrency safety ─────────────────────────────────────────────

def test_web_extract_dedup_is_per_shard():
    """Two workers on the SAME shard must never coexist; different shards may.

    web_extract parallelises by `company_id % shard_count`, which only separates
    workers holding *different* shard indices. Marking the type NO_DEDUP instead
    let every auto-enqueue (params={} => shard 0 of 1) spawn another worker
    claiming identical rows; the loser of each race saw mark_pages_extracted
    already done and logged a spurious "empty" — 14,277 of 15,000 on 2026-08-20.
    """
    from app.services.jobs.job_worker import _compute_dedup_key

    a = _compute_dedup_key("web_extract", None, {})
    b = _compute_dedup_key("web_extract", None, {"shard": 0, "shard_count": 1})
    assert a == b, "the auto-enqueue default must collide with itself"

    keys = {
        _compute_dedup_key("web_extract", None, {"shard": i, "shard_count": 4})
        for i in range(4)
    }
    assert len(keys) == 4, "distinct shards must be able to run concurrently"
    assert a not in keys, "an unsharded run must not block a sharded fan-out"


def test_web_extract_is_not_in_no_dedup():
    """Regression guard: NO_DEDUP would reinstate the identical-claim race."""
    import inspect
    from app.services.jobs import job_worker

    src = inspect.getsource(job_worker._compute_dedup_key)
    no_dedup = src.split("NO_DEDUP = {", 1)[1].split("}", 1)[0]
    assert "web_extract" not in no_dedup


# ── Extraction must see the whole page set ────────────────────────────────────

def test_extraction_sees_identity_pages_already_marked_extracted(db):
    """Phase B must not be able to erase the evidence phase A established.

    `needs_extraction` decides which COMPANIES are claimed; it must not decide
    which PAGES the extractor sees. When it did, a phase-B re-extract received
    only the new content pages — phase A's impressum/contact were already
    flagged extracted — so the UID that proved identity was excluded and
    upsert_web_extract overwrote a UID-verified extract with a name match.
    ENTRADIS GMBH (CHE-115.230.839): UID in plain HTML on a crawled /kontakt/
    page, scored 0.33 `deterministic+name_match`.
    """
    now = datetime.now(timezone.utc)
    _company(db, 90)
    cand = _cand(db, 90, "https://entradis.gmbh/")

    # Phase A: contact page, already extracted in an earlier run.
    db.add(CompanyWebPage(
        company_id=90, url_candidate_id=cand.id, page_type="contact",
        url="https://entradis.gmbh/kontakt/", crawled=True, crawled_at=now,
        needs_extraction=False, s3_key_html="crawl/90/contact.html",
    ))
    # Phase B: fresh content page, awaiting extraction.
    db.add(CompanyWebPage(
        company_id=90, url_candidate_id=cand.id, page_type="other",
        url="https://entradis.gmbh/2025/09", crawled=True, crawled_at=now,
        needs_extraction=True, s3_key_html="crawl/90/other.html",
    ))
    # Inventory-only row: discovered, never fetched, no HTML to extract.
    db.add(CompanyWebPage(
        company_id=90, url_candidate_id=None, page_type="news",
        url="https://entradis.gmbh/news", crawled=False, crawled_at=now,
        needs_extraction=False, s3_key_html=None,
    ))
    db.commit()

    types = {p.page_type for p in crawler_crud.get_extractable_pages(db, 90)}
    assert "contact" in types, "identity page was excluded from the re-extract"
    assert "other" in types
    assert "news" not in types, "inventory rows have no HTML to extract"


# ── Domain-frequency scan must not be a single unbounded query ───────────────

def test_domain_frequency_scan_is_keyset_paged(db):
    """discover_directory_domains hit the prod statement timeout (30s) on a
    single GROUP BY over a per-row regex with no supporting index — the table
    has multi-million rows. Now walked in id-ordered chunks with the hostname
    extracted and counted in Python, so no individual statement can time out
    regardless of table size. batch_size=2 forces >1 chunk here to prove the
    boundary between pages doesn't drop or double-count a company.
    """
    _company(db, 60)
    _company(db, 61)
    _company(db, 62)
    _cand(db, 60, "https://WWW.Directory.ch/profile/60")
    _cand(db, 61, "http://directory.ch/profile/61")
    _cand(db, 62, "https://directory.ch:8443/profile/62")
    _cand(db, 60, "https://onlyone.ch/")
    db.commit()

    result = crawler_crud.get_high_frequency_candidate_domains(
        db, min_companies=2, limit=10, batch_size=2,
    )
    assert result == [{"domain": "directory.ch", "company_count": 3}]

    # Below threshold => filtered out, not just low-ranked.
    none_ = crawler_crud.get_high_frequency_candidate_domains(
        db, min_companies=4, limit=10, batch_size=2,
    )
    assert none_ == []


def test_bare_hostname_matches_prior_sql_semantics():
    """Pins the Python extraction against the exact regex it replaced:
    strip scheme, take the first path segment, drop a trailing port, drop a
    leading 'www.' — case-insensitive throughout."""
    assert crawler_crud._bare_hostname("HTTPS://WWW.Example.CH/a/b") == "example.ch"
    assert crawler_crud._bare_hostname("http://example.ch:8080/x") == "example.ch"
    assert crawler_crud._bare_hostname("example.ch") == "example.ch"
