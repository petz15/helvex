"""HTTP-only crawler using httpx + BeautifulSoup.

Tries plain HTTP requests first. If the page appears to need JavaScript
rendering, sets CrawlResult.needs_playwright=True and returns without pages.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import httpx

from app.services.platform import s3_client
from app.services.enrichment.crawler_common import (
    ACCEPT_LANGUAGE,
    BOUNDED_ACCEPT_ENCODING,
    MAX_PAGE_BYTES,
    MAX_RAW_BYTES,
    CrawlResult,
    DecompressionBombError,
    PageResult,
    classify_all_urls,
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
    read_bounded_body,
    resolve_is_public,
    ssrf_request_guard as _ssrf_request_guard,
)

logger = logging.getLogger(__name__)

# Sec-Fetch-* headers signal a real top-level browser navigation.
# Their absence is a bot indicator that Cloudflare and similar services check.
# Accept-Encoding is intentionally narrower than a real browser's (no br/zstd) —
# see BOUNDED_ACCEPT_ENCODING: those codecs' Python bindings can't be bounded
# per-call, so we don't invite them.
_BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": ACCEPT_LANGUAGE,
    "Accept-Encoding": BOUNDED_ACCEPT_ENCODING,
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
    # server doesn't negotiate h2. The request event hook enforces the SSRF guard
    # on every request including redirect hops.
    hooks = {"request": [_ssrf_request_guard]}
    try:
        return httpx.AsyncClient(headers=headers, max_redirects=5, verify=verify_ssl, http2=True, event_hooks=hooks)
    except ImportError:
        # h2 package not available in this image — degrade gracefully.
        return httpx.AsyncClient(headers=headers, max_redirects=5, verify=verify_ssl, event_hooks=hooks)


_CURL_MAX_REDIRECTS = 5


async def _fetch_curl_impersonate(
    company_id: int,
    url: str,
    rate_limit_delay: float,
) -> tuple[int, str, dict, bytes] | None:
    """Re-fetch via curl_cffi with a real Chrome TLS/JA3 fingerprint.

    httpx's TLS fingerprint is flagged by Cloudflare/Akamai regardless of headers.
    Used only as a fallback when the httpx fetch was bot-blocked, before escalating
    to Playwright. Returns None if curl_cffi is unavailable or the fetch fails.

    Security notes (this path has no equivalent to httpx's per-hop event hook,
    since curl_cffi/libcurl follows redirects internally in C code with no
    interception point):
      - allow_redirects=False here; we resolve+SSRF-check and re-issue each hop
        ourselves, so a redirect to an internal address is refused just like
        the primary httpx fetch (crawler_http._ssrf_request_guard).
      - accept_encoding drops br (libcurl decodes it transparently in C with no
        way for us to bound a single decompress() call's output — the
        BOUNDED_ACCEPT_ENCODING codecs (gzip/deflate) are only genuinely bounded
        on the primary httpx path; here we additionally cap cumulative bytes
        read, which is a weaker but still real mitigation for this fallback tier.
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None
    profile = pick_browser_profile(company_id)
    next_url = url
    try:
        async with AsyncSession() as s:
            for _ in range(_CURL_MAX_REDIRECTS):
                host = urlparse(next_url).hostname
                if not host or not await resolve_is_public(host):
                    logger.warning("crawler.ssrf_blocked(curl_cffi) host=%s url=%s", host, next_url[:120])
                    return None

                await rate_limit(next_url, rate_limit_delay)
                resp = await s.get(
                    next_url,
                    impersonate=profile.impersonate,
                    headers={"Accept-Language": ACCEPT_LANGUAGE},
                    accept_encoding=BOUNDED_ACCEPT_ENCODING,
                    timeout=15,
                    allow_redirects=False,
                    verify=False,  # Swiss SME sites commonly have bad certs
                    stream=True,
                )

                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    await resp.aclose()
                    if not location:
                        return None
                    next_url = urljoin(next_url, location)
                    continue

                cl = int(resp.headers.get("content-length", 0) or 0)
                if cl > MAX_RAW_BYTES:
                    await resp.aclose()
                    return resp.status_code, str(resp.url), dict(resp.headers), b""

                body = bytearray()
                async for chunk in resp.aiter_content():
                    body.extend(chunk)
                    if len(body) >= MAX_PAGE_BYTES:
                        break
                final_url, status, headers = str(resp.url), resp.status_code, dict(resp.headers)
                await resp.aclose()
                return status, final_url, headers, bytes(body[:MAX_PAGE_BYTES])
            return None  # too many redirects
    except Exception as exc:  # noqa: BLE001
        logger.debug("curl_cffi impersonation fetch failed for %s: %s", url, exc)
        return None


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    rate_limit_delay: float,
) -> tuple[int, str, dict, bytes]:
    """Fetch URL with rate limiting. Returns (status, final_url, headers, body).

    Decodes the body ourselves via read_bounded_body rather than trusting
    httpx's automatic decoder, which has no output-size bound and can be
    forced to materialize gigabytes from a small crafted response (a
    decompression / "zip" bomb) before any size check here would run.
    """
    await rate_limit(url, rate_limit_delay)
    async with client.stream("GET", url, follow_redirects=True, timeout=12) as resp:
        # Reject before downloading if Content-Length (raw, on-the-wire size)
        # already exceeds the raw cap — cheap early-out for huge declared bodies.
        cl = int(resp.headers.get("content-length", 0) or 0)
        if cl > MAX_RAW_BYTES:
            logger.debug("Skipping oversized response (%d bytes declared) for %s", cl, url)
            return resp.status_code, str(resp.url), dict(resp.headers), b""
        try:
            body = await read_bounded_body(resp)
        except DecompressionBombError as exc:
            logger.warning("crawler.bounded_decode_refused url=%s reason=%s", url, exc)
            body = b""
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
        from app.services.enrichment.crawler_sitemap import discover_site_overview
        overview = await discover_site_overview(url, user_agent=headers["User-Agent"])
        if overview.crawl_delay:
            effective_delay = max(rate_limit_delay, overview.crawl_delay)
        if overview.urls:
            # Full site inventory — independent of crawl success/failure below,
            # since it only needs the sitemap, not a successful fetch.
            result.inventory = classify_all_urls(overview.urls, url)

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
