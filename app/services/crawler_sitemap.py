"""Lightweight robots.txt + sitemap.xml discovery for the crawlers.

Builds a "site overview" before page selection so subpages (impressum, contact,
…) can be found from the sitemap even when they aren't linked in the homepage
nav. Also surfaces the robots Crawl-delay so the crawler can be polite.

Best-effort and bounded: short timeout, one level of sitemap-index recursion,
hard URL cap. Never raises — returns an empty overview on any failure.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_SITEMAP_DIRECTIVE_RE = re.compile(r"^\s*sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_CRAWL_DELAY_RE = re.compile(r"^\s*crawl-delay:\s*([0-9.]+)", re.IGNORECASE | re.MULTILINE)

_MAX_URLS = 300
_MAX_SITEMAPS = 5  # cap how many sitemap files we fetch per domain


@dataclass
class SiteOverview:
    urls: list[str] = field(default_factory=list)
    crawl_delay: float | None = None


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


async def _get_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=8)
        if resp.status_code >= 400:
            return None
        # Cap body to avoid pathological sitemaps eating memory.
        return resp.text[:2_000_000]
    except Exception:  # noqa: BLE001
        return None


async def discover_site_overview(
    base_url: str,
    *,
    user_agent: str,
    max_urls: int = _MAX_URLS,
) -> SiteOverview:
    """Fetch robots.txt + sitemap(s) and return discovered URLs + crawl-delay.

    Always returns a SiteOverview (possibly empty); never raises.
    """
    overview = SiteOverview()
    origin = _origin(base_url)
    if not origin.startswith("http"):
        return overview

    headers = {"User-Agent": user_agent, "Accept": "text/plain,application/xml,*/*"}
    sitemap_urls: list[str] = []

    try:
        async with httpx.AsyncClient(headers=headers, verify=False) as client:
            # ── robots.txt ────────────────────────────────────────────────
            robots = await _get_text(client, urljoin(origin + "/", "robots.txt"))
            if robots:
                sitemap_urls.extend(m.strip() for m in _SITEMAP_DIRECTIVE_RE.findall(robots))
                cd = _CRAWL_DELAY_RE.search(robots)
                if cd:
                    try:
                        overview.crawl_delay = min(float(cd.group(1)), 10.0)
                    except ValueError:
                        pass

            # Fall back to the conventional locations if robots declared none.
            if not sitemap_urls:
                sitemap_urls = [
                    urljoin(origin + "/", "sitemap.xml"),
                    urljoin(origin + "/", "sitemap_index.xml"),
                ]

            # ── sitemaps (one level of index recursion) ───────────────────
            seen: set[str] = set()
            fetched = 0
            queue = list(dict.fromkeys(sitemap_urls))
            collected: list[str] = []

            while queue and fetched < _MAX_SITEMAPS and len(collected) < max_urls:
                sm = queue.pop(0)
                if sm in seen:
                    continue
                seen.add(sm)
                xml = await _get_text(client, sm)
                fetched += 1
                if not xml:
                    continue
                locs = _LOC_RE.findall(xml)
                if "<sitemapindex" in xml.lower():
                    # Index file — its <loc>s are nested sitemaps, recurse once.
                    for child in locs:
                        if child not in seen and len(queue) < _MAX_SITEMAPS:
                            queue.append(child)
                else:
                    collected.extend(locs)

            # De-dup, keep same-origin only, cap.
            host = urlparse(base_url).netloc
            out: list[str] = []
            seen_u: set[str] = set()
            for u in collected:
                if urlparse(u).netloc != host:
                    continue
                if u in seen_u:
                    continue
                seen_u.add(u)
                out.append(u)
                if len(out) >= max_urls:
                    break
            overview.urls = out
    except Exception:  # noqa: BLE001
        logger.debug("Sitemap discovery failed for %s", base_url, exc_info=True)

    return overview
