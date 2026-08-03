"""Memory-bound invariants for the crawler.

These all guard against the same failure: the crawler-http pod (1 Gi limit) being
OOMKilled mid-run. Each test pins one thing that must stay bounded no matter how
many companies, domains, links or errors a run walks through.
"""
from __future__ import annotations

import asyncio

from app.services.enrichment import crawler_common, crawler_http
from app.services.enrichment.crawler_common import PageResult, has_contact_form
from app.services.enrichment.crawler_http import BoundedFrontier


def _stub_page(pt, u, fu, s, b, cid, cand=None, soup=None) -> PageResult:
    """Stand-in for _make_page_result — no lxml parse, no S3 upload."""
    return PageResult(
        page_type=pt, url=u, final_url=fu, http_status=s, lang=None,
        word_count=0, image_count=0, video_count=0, has_contact_form=False,
    )


# ── PageResult must not carry HTML ────────────────────────────────────────────

def test_page_result_has_no_html_field():
    """The HTML belongs in S3, not in the result object.

    Retaining it pinned every page of every concurrently crawled site in RAM for
    the whole batch (phase B: 10 sites x 60 pages = 600 documents). Nothing ever
    read the field. If someone re-adds it, this fails.
    """
    assert "html" not in PageResult.__dataclass_fields__


def test_page_result_keeps_only_the_s3_pointer():
    page = PageResult(
        page_type="homepage", url="https://x.ch/", final_url="https://x.ch/",
        http_status=200, lang="de", word_count=10, image_count=0, video_count=0,
        has_contact_form=False, s3_key_html="crawl/1/homepage.html",
    )
    assert page.s3_key_html == "crawl/1/homepage.html"
    # No field on the instance may hold a bytes payload.
    assert not any(isinstance(v, (bytes, bytearray)) for v in vars(page).values())


# ── has_contact_form works on bytes (avoids a full str copy per page) ─────────

def test_has_contact_form_accepts_bytes_and_str_alike():
    html = '<form action="/kontakt"><input name="email"></form>'
    assert has_contact_form(html) is True
    assert has_contact_form(html.encode()) is True
    assert has_contact_form(b"<html><body>nothing here</body></html>") is False


def test_has_contact_form_on_bytes_handles_non_utf8():
    """Latin-1 bytes must not raise — the crawler never pre-decodes any more."""
    assert has_contact_form("<form><input name='Grüezi'>".encode("latin-1")) is True


# ── The per-domain rate-limit table is LRU-capped ─────────────────────────────

def test_rate_limit_table_stays_bounded(monkeypatch):
    """~700k domains through a process-lifetime dict would grow without bound."""
    monkeypatch.setattr(crawler_common, "_MAX_TRACKED_DOMAINS", 50)
    crawler_common._domain_last_access.clear()

    async def _hammer():
        for i in range(500):
            await crawler_common.rate_limit(f"https://d{i}.ch/page", 0.001)

    asyncio.run(_hammer())
    assert len(crawler_common._domain_last_access) <= 50
    # The cap must evict the OLDEST domains, keeping the recent ones that could
    # still be inside their delay window.
    assert "d499.ch" in crawler_common._domain_last_access
    assert "d0.ch" not in crawler_common._domain_last_access
    crawler_common._domain_last_access.clear()


# ── Phase B's frontier is capped ──────────────────────────────────────────────

def test_bounded_frontier_drops_the_newest_not_the_queue_head():
    """Eviction order is the whole point: dropping from the front (what
    deque(maxlen=…) does) would discard the pages about to be crawled."""
    f = BoundedFrontier(max_pages=2, per_page_factor=2, floor=4)
    assert f.cap == 4

    for i in range(10):
        f.push(f"https://x.ch/p{i}", 1)

    assert len(f) == 4
    assert f.dropped == 6
    # The first four pushed survive, in order — BFS coverage is intact.
    assert [f.pop()[0] for _ in range(4)] == [f"https://x.ch/p{i}" for i in range(4)]
    assert not f


def test_bounded_frontier_cap_scales_with_the_page_budget():
    assert BoundedFrontier(max_pages=60).cap == 60 * 20
    # Small budgets still get a usable floor rather than a near-empty queue.
    assert BoundedFrontier(max_pages=1).cap == 64


def test_phase_b_frontier_stays_capped_on_a_link_dense_site(monkeypatch):
    """Each page here advertises 400 fresh links. Unbounded, the frontier would
    hold thousands of URLs per site — times every site crawled concurrently."""
    peak: list[int] = []
    real_push = BoundedFrontier.push

    def _spy_push(self, link, depth):
        ok = real_push(self, link, depth)
        peak.append(len(self))
        return ok

    monkeypatch.setattr(BoundedFrontier, "push", _spy_push)

    async def _fake_fetch(client, url, delay):
        uniq = "".join(f'<a href="/{url.rsplit("/", 1)[-1]}x{i}">l</a>' for i in range(400))
        return 200, url, {}, f"<html><body>{uniq}</body></html>".encode()

    monkeypatch.setattr(crawler_http, "_fetch", _fake_fetch)
    monkeypatch.setattr(crawler_http, "_make_page_result", _stub_page)

    result = asyncio.run(crawler_http.crawl_site_full(
        1, "https://x.ch/", max_pages=5, rate_limit_delay=0, max_depth=10,
        seed_urls=[("other", "https://x.ch/start")],
    ))

    assert len(result.pages) == 5
    # 5 pages x 400 links = 2000 pushes, but the queue never exceeded its cap.
    assert max(peak) <= BoundedFrontier(max_pages=5).cap == 100


def test_phase_b_bounded_frontier_still_reaches_max_pages(monkeypatch):
    """Capping the frontier must not starve the crawl of pages to visit."""
    n = 0

    async def _fake_fetch(client, url, delay):
        # Every page must offer genuinely new URLs, or the crawl runs out of
        # frontier for reasons unrelated to the cap under test.
        nonlocal n
        n += 1
        body = (
            f'<html><body><a href="/a{n}">a</a><a href="/b{n}">b</a></body></html>'
        ).encode()
        return 200, url, {}, body

    monkeypatch.setattr(crawler_http, "_fetch", _fake_fetch)
    monkeypatch.setattr(crawler_http, "_make_page_result", _stub_page)

    result = asyncio.run(crawler_http.crawl_site_full(
        1, "https://x.ch/", max_pages=12, rate_limit_delay=0, max_depth=10,
        seed_urls=[("other", "https://x.ch/s01")],
    ))
    assert len(result.pages) == 12


# ── The job's error sample is capped ──────────────────────────────────────────

def test_track_error_caps_the_sample_but_keeps_the_count():
    """stats is json.dumps'd into job_runs on EVERY batch — an uncapped list made
    each write proportional to all errors so far."""
    from app.services.jobs.job_handlers.web_crawl import (
        _MAX_TRACKED_ERRORS,
        _track_error,
    )

    stats: dict = {"errors": []}
    for i in range(5_000):
        _track_error(stats, f"company {i}: boom")

    assert stats["error_count"] == 5_000
    assert len(stats["errors"]) == _MAX_TRACKED_ERRORS + 1
    assert stats["errors"][0] == "company 0: boom"
    assert "omitted" in stats["errors"][-1]
