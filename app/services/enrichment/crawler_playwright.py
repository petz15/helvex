"""Playwright-based crawler for JS-heavy and bot-protected sites.

Uses Chromium in headless mode with playwright-stealth patches to minimise
bot-detection signals. Same CrawlResult contract as crawler_http.
"""
from __future__ import annotations

import logging

import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from app.services.platform import s3_client
from app.services.enrichment.crawler_common import (
    ACCEPT_LANGUAGE,
    MAX_PAGE_BYTES,
    CrawlResult,
    PageResult,
    classify_all_urls,
    classify_page_type,
    classify_urls_by_path,
    count_media,
    page_text,
    detect_bot_block,
    detect_page_language,
    extract_internal_links,
    find_subpage_links,
    has_contact_form,
    is_crawlable_page_url,
    parse_soup,
    pick_user_agent,
    rate_limit,
    resolve_is_public,
    run_in_page_executor,
)

logger = logging.getLogger(__name__)

_VIEWPORT = {"width": 1280, "height": 800}

# Optional: launch a real Chrome/Edge instead of bundled Chromium for a stronger
# fingerprint against Cloudflare JS challenges. Empty (default) uses bundled
# Chromium, which is what the ml image installs. Set to "chrome" / "msedge" only
# in images where that browser is installed (`playwright install chrome`).
_CHANNEL = os.environ.get("PLAYWRIGHT_CHANNEL", "").strip() or None

# Resource types we never need (we only want DOM + text). Aborting them makes
# the crawl 2–4× faster, cuts memory, and reduces the automation footprint.
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})


async def _guard_and_filter_resources(route) -> None:
    """Runs on every network request the page (or Playwright's own navigation)
    makes. Two defenses combined:

      - drops image/media/font (we only need DOM + text — also reduces the
        automation footprint / speeds up the crawl, the original purpose).
      - SSRF guard: unlike the httpx crawler, this one renders real JS, so a
        malicious site's own script (fetch/XHR/further navigation) could
        otherwise pivot to internal services from within the crawler's
        network namespace. Every request — including the top-level navigation
        to the crawled URL itself — is resolved and refused if it points at a
        private/loopback/link-local/metadata/reserved address.
    """
    try:
        request = route.request
        if request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        host = urlparse(request.url).hostname
        if not host or not await resolve_is_public(host):
            logger.warning("crawler.ssrf_blocked(playwright) host=%s url=%s", host, request.url[:120])
            await route.abort()
            return
        await route.continue_()
    except Exception:  # noqa: BLE001
        # If the route was already handled/closed, ignore.
        pass

# Launch args required for headless Chromium inside K8s/Docker containers.
# --no-sandbox:               containers typically lack the Linux namespace
#                             privileges that Chromium's sandbox needs.
# --disable-dev-shm-usage:    /dev/shm is often very small in containers;
#                             without this flag Chrome crashes on memory writes.
# --disable-blink-features=AutomationControlled:
#                             removes navigator.webdriver=true signal that
#                             complements playwright-stealth's JS patches.
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--disable-background-networking",
]


