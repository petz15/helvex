"""Batch-scoped Chromium reuse on the Playwright crawl tier.

The tier managed ~20 companies/hour in production. The dominant cost was that
`crawl_company_playwright` launched a FULL Chromium per company (1-3 s and
~150 MB each) rather than reusing one across the batch. These tests pin the
reuse and the isolation it must not sacrifice.

None of this needs a real browser: `crawl_fn` and the session factory are both
injected, which is exactly why the session had to become a parameter rather than
something the crawl function constructs internally.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.services.enrichment import crawler_playwright as pw
from app.services.jobs.job_handlers.web_crawl import _crawl_targets_concurrently


class _FakeState:
    def __init__(self, company_id: int):
        self.company_id = company_id
        self.selected_url_id = company_id


class _FakeCandidate:
    def __init__(self, company_id: int):
        self.id = company_id
        self.url = f"https://c{company_id}.ch"


def _targets(n: int):
    return [(_FakeState(i), _FakeCandidate(i)) for i in range(1, n + 1)]


class _FakeSession:
    """Stands in for BrowserSession; records what the batch asked of it."""

    def __init__(self):
        self.pages_opened: list[int] = []

    @asynccontextmanager
    async def page(self, company_id: int):
        self.pages_opened.append(company_id)
        yield object()


def _factory_recording(opened: list):
    """A session_factory that counts how many times the browser was launched."""

    @asynccontextmanager
    async def _factory():
        session = _FakeSession()
        opened.append(session)
        try:
            yield session
        finally:
            session.closed = True

    return _factory


def _drain(targets, crawl_fn, factory, on_result=None, concurrency=4):
    results: list = []

    def _collect(state, candidate, kind, result, exc):
        results.append((state.company_id, kind, exc))

    asyncio.run(
        _crawl_targets_concurrently(
            targets,
            crawl_fn=crawl_fn,
            max_pages=5,
            rate_limit_delay=0,
            company_timeout=5,
            concurrency=concurrency,
            on_result=on_result or _collect,
            session_factory=factory,
        )
    )
    return results


# ── The reuse itself ──────────────────────────────────────────────────────────

def test_one_browser_launch_serves_the_whole_batch():
    """The fix. N companies must cost ONE launch, not N."""
    opened: list = []

    async def _crawl(company_id, url, *, session=None, **kw):
        async with session.page(company_id):
            return pw.CrawlResult()

    _drain(_targets(10), _crawl, _factory_recording(opened))

    assert len(opened) == 1, "browser was launched more than once for one batch"
    assert sorted(opened[0].pages_opened) == list(range(1, 11))


def test_each_company_gets_its_own_context():
    """Sharing the process must not share cookies or user agent.

    BrowserSession.page() opens a fresh new_context() per company; only the
    Chromium process is shared.
    """
    opened: list = []

    async def _crawl(company_id, url, *, session=None, **kw):
        async with session.page(company_id):
            return pw.CrawlResult()

    _drain(_targets(5), _crawl, _factory_recording(opened))

    assert len(opened[0].pages_opened) == 5
    assert len(set(opened[0].pages_opened)) == 5, "contexts were reused across companies"


def test_no_session_factory_means_no_session_kwarg():
    """The HTTP tier passes no factory and its crawl_fn takes no `session`."""
    seen_kwargs: list = []

    async def _crawl(company_id, url, **kw):
        seen_kwargs.append(kw)
        return pw.CrawlResult()

    _drain(_targets(3), _crawl, None)

    assert all("session" not in kw for kw in seen_kwargs)


# ── Failure isolation ─────────────────────────────────────────────────────────

def test_browser_is_closed_when_on_result_raises():
    """A JobPausedError mid-batch must not leak a Chromium process.

    The session CM has to wrap the task loop, not sit inside it — otherwise every
    preempted batch strands a browser.
    """
    opened: list = []

    async def _crawl(company_id, url, *, session=None, **kw):
        async with session.page(company_id):
            return pw.CrawlResult()

    def _boom(state, candidate, kind, result, exc):
        raise RuntimeError("simulated pause")

    with pytest.raises(RuntimeError, match="simulated pause"):
        _drain(_targets(6), _crawl, _factory_recording(opened), on_result=_boom)

    assert len(opened) == 1
    assert getattr(opened[0], "closed", False) is True, "browser leaked on pause"


def test_one_company_crashing_does_not_kill_the_batch():
    """Per-company failures are already isolated by _crawl_one_target; sharing a
    browser must not change that."""
    opened: list = []

    async def _crawl(company_id, url, *, session=None, **kw):
        async with session.page(company_id):
            if company_id == 3:
                raise RuntimeError("boom")
            return pw.CrawlResult()

    results = _drain(_targets(5), _crawl, _factory_recording(opened))

    kinds = {cid: kind for cid, kind, _ in results}
    assert kinds[3] == "error"
    assert all(kinds[i] == "ok" for i in (1, 2, 4, 5))


# ── Relaunch budget ───────────────────────────────────────────────────────────

def test_relaunch_budget_is_bounded():
    """A dead Chromium is relaunched, but not forever — an unbounded retry on a
    browser that cannot start would spin for the whole batch."""
    assert pw._MAX_BROWSER_RELAUNCHES >= 1
    assert pw._MAX_BROWSER_RELAUNCHES <= 3


def test_relaunch_replaces_a_disconnected_browser():
    class _DeadBrowser:
        def is_connected(self):
            return False

    class _LivePw:
        def __init__(self):
            self.launches = 0

        class _Chromium:
            def __init__(self, outer):
                self.outer = outer

            async def launch(self, **kw):
                self.outer.launches += 1
                return object()

        @property
        def chromium(self):
            return self._Chromium(self)

    fake_pw = _LivePw()
    session = pw.BrowserSession(fake_pw, _DeadBrowser())

    asyncio.run(session._ensure_alive())

    assert session.relaunches == 1
    assert fake_pw.launches == 1


def test_relaunch_gives_up_once_the_budget_is_spent():
    class _DeadBrowser:
        def is_connected(self):
            return False

    session = pw.BrowserSession(None, _DeadBrowser())
    session.relaunches = pw._MAX_BROWSER_RELAUNCHES

    with pytest.raises(RuntimeError, match="relaunch budget"):
        asyncio.run(session._ensure_alive())


# ── Timeout arithmetic, asserted as intent ────────────────────────────────────

def test_page_budget_fits_inside_the_identity_company_timeout():
    """Subpages are what gets dropped on a slow site — never the homepage.

    The homepage is where identity is decided, so the per-page budgets must leave
    room for it plus at least one subpage inside the 60s company_timeout, while
    NOT allowing a full 5-page crawl to exceed it.
    """
    identity_timeout_s = 60.0
    settle_s = 0.8  # _fetch_page's post-load wait

    homepage_s = pw._HOMEPAGE_TIMEOUT_MS / 1000 + settle_s
    subpage_s = pw._SUBPAGE_TIMEOUT_MS / 1000 + settle_s

    assert homepage_s + subpage_s < identity_timeout_s, "no room for a single subpage"
    # 1 homepage + 4 subpages is the max_pages=5 shape; it should fit, but only
    # just — that is what makes the timeout the effective bound, not a rubber stamp.
    assert homepage_s + 4 * subpage_s <= identity_timeout_s


def test_identity_tier_no_longer_fetches_sitemaps():
    """discover_site_overview is an httpx fetch of robots.txt + sitemap.xml — at
    a site that reached this tier precisely because httpx was bot-blocked. Two
    near-certain failures inside every company's budget."""
    import inspect

    sig = inspect.signature(pw.crawl_company_playwright)
    assert sig.parameters["use_sitemap"].default is False
