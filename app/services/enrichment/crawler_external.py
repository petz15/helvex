"""Tier 3 crawler — paid external scrape (ScrapingDog).

The last rung of the escalation ladder:

    httpx  ──bot_blocked──▶  curl_cffi (real Chrome TLS)  ──still blocked──▶
    Playwright (real browser)  ──still blocked──▶  THIS (residential proxy)

Every page here costs credits, so the job that drives it enforces a per-run
budget and only ever claims companies that already failed the free tiers
(`tier='external'`). Identity-phase only by default: proving *which* site is the
company is worth real money, re-crawling 40 content pages of it usually is not.
"""

from __future__ import annotations

import logging

from app import crud
from app.config import settings
from app.services.enrichment.crawler_common import (
    CrawlResult,
    PageResult,
    detect_bot_block,
    find_subpage_links,
    parse_soup,
    run_in_page_executor,
)
from app.services.enrichment.crawler_http import _make_page_result

logger = logging.getLogger(__name__)


def _homepage_and_subpages(
    url: str,
    final_url: str,
    status: int,
    body: bytes,
    company_id: int,
    url_candidate_id: int | None,
    want_subpages: bool,
) -> tuple[PageResult, dict[str, str]]:
    """One parse: build the homepage PageResult and pick the follow-up link.

    Runs in the page executor. Returns at most one subpage — each extra fetch
    here is a billed request.
    """
    soup = parse_soup(body)
    try:
        page = _make_page_result(
            "homepage", url, final_url, status, body, company_id,
            url_candidate_id, soup, None, metrics=False,
        )
        subpages = (
            find_subpage_links(soup, final_url, max_subpages=1) if want_subpages else {}
        )
        return page, subpages
    finally:
        del soup


def get_external_api_key(db) -> str:
    """DB setting wins over the env default, matching the search client."""
    return (
        crud.get_setting(db, "scrapingdog_api_key", "")
        or settings.scrapingdog_api_key
        or ""
    )


def is_external_tier_enabled(db) -> bool:
    return bool(get_external_api_key(db))


async def crawl_company_external(
    company_id: int,
    url: str,
    *,
    url_candidate_id: int | None = None,
    max_pages: int = 2,
    rate_limit_delay: float = 0.0,
    api_key: str = "",
) -> CrawlResult:
    """Fetch a company's identity pages through the paid scrape API.

    `max_pages` defaults to 2 (homepage + one impressum/contact link), not the
    3–5 the free tiers use: each page is a billed request, and the UID that
    settles identity is almost always on the homepage or the impressum.

    rate_limit_delay is accepted for signature compatibility with the other
    crawl functions but ignored — the provider pools its own proxies, and we are
    not the ones hitting the origin.
    """
    from app.clients.scrapingdog_scrape_client import (
        ExternalScrapeError,
        fetch_rendered_html,
    )

    result = CrawlResult()
    if not api_key:
        result.failure_status = "http_error"
        result.failure_detail = "External scrape tier has no API key configured"
        return result

    try:
        status, final_url, body = await fetch_rendered_html(url, api_key=api_key)
    except ExternalScrapeError as exc:
        result.failure_status = "bot_blocked"
        result.failure_detail = f"external: {exc}"
        result.bot_protection_type = "external_failed"
        return result

    # Even the paid tier gets served challenge pages sometimes. Treat that as a
    # terminal bot block: there is no further tier to escalate to.
    blocked, ptype = detect_bot_block(status, {}, body.decode("utf-8", errors="replace"))
    if blocked:
        result.bot_blocked = True
        result.bot_protection_type = ptype
        result.failure_status = "bot_blocked"
        result.failure_detail = "external tier also bot-blocked"
        return result

    # Parse + S3 upload off the event loop, like every other crawl tier — doing
    # it inline stalls the other companies sharing this loop.
    page, subpages = await run_in_page_executor(
        _homepage_and_subpages, url, final_url, status, body, company_id,
        url_candidate_id, max_pages > 1,
    )
    result.pages.append(page)
    del body

    # One extra billed page, at most: the impressum/contact where the UID lives.
    for page_type, sub_url in subpages.items():
        try:
            s_status, s_final, s_body = await fetch_rendered_html(
                sub_url, api_key=api_key
            )
        except ExternalScrapeError as exc:
            logger.debug("external subpage %s failed: %s", sub_url, exc)
            break
        result.pages.append(
            await run_in_page_executor(
                _make_page_result, page_type, sub_url, s_final, s_status, s_body,
                company_id, url_candidate_id, None, None, metrics=False,
            )
        )
        break  # hard stop at one — this is the budget, not a loop

    return result