async def _dismiss_consent_banner(page) -> None:
    """Try to dismiss GDPR/cookie consent banners using known CMP selectors.

    Covers the CMPs most common on Swiss/European company sites.
    Silently no-ops if no banner is found — never raises.
    """
    # CSS selectors for the "Accept All" button of each major CMP.
    # Ordered roughly by prevalence on Swiss sites.
    _CMP_SELECTORS = [
        # OneTrust / CookiePro (widespread in large companies)
        "#onetrust-accept-btn-handler",
        # Cookiebot / Cybot (very common in DACH region)
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "#CybotCookiebotDialogBodyButtonAccept",
        # Axeptio (popular in French-speaking Switzerland)
        ".axeptio_btn_acceptAll",
        ".axeptio_btn[data-cy='btn_acceptAll']",
        # Usercentrics (popular in German-speaking Switzerland)
        "[data-testid='uc-accept-all-button']",
        "button.sc-dcJsrY",  # Usercentrics v2 compiled class
        # consentmanager.net (Google-certified, widely used in EU)
        "#cmpwrapper #cmpbntyestxt",
        ".cmpboxbtnyes",
        # Klaro (open-source, self-hosted)
        ".cm-btn.cm-btn-success",
        # Didomi
        "#didomi-notice-agree-button",
        ".didomi-components-button--variant-highlight",
        # Google Funding Choices / FC consent
        ".fc-button.fc-cta-consent",
        # Borlabs Cookie (WordPress, common on SME sites)
        "#borlabs-cookie-accept",
        ".borlabs-cookie .cookie-btn",
        # Generic IAB TCF v2 / fallback
        "button[id*='accept-all']",
        "button[class*='accept-all']",
        "a[id*='accept-all']",
    ]

    # Text-based fallback: find buttons containing acceptance text in CH languages.
    _ACCEPT_TEXTS = [
        "alle akzeptieren", "akzeptieren", "zustimmen", "einverstanden",
        "tout accepter", "j'accepte", "accepter tout", "accepter",
        "accetta tutto", "accetta",
        "accept all", "accept", "agree",
        "ok", "verstanden",
    ]

    try:
        # Give banner time to appear after networkidle
        await page.wait_for_timeout(600)

        # Try known CMP selectors first
        for selector in _CMP_SELECTORS:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=300):
                    await el.click(timeout=500)
                    logger.debug("Dismissed consent banner via selector: %s", selector)
                    await page.wait_for_timeout(300)
                    return
            except Exception:
                continue

        # Text-based fallback: search all visible buttons
        buttons = page.locator("button, a[role='button'], [type='button']")
        count = await buttons.count()
        for i in range(min(count, 20)):  # cap scan at 20 elements
            try:
                btn = buttons.nth(i)
                if not await btn.is_visible(timeout=200):
                    continue
                text = (await btn.inner_text()).strip().lower()
                if any(t in text for t in _ACCEPT_TEXTS):
                    await btn.click(timeout=500)
                    logger.debug("Dismissed consent banner via text match: %r", text)
                    await page.wait_for_timeout(300)
                    return
            except Exception:
                continue

    except Exception:
        pass  # Never let banner handling break the crawl


async def _get_bounded_html(page, cap: int = MAX_PAGE_BYTES) -> str:
    """Serialize the DOM, bounded against a pathologically huge document.

    page.content() serializes the *entire* DOM over the CDP protocol — a
    malicious page whose JS generates millions of nodes ("DOM bomb") would
    transfer the whole multi-hundred-MB string before we ever got to truncate
    it. Checking the length first (a cheap evaluate() round-trip returning
    just a number) lets us pull only a bounded prefix over the wire instead.
    """
    try:
        size = await page.evaluate("() => document.documentElement.outerHTML.length")
    except Exception:  # noqa: BLE001
        return await page.content()
    if isinstance(size, (int, float)) and size > cap * 4:  # chars vs bytes — generous margin
        try:
            return await page.evaluate(f"() => document.documentElement.outerHTML.slice(0, {cap})")
        except Exception:  # noqa: BLE001
            pass
    return await page.content()


# Per-page navigation budgets. Deliberately tight: a bot wall answers fast or
# not at all, so a long nav timeout buys nothing on this tier and burns the
# company's whole budget. 1 homepage + 4 subpages sits just inside the 60s
# identity company_timeout, which means SUBPAGES are what gets dropped when a
# site is slow — never the homepage, which is where identity is decided.
_HOMEPAGE_TIMEOUT_MS = 10_000
_SUBPAGE_TIMEOUT_MS = 12_000


