"""Playwright-based crawler for JS-heavy and bot-protected sites.

Uses Chromium in headless mode with playwright-stealth patches to minimise
bot-detection signals. Same CrawlResult contract as crawler_http.
"""
from __future__ import annotations

import logging

import os

from app.services import s3_client
from app.services.crawler_common import (
    ACCEPT_LANGUAGE,
    MAX_PAGE_BYTES,
    CrawlResult,
    PageResult,
    classify_urls_by_path,
    count_media,
    count_words,
    detect_bot_block,
    detect_page_language,
    find_subpage_links,
    has_contact_form,
    parse_soup,
    pick_user_agent,
    rate_limit,
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


async def _block_heavy_resources(route) -> None:
    try:
        if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
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


async def _fetch_page(
    page,
    url: str,
    timeout_ms: int = 20_000,
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
    html_str = await page.content()
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
) -> PageResult:
    html_str = html_bytes.decode("utf-8", errors="replace")
    soup = parse_soup(html_bytes)
    images, videos = count_media(soup)
    words = count_words(soup)
    lang = detect_page_language(soup)
    contact_form = has_contact_form(html_str)

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
        html=html_bytes,
        lang=lang,
        word_count=words,
        image_count=images,
        video_count=videos,
        has_contact_form=contact_form,
        s3_key_html=s3_key,
    )


async def crawl_company_playwright(
    company_id: int,
    url: str,
    *,
    url_candidate_id: int | None = None,
    max_pages: int = 5,
    rate_limit_delay: float = 0.5,
    use_sitemap: bool = True,
) -> CrawlResult:
    """Crawl a company website using Playwright + playwright-stealth.

    max_pages: total pages per domain (homepage counts as 1).
    rate_limit_delay: minimum seconds between requests to the same domain.
    use_sitemap: fetch robots.txt + sitemap.xml to find subpages and crawl-delay.
    Imports Playwright lazily so the module can be imported on pods without it.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed in this image. "
            "Add playwright/playwright-stealth to requirements.ml.txt."
        ) from exc

    result = CrawlResult()
    max_subpages = max(0, max_pages - 1)

    # ── Site overview (robots.txt + sitemap) ──────────────────────────────
    overview = None
    effective_delay = rate_limit_delay
    if use_sitemap:
        from app.services.crawler_sitemap import discover_site_overview
        overview = await discover_site_overview(url, user_agent=pick_user_agent(company_id))
        if overview.crawl_delay:
            effective_delay = max(rate_limit_delay, overview.crawl_delay)

    # Stealth().use_async() wraps async_playwright and auto-applies stealth
    # patches to every new page/context before any navigation — replaces the
    # old stealth_async(page) call that was removed in playwright-stealth 2.x.
    async with Stealth().use_async(async_playwright()) as pw:
        launch_kwargs: dict = {"headless": True, "args": _LAUNCH_ARGS}
        if _CHANNEL:
            launch_kwargs["channel"] = _CHANNEL
        try:
            browser = await pw.chromium.launch(**launch_kwargs)
        except Exception:
            # Configured channel (e.g. "chrome") not installed in this image —
            # fall back to bundled Chromium.
            browser = await pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
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
        # Drop images/fonts/media — we only need DOM + text.
        await context.route("**/*", _block_heavy_resources)
        page = await context.new_page()

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
            words = count_words(soup)
            if words < 5 and len(body) < 500:
                result.failure_status = "no_content"
                result.failure_detail = f"Near-empty body after render ({len(body)} bytes)"
                return result

            result.pages.append(
                _make_page_result("homepage", url, final_url, status, body, company_id, url_candidate_id)
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
                            page, sub_url, rate_limit_delay=effective_delay
                        )
                        if s_status < 400:
                            result.pages.append(
                                _make_page_result(
                                    page_type, sub_url, s_final, s_status, s_body, company_id, url_candidate_id
                                )
                            )
                    except Exception:
                        logger.debug(
                            "Skipping subpage %s for company %d", sub_url, company_id, exc_info=True
                        )

        finally:
            await browser.close()

    return result
