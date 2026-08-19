"""Two-phase crawl: phase gating, frontier bounds, and page-preservation."""
from __future__ import annotations

import asyncio

import pytest

from app.crud import crawler as crawler_crud
from app.models.company_crawl_state import CompanyCrawlState
from app.models.company_url_candidate import CompanyUrlCandidate
from app.models.company_web_page import CompanyWebPage
from app.services.enrichment.crawler_common import (
    CONTENT_PAGE_TYPES,
    IDENTITY_PAGE_TYPES,
    classify_page_type,
    extract_internal_links,
    is_crawlable_page_url,
    parse_soup,
)


# ── Frontier filtering ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://x.ch/blog/page/2",       # pagination trap
    "https://x.ch/tag/consulting",    # tag archive
    "https://x.ch/?s=suche",          # search
    "https://x.ch/warenkorb",         # cart
    "https://x.ch/datei.pdf",         # binary
    "https://x.ch/logo.svg",          # asset
    "https://other.ch/ueber-uns",     # off-host
    "ftp://x.ch/a",                   # non-HTTP scheme
])
def test_frontier_rejects_traps_and_assets(url):
    assert is_crawlable_page_url(url, "x.ch") is False


@pytest.mark.parametrize("url", [
    "https://x.ch/ueber-uns",
    "https://x.ch/team/mitarbeiter",
    "https://x.ch/dienstleistungen/beratung",
])
def test_frontier_accepts_real_pages(url):
    assert is_crawlable_page_url(url, "x.ch") is True


def test_extract_internal_links_dedupes_and_strips_fragments():
    html = b"""
    <html><body>
      <a href="/ueber-uns">About</a>
      <a href="/ueber-uns#team">About again, fragment</a>
      <a href="https://other.ch/x">Off host</a>
      <a href="mailto:a@b.ch">Mail</a>
      <a href="/blog/page/3">Pagination</a>
      <a href="/kontakt">Contact</a>
    </body></html>
    """
    links = extract_internal_links(parse_soup(html), "https://x.ch/")
    assert links == ["https://x.ch/ueber-uns", "https://x.ch/kontakt"]


def test_identity_and_content_page_types_are_disjoint():
    assert not (IDENTITY_PAGE_TYPES & CONTENT_PAGE_TYPES)
    assert "impressum" in IDENTITY_PAGE_TYPES
    assert "team" in CONTENT_PAGE_TYPES


def test_classify_page_type_falls_back_to_other():
    assert classify_page_type("https://x.ch/team/") == "team"
    assert classify_page_type("https://x.ch/zufaellig-xyz") == "other"


# ── Phase transitions ─────────────────────────────────────────────────────────

def _make_state(db, company_id: int, phase: str = "identity", status: str = "crawled"):
    cand = CompanyUrlCandidate(company_id=company_id, url=f"https://c{company_id}.ch", status="selected")
    db.add(cand)
    db.flush()
    state = CompanyCrawlState(
        company_id=company_id, selected_url_id=cand.id,
        crawl_status=status, tier="http", crawl_phase=phase,
    )
    db.add(state)
    db.flush()
    return state, cand


def test_advance_to_content_phase_is_idempotent(db):
    _make_state(db, 1)

    assert crawler_crud.advance_to_content_phase(db, 1) is True
    state = db.get(CompanyCrawlState, 1)
    assert state.crawl_phase == "content"
    assert state.crawl_status == "pending"

    # A second call must not re-queue an already-advanced company: this is what
    # stops a re-extract from dragging a finished content crawl back to pending.
    assert crawler_crud.advance_to_content_phase(db, 1) is False
    assert db.get(CompanyCrawlState, 1).crawl_status == "pending"


def test_advance_does_not_touch_a_done_company(db):
    _make_state(db, 2, phase="done", status="crawled")
    assert crawler_crud.advance_to_content_phase(db, 2) is False
    assert db.get(CompanyCrawlState, 2).crawl_phase == "done"


def test_release_in_progress_is_scoped_to_phase(db):
    """An identity job's recovery sweep must not yank a content job's batch.

    claim_crawl_batch itself can't be tested here — it uses FOR UPDATE SKIP
    LOCKED, which SQLite cannot parse — but it carries the same crawl_phase
    predicate this exercises.
    """
    _make_state(db, 10, phase="identity", status="in_progress")
    _make_state(db, 11, phase="content", status="in_progress")
    db.flush()

    released = crawler_crud.release_in_progress_states(db, tier="http", phase="identity")
    assert released == 1
    assert db.get(CompanyCrawlState, 10).crawl_status == "pending"
    assert db.get(CompanyCrawlState, 11).crawl_status == "in_progress"


# ── Phase B must not destroy the identity crawl ───────────────────────────────

