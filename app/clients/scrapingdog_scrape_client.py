"""ScrapingDog **web scraping** API — the crawler's last-resort fetch tier.

Distinct from `scrapingdog_search_client`, which calls ScrapingDog's *Google
Search* endpoint to discover candidate URLs. This module fetches the HTML of one
specific page through ScrapingDog's residential-proxy + browser-render service,
for the small set of sites that defeat both httpx (even with curl_cffi TLS
impersonation) and headless Playwright.

Every call costs credits, so this tier is never reached automatically from the
HTTP crawler — a company must first be escalated to Playwright and fail there
too. See `web_crawl.handle_web_crawl_external` for the per-run budget cap.
"""

from __future__ import annotations

import logging

import httpx

from app.services.enrichment.crawler_common import MAX_PAGE_BYTES, resolve_is_public

logger = logging.getLogger(__name__)

_SCRAPINGDOG_URL = "https://api.scrapingdog.com/scrape"

# ScrapingDog bills per request and more for JS rendering / premium proxies.
# Defaults here are the expensive-but-works combination, because by the time a
# company reaches this tier the cheap options have already failed.
_DEFAULT_TIMEOUT = 90.0


class ExternalScrapeError(RuntimeError):
    """The external fetch failed — caller should mark the crawl failed, not retry."""


def _scrub(message: str, api_key: str) -> str:
    """Strip the API key out of anything derived from the request/response.

    ScrapingDog takes the key as a QUERY PARAMETER, so it is part of the request
    URL — and httpx exceptions and provider error bodies can echo that URL back.
    These messages do not stay in the process: they become `failure_detail`, get
    persisted to `company_crawl_state.crawl_error_detail`, and are rendered in the
    admin crawler-failures table. Without this, one provider error page could put
    a live credential on an operator's screen and in the database.
    """
    if api_key and api_key in message:
        message = message.replace(api_key, "***")
    return message


async def fetch_rendered_html(
    url: str,
    *,
    api_key: str,
    render_js: bool = True,
    premium: bool = True,
    country: str = "ch",
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, str, bytes]:
    """Fetch one URL through ScrapingDog. Returns (status, final_url, html_bytes).

    Raises ExternalScrapeError on transport failure or a non-2xx from the
    provider itself (as opposed to the target site, whose status is returned).

    The SSRF guard still applies: ScrapingDog fetches on our behalf, so a target
    that resolves internally is not a risk to us the way a direct fetch is, but
    refusing it keeps one rule for what this crawler is allowed to request and
    avoids paying credits for a URL the rest of the pipeline would reject.
    """
    from urllib.parse import urlparse

    host = urlparse(url).hostname
    if not host or not await resolve_is_public(host):
        raise ExternalScrapeError(f"refusing non-public host: {host!r}")

    params = {
        "api_key": api_key,
        "url": url,
        "dynamic": "true" if render_js else "false",
        "premium": "true" if premium else "false",
        "country": country,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(_SCRAPINGDOG_URL, params=params)
    except Exception as exc:  # noqa: BLE001
        raise ExternalScrapeError(
            f"ScrapingDog request failed: {_scrub(str(exc), api_key)}"
        ) from exc

    if resp.status_code == 401:
        raise ExternalScrapeError("ScrapingDog rejected the API key (401)")
    if resp.status_code == 402:
        raise ExternalScrapeError("ScrapingDog credits exhausted (402)")
    if resp.status_code >= 400:
        raise ExternalScrapeError(
            f"ScrapingDog returned HTTP {resp.status_code}: "
            f"{_scrub(resp.text[:200], api_key)}"
        )

    body = resp.content[:MAX_PAGE_BYTES]
    if not body:
        raise ExternalScrapeError("ScrapingDog returned an empty body")

    # The provider proxies the target's body; it does not report the target's
    # own status separately, so a 200 here means "we got the page".
    return 200, url, body
