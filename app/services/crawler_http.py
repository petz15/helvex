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
    count_media,
    count_words,
    detect_bot_block,
    detect_js_required,
    detect_page_language,
    find_subpage_links,
    has_contact_form,
    parse_soup,
    pick_user_agent,
    rate_limit,
)

logger = logging.getLogger(__name__)

# Sec-Fetch-* headers signal a real top-level browser navigation.
# Their absence is a bot indicator that Cloudflare and similar services check.
_BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": ACCEPT_LANGUAGE,
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Connection": "keep-alive",
}


def _make_headers(company_id: int) -> dict[str, str]:
    return {**_BASE_HEADERS, "User-Agent": pick_user_agent(company_id)}


def _client(headers: dict[str, str], verify_ssl: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=headers, max_redirects=5, verify=verify_ssl)


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    rate_limit_delay: float,
) -> tuple[int, str, dict, bytes]:
    """Fetch URL with rate limiting. Returns (status, final_url, headers, body)."""
    await rate_limit(url, rate_limit_delay)
    resp = await client.get(url, follow_redirects=True, timeout=12)
    # Guard against very large pages before loading into memory
    body = resp.content[:MAX_PAGE_BYTES]
    if len(resp.content) > MAX_PAGE_BYTES:
        logger.debug("Truncated response for %s: %d → %d bytes", url, len(resp.content), MAX_PAGE_BYTES)
    return resp.status_code, str(resp.url), dict(resp.headers), body


def _make_page_result(
    page_type: str,
    url: str,
    final_url: str,
    http_status: int,
    html_bytes: bytes,
    company_id: int,
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
            s3_key = s3_client.crawl_s3_key(company_id, page_type)
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
    max_pages: int = 5,
    rate_limit_delay: float = 0.5,
) -> CrawlResult:
    """Crawl a company website with plain httpx.

    max_pages: total pages per domain (homepage counts as 1).
    rate_limit_delay: minimum seconds between requests to the same domain.
    """
    result = CrawlResult()
    max_subpages = max(0, max_pages - 1)
    headers = _make_headers(company_id)

    async with _client(headers) as client:
        # ── Homepage ──────────────────────────────────────────────────────
        try:
            status, final_url, resp_headers, body = await _fetch(client, url, rate_limit_delay)
        except httpx.TimeoutException:
            result.failure_status = "timeout"
            result.failure_detail = f"Timeout fetching {url}"
            return result
        except httpx.ConnectError as exc:
            err = str(exc).lower()
            if "ssl" in err or "certificate" in err or "handshake" in err:
                # SSL cert error — retry without verification
                fallback = await _fetch_with_ssl_fallback(url, company_id, rate_limit_delay)
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

        if status >= 400:
            body_str = body.decode("utf-8", errors="replace")
            blocked, ptype = detect_bot_block(status, resp_headers, body_str)
            if blocked:
                result.bot_blocked = True
                result.bot_protection_type = ptype
                result.failure_status = "bot_blocked"
                return result
            result.failure_status = "http_error"
            result.failure_detail = f"HTTP {status}"
            return result

        body_str = body.decode("utf-8", errors="replace")
        blocked, ptype = detect_bot_block(status, resp_headers, body_str)
        if blocked:
            result.bot_blocked = True
            result.bot_protection_type = ptype
            result.failure_status = "bot_blocked"
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
            _make_page_result("homepage", url, final_url, status, body, company_id)
        )

        if max_subpages == 0:
            return result

        # ── Subpages ──────────────────────────────────────────────────────
        # Subpage requests use same-origin Sec-Fetch-Site since we're navigating
        # within the same domain from the homepage.
        subpage_headers = {**headers, "Sec-Fetch-Site": "same-origin", "Referer": final_url}
        async with _client(subpage_headers) as sub_client:
            subpage_urls = find_subpage_links(soup, final_url, max_subpages=max_subpages)
            for page_type, sub_url in subpage_urls.items():
                try:
                    s_status, s_final, _, s_body = await _fetch(sub_client, sub_url, rate_limit_delay)
                    if s_status < 400:
                        result.pages.append(
                            _make_page_result(page_type, sub_url, s_final, s_status, s_body, company_id)
                        )
                except Exception:
                    logger.debug("Skipping subpage %s for company %d", sub_url, company_id, exc_info=True)

    return result