async def _fetch_page(
    page,
    url: str,
    timeout_ms: int = _HOMEPAGE_TIMEOUT_MS,
    rate_limit_delay: float = 0.5,
) -> tuple[int, str, dict, bytes]:
    """Navigate to url and return (status, final_url, headers, body)."""
    await rate_limit(url, rate_limit_delay)
    # "load" rather than "networkidle": networkidle frequently never fires on
    # sites with analytics/long-polling, causing spurious timeouts. "load" plus a
    # short settle gives JS frameworks time to hydrate without hanging.
    response = await page.goto(url, wait_until="load", timeout=timeout_ms)
    await page.wait_for_timeout(800)
    # Attempt to dismiss consent banners before capturing content.
    # Called on every page so impressum/privacy banners are also handled.
    await _dismiss_consent_banner(page)
    status = response.status if response else 0
    final_url = page.url
    headers = dict(response.headers) if response else {}
    html_str = await _get_bounded_html(page)
    html_bytes = html_str.encode("utf-8", errors="replace")[:MAX_PAGE_BYTES]
    return status, final_url, headers, html_bytes


def _make_page_result(
    page_type: str,
    url: str,
    final_url: str,
    http_status: int,
    html_bytes: bytes,
    company_id: int,
    url_candidate_id: int | None = None,
    text: str | None = None,
    soup=None,
    *,
    metrics: bool = True,
) -> PageResult:
    """See crawler_http._make_page_result — same `metrics`/`soup` contract.

    The Playwright tier is an identity-phase escalation (js_required), so it
    passes metrics=False for the same reason phase A does: the extract that
    decides identity re-parses the HTML from S3.
    """
    images = videos = lang = contact_form = None
    words = len(text.split()) if text is not None else None

    if metrics:
        if soup is None:
            soup = parse_soup(html_bytes)
        images, videos = count_media(soup)
        # One get_text() traversal feeds both — see crawler_common.page_text.
        if text is None:
            text = page_text(soup)
            words = len(text.split())
        lang = detect_page_language(soup, text=text)
        contact_form = has_contact_form(html_bytes)
        del soup  # drop the tree (~10x the source size) before the blocking S3 PUT

    s3_key: str | None = None
    if s3_client.is_crawl_bucket_configured():
        try:
            s3_key = s3_client.crawl_s3_key(company_id, page_type, url_candidate_id)
            s3_client.upload_crawl_html(html_bytes, s3_key)
        except Exception:
            logger.warning("S3 upload failed for company %d / %s", company_id, page_type, exc_info=True)
            s3_key = None

    return PageResult(
        page_type=page_type,
        url=url,
        final_url=final_url,
        http_status=http_status,
        lang=lang,
        word_count=words,
        image_count=images,
        video_count=videos,
        has_contact_form=contact_form,
        s3_key_html=s3_key,
    )


def _links_only(html_bytes: bytes, base_url: str) -> list[str]:
    """Parse and return only the links — the soup never escapes to the caller."""
    soup = parse_soup(html_bytes)
    try:
        return extract_internal_links(soup, base_url)
    finally:
        del soup


def _page_result_and_links(
    page_type: str,
    url: str,
    final_url: str,
    http_status: int,
    html_bytes: bytes,
    company_id: int,
    url_candidate_id: int | None,
    want_links: bool,
) -> tuple[PageResult, list[str]]:
    """One parse for both the PageResult and the frontier links — mirrors
    crawler_http._page_result_and_links. Runs in the page executor."""
    if not want_links:
        return _make_page_result(
            page_type, url, final_url, http_status, html_bytes, company_id,
            url_candidate_id,
        ), []
    soup = parse_soup(html_bytes)
    try:
        page = _make_page_result(
            page_type, url, final_url, http_status, html_bytes, company_id,
            url_candidate_id, soup=soup,
        )
        return page, extract_internal_links(soup, final_url)
    finally:
        del soup


# How many times one session may relaunch a dead browser. A Chromium that OOMs
# or has its target closed would otherwise take every remaining company in the
# batch down with it — the whole point of sharing the browser is that one crash
# is now a batch-wide event rather than a per-company one.
_MAX_BROWSER_RELAUNCHES = 2


