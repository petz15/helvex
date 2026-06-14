"""HTTP-only crawler using httpx + BeautifulSoup.

Tries plain HTTP requests first. If the page appears to need JavaScript
rendering, sets CrawlResult.needs_playwright=True and returns without pages.
"""
from __future__ import annotations

import logging

import httpx

from app.services import s3_client
from app.services.crawler_common import (
    ACCEPT_LANGUAGE,
    MAX_PAGE_BYTES,
    CrawlResult,
    PageResult,
    classify_urls_by_path,
    client_hint_headers,
    count_media,
    count_words,
    detect_bot_block,
    detect_js_required,
    detect_page_language,
    find_subpage_links,
    has_contact_form,
    parse_soup,
    pick_browser_profile,
    rate_limit,
)

logger = logging.getLogger(__name__)

# Sec-Fetch-* headers signal a real top-level browser navigation.
# Their absence is a bot indicator that Cloudflare and similar services check.
_BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": ACCEPT_LANGUAGE,
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Connection": "keep-alive",
}


def _make_headers(company_id: int) -> dict[str, str]:
    profile = pick_browser_profile(company_id)
    return {
        **_BASE_HEADERS,
        "User-Agent": profile.user_agent,
        # Sec-Ch-Ua* must agree with the UA — a real Chrome always sends both.
        **client_hint_headers(profile),
    }


def _client(headers: dict[str, str], verify_ssl: bool = True) -> httpx.AsyncClient:
    # http2=True so the TLS/ALPN profile looks like a browser (HTTP/1.1-only
    # clients are a bot signal). Falls back to HTTP/1.1 transparently if the
    # server doesn't negotiate h2.
    try:
        return httpx.AsyncClient(headers=headers, max_redirects=5, verify=verify_ssl, http2=True)
    except ImportError:
        # h2 package not available in this image — degrade gracefully.
        return httpx.AsyncClient(headers=headers, max_redirects=5, verify=verify_ssl)


