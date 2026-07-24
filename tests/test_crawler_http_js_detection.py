"""crawl_company_http must check JS-shell detection BEFORE the near-empty-body
check. A minimal SPA shell (bare <div id="root"> + a script tag) is easily
under the no_content threshold (<5 words, <500 bytes) — if no_content were
checked first, such a page would be wrongly written off as a terminal,
non-retried failure instead of correctly escalating to the Playwright tier
that can actually render it.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from app.services.enrichment import crawler_http

_TINY_SPA_SHELL = (
    b'<html><head><title>App</title></head>'
    b'<body><div id="root"></div><script src="/main.js"></script></body></html>'
)

_GENUINELY_EMPTY_PAGE = b'<html><head></head><body></body></html>'


def _run(coro):
    return asyncio.run(coro)


def test_tiny_spa_shell_escalates_to_playwright_not_no_content():
    assert len(_TINY_SPA_SHELL) < 500  # confirms this would also match the no_content threshold

    async def _go():
        with patch.object(crawler_http, "_fetch", new=AsyncMock(return_value=(200, "https://spa.ch/", {}, _TINY_SPA_SHELL))):
            with patch.object(crawler_http, "_fetch_curl_impersonate", new=AsyncMock(return_value=None)):
                return await crawler_http.crawl_company_http(1, "https://spa.ch/", use_sitemap=False, max_pages=1)

    result = _run(_go())
    assert result.needs_playwright is True
    assert result.failure_status is None


def test_genuinely_empty_page_still_reports_no_content():
    async def _go():
        with patch.object(crawler_http, "_fetch", new=AsyncMock(return_value=(200, "https://empty.ch/", {}, _GENUINELY_EMPTY_PAGE))):
            with patch.object(crawler_http, "_fetch_curl_impersonate", new=AsyncMock(return_value=None)):
                return await crawler_http.crawl_company_http(1, "https://empty.ch/", use_sitemap=False, max_pages=1)

    result = _run(_go())
    assert result.needs_playwright is False
    assert result.failure_status == "no_content"