def test_content_delete_preserves_identity_pages(db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for page_type, crawled in [
        ("homepage", True), ("impressum", True), ("contact", True),
        ("team", True), ("services", True), ("other", True),
        ("news", False),  # inventory-only — phase B's frontier seed
    ]:
        db.add(CompanyWebPage(
            company_id=42, url_candidate_id=None, page_type=page_type,
            url=f"https://c42.ch/{page_type}", crawled=crawled, crawled_at=now,
        ))
    db.flush()

    deleted = crawler_crud.delete_content_pages_for_company(db, 42)
    assert deleted == 3  # team, services, other

    remaining = {
        (p.page_type, p.crawled)
        for p in db.query(CompanyWebPage).filter_by(company_id=42).all()
    }
    # The identity evidence behind the verdict survives, and so does the frontier.
    assert ("homepage", True) in remaining
    assert ("impressum", True) in remaining
    assert ("contact", True) in remaining
    assert ("news", False) in remaining


# ── Full-site crawl bounds ────────────────────────────────────────────────────

def _stub_page_result(page_type, url, final_url, status, body, company_id, cand_id=None, soup=None, text=None):
    """Stand-in for _make_page_result — skips lxml parsing and the S3 upload."""
    from app.services.enrichment.crawler_common import PageResult
    return PageResult(
        page_type=page_type, url=url, final_url=final_url, http_status=status,
        lang=None, word_count=0, image_count=0, video_count=0,
        has_contact_form=False,
    )

def test_crawl_site_full_respects_max_pages(monkeypatch):
    """A site that links to itself endlessly must still stop at max_pages."""
    from app.services.enrichment import crawler_http

    fetched: list[str] = []

    async def _fake_fetch(client, url, delay):
        fetched.append(url)
        body = (
            "<html><body>"
            f'<a href="/p{len(fetched)}">next</a>'
            f'<a href="/q{len(fetched)}">other</a>'
            "</body></html>"
        ).encode()
        return 200, url, {}, body

    monkeypatch.setattr(crawler_http, "_fetch", _fake_fetch)
    monkeypatch.setattr(crawler_http, "_make_page_result", _stub_page_result)

    result = asyncio.run(crawler_http.crawl_site_full(
        1, "https://x.ch/", max_pages=5, rate_limit_delay=0, max_depth=10,
        seed_urls=[("other", "https://x.ch/start")],
    ))

    assert len(result.pages) == 5
    assert result.failure_status is None


def test_crawl_site_full_skips_already_visited(monkeypatch):
    """Phase A's pages must never be re-fetched by phase B."""
    from app.services.enrichment import crawler_http

    fetched: list[str] = []

    async def _fake_fetch(client, url, delay):
        fetched.append(url)
        return 200, url, {}, b"<html><body>no links</body></html>"

    monkeypatch.setattr(crawler_http, "_fetch", _fake_fetch)
    monkeypatch.setattr(crawler_http, "_make_page_result", _stub_page_result)

    result = asyncio.run(crawler_http.crawl_site_full(
        1, "https://x.ch/", max_pages=10, rate_limit_delay=0,
        seed_urls=[("about", "https://x.ch/about"), ("impressum", "https://x.ch/impressum")],
        visited_urls={"https://x.ch/impressum"},
    ))

    assert fetched == ["https://x.ch/about"]
    assert len(result.pages) == 1


def test_crawl_site_full_reports_no_content_when_nothing_fetched(monkeypatch):
    from app.services.enrichment import crawler_http

    async def _fake_fetch(client, url, delay):
        return 404, url, {}, b""

    monkeypatch.setattr(crawler_http, "_fetch", _fake_fetch)

    result = asyncio.run(crawler_http.crawl_site_full(
        1, "https://x.ch/", max_pages=10, rate_limit_delay=0,
        seed_urls=[("about", "https://x.ch/about")],
    ))
    assert result.pages == []
    assert result.failure_status == "no_content"


# ── Real-site regressions (remarkt.ch, 2026-08-18) ────────────────────────────

def test_non_html_responses_are_never_stored():
    """is_crawlable_page_url filters by URL SUFFIX only, so an extensionless
    download endpoint (/download_file/view/51/187) reaches the fetch. Without a
    Content-Type check the crawler stored whole PDFs — 50k "words" of foreign
    language that also pollute the identity extract's name matching."""
    from app.services.enrichment.crawler_http import _is_html_response

    assert _is_html_response({"content-type": "text/html; charset=utf-8"}) is True
    assert _is_html_response({"content-type": "application/xhtml+xml"}) is True
    # A server that states nothing stays crawlable — plenty of small CH sites omit it.
    assert _is_html_response({}) is True

    for ctype in ("application/pdf", "image/png", "application/octet-stream",
                  "application/zip", "video/mp4"):
        assert _is_html_response({"content-type": ctype}) is False, ctype


def test_trailing_slash_urls_collapse_to_one_page():
    """/Unterstuetzung/spenden and /Unterstuetzung/spenden/ are one page, but
    were two frontier entries and two `visited` keys — so both were fetched,
    stored and extracted."""
    from app.services.enrichment.crawler_common import normalize_page_url

    a = normalize_page_url("https://x.ch/support/donate")
    b = normalize_page_url("https://x.ch/support/donate/")
    assert a == b
    # Fragments go too, and the bare root keeps its slash.
    assert normalize_page_url("https://x.ch/a#top") == "https://x.ch/a"
    assert normalize_page_url("https://x.ch/") == "https://x.ch/"


def test_recrawl_does_not_accumulate_duplicate_inventory_rows(db):
    """Inventory rows carry url_candidate_id IS NULL, so a candidate-scoped
    delete never matched them and save_page_inventory only de-dupes within one
    run — every re-crawl appended another full copy of the sitemap."""
    from datetime import datetime, timezone
    cand = CompanyUrlCandidate(company_id=77, url="https://x.ch", status="selected")
    db.add(cand)
    db.flush()

    now = datetime.now(timezone.utc)
    # One fetched page for the candidate, plus two sitemap-only inventory rows.
    db.add(CompanyWebPage(
        company_id=77, url_candidate_id=cand.id, page_type="homepage",
        url="https://x.ch/", crawled=True, crawled_at=now,
    ))
    for u in ("https://x.ch/shop", "https://x.ch/team"):
        db.add(CompanyWebPage(
            company_id=77, url_candidate_id=None, page_type="other", url=u,
            crawled=False, crawled_at=now, discovered_via="sitemap",
        ))
    db.flush()

    crawler_crud.delete_web_pages_for_candidate(db, 77, cand.id)
    db.commit()

    assert db.query(CompanyWebPage).filter_by(company_id=77).count() == 0, (
        "stale inventory survived the re-crawl and would be duplicated"
    )


def test_non_page_urls_are_rejected_as_website_candidates():
    """Whatever Google returned became a candidate "website" and was then fetched
    as a company homepage. Crawling a SHAB-notices PDF harvests OTHER companies'
    UIDs, which is how one PDF produced MISMATCH for several unrelated companies.
    """
    from datetime import datetime, timezone
    from app.services.enrichment.crawler_common import is_page_like_url

    for bad in (
        "https://www.sshv.ch/fileadmin/SHAB_Meldungen_Februar_2025.pdf",
        "https://x.ch/prospekt.docx",
        "https://x.ch/logo.svg",
        "ftp://x.ch/a",
        "not-a-url",
    ):
        assert is_page_like_url(bad) is False, bad

    for good in ("https://www.remarkt.ch/", "https://x.ch/impressum",
                 "http://x.ch/ueber-uns?lang=de"):
        assert is_page_like_url(good) is True, good

    rows = crawler_crud.build_candidate_rows(
        1,
        [{"link": "https://x.ch/notice.pdf"}, {"link": "https://x.ch/"}],
        datetime.now(timezone.utc),
    )
    assert [r["url"] for r in rows] == ["https://x.ch/"]


def test_extensionless_binary_urls_are_caught_at_fetch_not_by_the_url_filter():
    """The two filters are complementary, and neither alone is sufficient.

    A URL heuristic cannot classify
    `eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=...` — its path ends in
    `/PDF/`, not `.pdf` — and guessing harder would start rejecting real pages.
    Content-Type settles it at fetch time, before a byte of body is read.
    """
    from app.services.enrichment.crawler_common import is_page_like_url
    from app.services.enrichment.crawler_http import _is_html_response

    url = "https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=OJ:C:2019:399:FULL"
    assert is_page_like_url(url) is True, "URL filter cannot and need not catch this"
    assert _is_html_response({"content-type": "application/pdf"}) is False


def test_impressum_wins_the_budget_over_nav_links_found_earlier():
    """The impressum lives in the FOOTER; nav links come first in the DOM.

    find_subpage_links used to break out of the link scan as soon as
    max_subpages types were collected, then slice the DOM-ordered dict — so on a
    site with a rich nav the budget was spent before the crawler ever reached the
    one page the identity ladder reads. Observed on taxware.ch: services + team
    were fetched, /de/site/impressum (plain HTML, footer) was not.
    """
    from app.services.enrichment.crawler_common import find_subpage_links, parse_soup

    html = b"""
    <html><body>
      <nav>
        <a href="/de/site/loesungen">Loesungen</a>
        <a href="/de/site/ueber-taxware/team">Team</a>
        <a href="/de/site/ueber-taxware">Ueber uns</a>
      </nav>
      <footer>
        <a href="/de/site/impressum">Impressum</a>
        <a href="/de/site/kontakt-support">Kontakt &amp; Support</a>
      </footer>
    </body></html>
    """
    picked = find_subpage_links(parse_soup(html), "https://www.taxware.ch/de", max_subpages=2)

    assert "impressum" in picked, "impressum lost its slot to earlier nav links"
    assert picked["impressum"] == "https://www.taxware.ch/de/site/impressum"
    # _FETCH_PRIORITY order is impressum > contact > about > team > services.
    assert list(picked) == ["impressum", "contact"]


def test_subpage_selection_respects_the_budget():
    from app.services.enrichment.crawler_common import find_subpage_links, parse_soup

    html = b"""
    <html><body>
      <a href="/impressum">Impressum</a><a href="/kontakt">Kontakt</a>
      <a href="/ueber-uns">Ueber uns</a><a href="/team">Team</a>
      <a href="/leistungen">Leistungen</a>
    </body></html>
    """
    picked = find_subpage_links(parse_soup(html), "https://x.ch/", max_subpages=2)
    assert list(picked) == ["impressum", "contact"]
    assert len(picked) == 2