async def _fetch_curl_impersonate(
    company_id: int,
    url: str,
    rate_limit_delay: float,
) -> tuple[int, str, dict, bytes] | None:
    """Re-fetch via curl_cffi with a real Chrome TLS/JA3 fingerprint.

    httpx's TLS fingerprint is flagged by Cloudflare/Akamai regardless of headers.
    Used only as a fallback when the httpx fetch was bot-blocked, before escalating
    to Playwright. Returns None if curl_cffi is unavailable or the fetch fails.
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None
    profile = pick_browser_profile(company_id)
    try:
        await rate_limit(url, rate_limit_delay)
        async with AsyncSession() as s:
            resp = await s.get(
                url,
                impersonate=profile.impersonate,
                headers={"Accept-Language": ACCEPT_LANGUAGE},
                timeout=15,
                allow_redirects=True,
                verify=False,  # Swiss SME sites commonly have bad certs
            )
            body = resp.content[:MAX_PAGE_BYTES]
            return resp.status_code, str(resp.url), dict(resp.headers), body
    except Exception as exc:  # noqa: BLE001
        logger.debug("curl_cffi impersonation fetch failed for %s: %s", url, exc)
        return None


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    rate_limit_delay: float,
) -> tuple[int, str, dict, bytes]:
    """Fetch URL with rate limiting. Returns (status, final_url, headers, body).

    Uses streaming so decompression is capped at MAX_PAGE_BYTES — a gzip bomb
    (tiny compressed payload that expands to GBs) would fill memory before the
    slice if we used resp.content directly.
    """
    await rate_limit(url, rate_limit_delay)
    async with client.stream("GET", url, follow_redirects=True, timeout=12) as resp:
        # Reject before downloading if Content-Length already exceeds limit
        cl = int(resp.headers.get("content-length", 0) or 0)
        if cl > MAX_PAGE_BYTES:
            logger.debug("Skipping oversized response (%d bytes declared) for %s", cl, url)
            return resp.status_code, str(resp.url), dict(resp.headers), b""
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes(chunk_size=65_536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_PAGE_BYTES:
                logger.debug("Capped streaming response at %d bytes for %s", MAX_PAGE_BYTES, url)
                break
        body = b"".join(chunks)[:MAX_PAGE_BYTES]
    return resp.status_code, str(resp.url), dict(resp.headers), body


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


async def _fetch_with_ssl_fallback(
    url: str,
    company_id: int,
    rate_limit_delay: float,
) -> tuple[int, str, dict, bytes] | None:
    """Fetch with SSL verification; on cert error retry without verification.

    Swiss SME sites commonly have expired or self-signed certificates.
    Returns None on unrecoverable error.
    """
    headers = _make_headers(company_id)
    try:
        async with _client(headers, verify_ssl=True) as c:
            return await _fetch(c, url, rate_limit_delay)
    except httpx.ConnectError as exc:
        err = str(exc).lower()
        if "ssl" in err or "certificate" in err or "handshake" in err:
            logger.debug("SSL error for %s — retrying without verification: %s", url, exc)
            try:
                async with _client(headers, verify_ssl=False) as c:
                    return await _fetch(c, url, rate_limit_delay)
            except Exception as retry_exc:
                logger.debug("SSL fallback also failed for %s: %s", url, retry_exc)
                return None
        return None


async def crawl_company_http(
    company_id: int,
    url: str,
    *,
    url_candidate_id: int | None = None,
    max_pages: int = 5,
    rate_limit_delay: float = 0.5,
    use_sitemap: bool = True,
) -> CrawlResult:
    """Crawl a company website with plain httpx.

    max_pages: total pages per domain (homepage counts as 1).
    rate_limit_delay: minimum seconds between requests to the same domain.
    use_sitemap: fetch robots.txt + sitemap.xml to find subpages and crawl-delay.
    """
    result = CrawlResult()
    max_subpages = max(0, max_pages - 1)
    headers = _make_headers(company_id)

    # ── Site overview (robots.txt + sitemap) ──────────────────────────────
    # Finds impressum/contact pages not linked in the nav and surfaces the
    # site's crawl-delay. Best-effort — never blocks the crawl.
    overview = None
    effective_delay = rate_limit_delay
    if use_sitemap:
        from app.services.crawler_sitemap import discover_site_overview
        overview = await discover_site_overview(url, user_agent=headers["User-Agent"])
        if overview.crawl_delay:
            effective_delay = max(rate_limit_delay, overview.crawl_delay)

    async with _client(headers) as client:
        # ── Homepage ──────────────────────────────────────────────────────
        try:
            status, final_url, resp_headers, body = await _fetch(client, url, effective_delay)
        except httpx.TimeoutException:
            result.failure_status = "timeout"
            result.failure_detail = f"Timeout fetching {url}"
            return result
        except httpx.ConnectError as exc:
            err = str(exc).lower()
            if "ssl" in err or "certificate" in err or "handshake" in err:
                # SSL cert error — retry without verification
                fallback = await _fetch_with_ssl_fallback(url, company_id, effective_delay)
                if fallback is None:
                    result.failure_status = "http_error"
                    result.failure_detail = f"SSL error: {exc}"
                    return result
                status, final_url, resp_headers, body = fallback
            else:
                result.failure_status = "http_error"
                result.failure_detail = str(exc)
                return result
        except httpx.RequestError as exc:
            result.failure_status = "http_error"
            result.failure_detail = str(exc)
            return result

        body_str = body.decode("utf-8", errors="replace")
        blocked, ptype = detect_bot_block(status, resp_headers, body_str)

        # Bot-blocked or hard HTTP error → retry once with a real Chrome TLS
        # fingerprint (curl_cffi) before giving up. Defeats fingerprint-only
        # blocks that no header tweak can fix. Playwright remains the next tier.
        if blocked or status >= 400:
            curl = await _fetch_curl_impersonate(company_id, url, effective_delay)
            if curl is not None:
                status, final_url, resp_headers, body = curl
                body_str = body.decode("utf-8", errors="replace")
                blocked, ptype = detect_bot_block(status, resp_headers, body_str)

        if blocked:
            result.bot_blocked = True
            result.bot_protection_type = ptype
            result.failure_status = "bot_blocked"
            return result
        if status >= 400:
            result.failure_status = "http_error"
            result.failure_detail = f"HTTP {status}"
            return result

        soup = parse_soup(body)
        words = count_words(soup)

        if words < 5 and len(body) < 500:
            result.failure_status = "no_content"
            result.failure_detail = f"Near-empty body ({len(body)} bytes)"
            return result

        if detect_js_required(body_str, words):
            result.needs_playwright = True
            return result

        result.pages.append(
            _make_page_result("homepage", url, final_url, status, body, company_id, url_candidate_id)
        )

        if max_subpages == 0:
            return result

        # ── Subpages ──────────────────────────────────────────────────────
        # Subpage requests use same-origin Sec-Fetch-Site since we're navigating
        # within the same domain from the homepage.
        subpage_headers = {**headers, "Sec-Fetch-Site": "same-origin", "Referer": final_url}
        async with _client(subpage_headers) as sub_client:
            subpage_urls = find_subpage_links(soup, final_url, max_subpages=max_subpages)
            # Fill remaining slots from sitemap URLs — catches impressum/contact
            # pages that aren't linked in the homepage navigation.
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
                    s_status, s_final, _, s_body = await _fetch(sub_client, sub_url, effective_delay)
                    if s_status < 400:
                        result.pages.append(
                            _make_page_result(page_type, sub_url, s_final, s_status, s_body, company_id, url_candidate_id)
                        )
                except Exception:
                    logger.debug("Skipping subpage %s for company %d", sub_url, company_id, exc_info=True)

    return result