class BrowserSession:
    """One Chromium, reused across every company in a crawl batch.

    Launching a browser costs 1–3 s and ~150 MB; opening a context costs tens of
    milliseconds. The crawler used to launch one browser PER COMPANY, which is
    the dominant reason the Playwright tier managed ~20 companies/hour.

    Per-company isolation is preserved exactly: `page()` opens a fresh
    `new_context()` with that company's own user agent and its own cookie jar,
    and closes it on exit. Only the process is shared.

    Lifetime note: `_run_crawl_batch` calls `asyncio.run()` once per batch, so
    every batch gets a fresh event loop that is then closed. Playwright objects
    are bound to their creating loop, which is why this is batch-scoped and not
    a process-wide singleton.
    """

    __slots__ = ("_pw", "_browser", "relaunches")

    def __init__(self, pw, browser):
        self._pw = pw
        self._browser = browser
        self.relaunches = 0

    async def _launch(self):
        launch_kwargs: dict = {"headless": True, "args": _LAUNCH_ARGS}
        if _CHANNEL:
            launch_kwargs["channel"] = _CHANNEL
        try:
            return await self._pw.chromium.launch(**launch_kwargs)
        except Exception:
            # Configured channel (e.g. "chrome") not installed in this image —
            # fall back to bundled Chromium.
            return await self._pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)

    async def _ensure_alive(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        if self.relaunches >= _MAX_BROWSER_RELAUNCHES:
            raise RuntimeError(
                f"Chromium died and the relaunch budget ({_MAX_BROWSER_RELAUNCHES}) is spent"
            )
        self.relaunches += 1
        logger.warning(
            "Playwright browser was disconnected — relaunching (%d/%d)",
            self.relaunches, _MAX_BROWSER_RELAUNCHES,
        )
        self._browser = await self._launch()
        return self._browser

    @asynccontextmanager
    async def page(self, company_id: int):
        """Yield a stealth-patched, SSRF-guarded page in its own fresh context."""
        browser = await self._ensure_alive()
        context = await browser.new_context(
            viewport=_VIEWPORT,
            user_agent=pick_user_agent(company_id),
            locale="de-CH",
            # Match HTTP crawler's full CH language negotiation.
            extra_http_headers={"Accept-Language": ACCEPT_LANGUAGE},
            # Ignore SSL cert errors (expired/self-signed certs are common on
            # small Swiss company sites). Equivalent to httpx verify=False.
            ignore_https_errors=True,
        )
        # Drop images/fonts/media, and SSRF-guard every request the page's own
        # JS might make (see _guard_and_filter_resources).
        await context.route("**/*", _guard_and_filter_resources)
        try:
            yield await context.new_page()
        finally:
            # Close the CONTEXT, not the browser — the next company reuses it.
            try:
                await context.close()
            except Exception:  # noqa: BLE001 — browser may already be gone
                pass


@asynccontextmanager
async def browser_session():
    """Launch one Chromium for a whole batch; always closes it.

    Pass this (the factory itself, uncalled) as `_run_crawl_batch`'s
    `session_factory` — the driver enters it once and hands the resulting
    BrowserSession to every company's crawl.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed in this image. "
            "Add playwright/playwright-stealth to requirements.ml.txt."
        ) from exc

    # Stealth().use_async() wraps async_playwright and auto-applies stealth
    # patches to every new page/context before any navigation.
    async with Stealth().use_async(async_playwright()) as pw:
        session = BrowserSession(pw, None)
        session._browser = await session._launch()
        try:
            yield session
        finally:
            if session._browser is not None:
                try:
                    await session._browser.close()
                except Exception:  # noqa: BLE001
                    pass


@asynccontextmanager
async def _browser_page(company_id: int):
    """One-off page for a single company — opens and closes its own browser.

    Thin wrapper over browser_session() so the single-company path cannot drift
    from the batch path on stealth patches, the resource route guard, or the CH
    locale headers. Batch callers must use browser_session() directly.
    """
    async with browser_session() as session:
        async with session.page(company_id) as page:
            yield page


@asynccontextmanager
async def _page_for(session: "BrowserSession | None", company_id: int):
    """A page from the batch's shared browser, or a throwaway one if solo.

    Lets both crawl functions accept `session=None` without branching: batch
    callers amortise one launch across the whole batch, single-company callers
    (web_crawl_single, the fallback chain) behave exactly as before.
    """
    if session is not None:
        async with session.page(company_id) as page:
            yield page
    else:
        async with _browser_page(company_id) as page:
            yield page


async def crawl_site_full_playwright(
    company_id: int,
    url: str,
    *,
    url_candidate_id: int | None = None,
    max_pages: int = 60,
    rate_limit_delay: float = 0.5,
    max_depth: int = 3,
    seed_urls: list[tuple[str, str]] | None = None,
    visited_urls: set[str] | None = None,
    session: "BrowserSession | None" = None,
) -> CrawlResult:
    """Phase B for sites that need a real browser — the Playwright twin of
    `crawler_http.crawl_site_full`.

    `session`: a batch-scoped BrowserSession. When None the function opens its
    own browser, which keeps single-company callers working unchanged.

    Reached when the HTTP content crawl comes back bot-blocked or JS-shelled.
    Same bounds and same partial-success semantics as the HTTP version: a page
    that fails is skipped, and only a completely empty result is a failure.
    One browser is launched per company and reused for every page, so the
    per-page cost is a navigation, not a process start.
    """
    from app.services.enrichment.crawler_http import BoundedFrontier

    result = CrawlResult()
    visited: set[str] = set(visited_urls or ())
    base_host = urlparse(url).netloc

    frontier = BoundedFrontier(max_pages)
    for _page_type, seed in (seed_urls or []):
        if seed not in visited and is_crawlable_page_url(seed, base_host):
            frontier.push(seed, 1)

    async with _page_for(session, company_id) as page:
        # No inventory — re-render the homepage purely to expand the frontier.
        # Not saved: phase A already stored the homepage its extract depends on.
        if not frontier:
            try:
                status, final_url, _, body = await _fetch_page(
                    page, url, rate_limit_delay=rate_limit_delay
                )
            except Exception:  # noqa: BLE001
                result.failure_status = "http_error"
                result.failure_detail = f"Phase B (playwright) could not render {url}"
                return result
            if status >= 400 or not body:
                result.failure_status = "http_error"
                result.failure_detail = f"Phase B (playwright) homepage returned HTTP {status}"
                return result
            visited.add(url)
            visited.add(final_url)
            # Off the event loop: a blocking parse here stalls every other
            # company's browser navigation sharing this loop.
            for link in await run_in_page_executor(_links_only, body, final_url):
                if link not in visited:
                    frontier.push(link, 1)
            del body

        while frontier and len(result.pages) < max_pages:
            page_url, depth = frontier.pop()
            if page_url in visited:
                continue
            visited.add(page_url)

            try:
                status, final_url, _, body = await _fetch_page(
                    page, page_url, rate_limit_delay=rate_limit_delay
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Phase B (playwright) skipping %s for company %d",
                    page_url, company_id, exc_info=True,
                )
                continue
            if status >= 400 or not body:
                continue
            visited.add(final_url)

            want_links = depth < max_depth and len(result.pages) + 1 < max_pages
            page_result, links = await run_in_page_executor(
                _page_result_and_links,
                classify_page_type(final_url), page_url, final_url, status, body,
                company_id, url_candidate_id, want_links,
            )
            del body  # in S3 now; don't hold it across the next navigation
            result.pages.append(page_result)

            for link in links:
                if link not in visited:
                    frontier.push(link, depth + 1)

    if not result.pages:
        result.failure_status = "no_content"
        result.failure_detail = "Phase B (playwright) found no crawlable pages"
    return result


async def crawl_company_playwright(
    company_id: int,
    url: str,
    *,
    url_candidate_id: int | None = None,
    max_pages: int = 5,
    rate_limit_delay: float = 0.5,
    use_sitemap: bool = False,
    session: "BrowserSession | None" = None,
) -> CrawlResult:
    """Crawl a company website using Playwright + playwright-stealth.

    max_pages: total pages per domain (homepage counts as 1).
    rate_limit_delay: minimum seconds between requests to the same domain.
    use_sitemap: fetch robots.txt + sitemap.xml to find subpages and crawl-delay.
      Defaults to False on this tier: discover_site_overview is an *httpx* fetch
      of robots.txt and sitemap.xml, and every company that reaches Playwright is
      here precisely because httpx was bot-blocked — so it is two near-certain
      failures burned inside each company's timeout budget.
    session: batch-scoped BrowserSession. When None the function launches its own
      browser, so single-company callers keep working unchanged.
    """
    result = CrawlResult()
    max_subpages = max(0, max_pages - 1)

    # ── Site overview (robots.txt + sitemap) ──────────────────────────────
    overview = None
    effective_delay = rate_limit_delay
    if use_sitemap:
        from app.services.enrichment.crawler_sitemap import discover_site_overview
        overview = await discover_site_overview(url, user_agent=pick_user_agent(company_id))
        if overview.crawl_delay:
            effective_delay = max(rate_limit_delay, overview.crawl_delay)
        if overview.urls:
            result.inventory = classify_all_urls(overview.urls, url)

    # One shared browser per batch, a fresh context per company — see
    # BrowserSession. Launching per company was the dominant cost on this tier.
    async with _page_for(session, company_id) as page:
        try:
            # ── Homepage ──────────────────────────────────────────────────
            try:
                status, final_url, headers, body = await _fetch_page(
                    page, url, rate_limit_delay=effective_delay
                )
            except Exception as exc:
                err = str(exc)
                if "timeout" in err.lower():
                    result.failure_status = "timeout"
                    result.failure_detail = err
                else:
                    result.failure_status = "http_error"
                    result.failure_detail = err
                return result

            body_str = body.decode("utf-8", errors="replace")
            blocked, ptype = detect_bot_block(status, headers, body_str)
            if blocked:
                result.bot_blocked = True
                result.bot_protection_type = ptype
                result.failure_status = "bot_blocked"
                return result

            soup = parse_soup(body)
            # One traversal, reused for the near-empty check and the PageResult.
            homepage_text = page_text(soup)
            words = len(homepage_text.split())
            if words < 5 and len(body) < 500:
                result.failure_status = "no_content"
                result.failure_detail = f"Near-empty body after render ({len(body)} bytes)"
                return result

            result.pages.append(
                await run_in_page_executor(
                    _make_page_result, "homepage", url, final_url, status, body,
                    company_id, url_candidate_id, homepage_text,
                    metrics=False,
                )
            )

            # ── Subpages ──────────────────────────────────────────────────
            if max_subpages > 0:
                subpage_urls = find_subpage_links(soup, final_url, max_subpages=max_subpages)
                if overview and overview.urls and len(subpage_urls) < max_subpages:
                    extra = classify_urls_by_path(
                        overview.urls, final_url,
                        exclude_types=set(subpage_urls.keys()),
                        max_needed=max_subpages - len(subpage_urls),
                    )
                    for ptype, u in extra.items():
                        subpage_urls.setdefault(ptype, u)
                for page_type, sub_url in subpage_urls.items():
                    try:
                        s_status, s_final, _, s_body = await _fetch_page(
                            page, sub_url, timeout_ms=_SUBPAGE_TIMEOUT_MS,
                            rate_limit_delay=effective_delay,
                        )
                        if s_status < 400:
                            result.pages.append(
                                await run_in_page_executor(
                                    _make_page_result, page_type, sub_url, s_final, s_status, s_body,
                                    company_id, url_candidate_id,
                                    metrics=False,  # identity phase — never parsed here
                                )
                            )
                    except Exception:
                        logger.debug(
                            "Skipping subpage %s for company %d", sub_url, company_id, exc_info=True
                        )

        finally:
            # The context is closed by _page_for; the browser outlives this
            # company and is reused for the rest of the batch.
            pass

    return result
