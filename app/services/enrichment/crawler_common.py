"""Shared utilities for HTTP and Playwright crawlers."""
from __future__ import annotations

import asyncio
import functools
import ipaddress
import logging
import os
import re
import socket
import time
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Off-loop page processing ──────────────────────────────────────────────────
#
# Turning a fetched page into a PageResult is blocking work: an lxml parse plus
# media/word counting and language detection (CPU-bound), followed by a blocking
# boto3 S3 PUT. Doing that inline on the crawl coroutine stalls the event loop,
# and with it EVERY other company being crawled concurrently on the same loop —
# which is why raising `crawl_concurrency` on its own never moved throughput.
#
# Hand that work to a thread pool instead so the loop stays free to keep sockets
# in flight. Sized independently of crawl_concurrency: it bounds how much CPU +
# S3 work is in flight, while the semaphore bounds how many sites are open.
#
# It is also the hard bound on PEAK page-processing memory: each worker holds one
# document's bytes plus its lxml/BeautifulSoup tree (a tree runs ~5-10x the source
# size), so peak ~= PAGE_WORKERS * MAX_PAGE_BYTES * 10. At the old 32 x 5 MB that
# is >1.5 GB against a 1 Gi pod limit. The crawler pods are also capped at 1 CPU,
# so 32 parse threads never ran in parallel anyway — they just queued, each
# pinning a tree. Raise via CRAWL_PAGE_WORKERS only alongside the pod memory limit.
PAGE_WORKERS: int = int(os.getenv("CRAWL_PAGE_WORKERS", "12"))

_page_executor = ThreadPoolExecutor(max_workers=PAGE_WORKERS, thread_name_prefix="crawl-page")


async def run_in_page_executor(fn, *args, **kwargs):
    """Run a blocking page-processing function off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_page_executor, functools.partial(fn, *args, **kwargs))

# ── Subpage keyword sets (DE / FR / IT / EN) ──────────────────────────────────

_IMPRESSUM_KEYWORDS = frozenset([
    # DE
    "impressum", "rechtliche hinweise", "herausgeber",
    # FR
    "mentions légales", "mentions legales", "informations légales", "information légale",
    # IT
    "note legali", "informazioni legali",
    # EN
    "imprint", "legal notice", "legal information",
])

_PRIVACY_KEYWORDS = frozenset([
    # DE
    "datenschutz", "datenschutzerklärung", "datenschutzerklaerung", "datenschutzhinweise",
    # FR
    "politique de confidentialité", "politique de confidentialite",
    "politique de vie privée", "données personnelles", "donnees personnelles",
    # IT
    "informativa sulla privacy", "informativa privacy",
    "dichiarazione sulla privacy", "politica sulla privacy",
    # EN
    "privacy", "privacy policy", "privacy notice", "data privacy",
])

_CONTACT_KEYWORDS = frozenset([
    # DE
    "kontakt", "kontaktieren", "kontaktformular", "so erreichen sie uns", "anfahrt",
    # FR
    "contact", "contactez-nous", "nous contacter", "formulaire de contact",
    # IT
    "contatto", "contatti", "contattaci", "modulo di contatto",
    # EN
    "get in touch", "reach us", "write us",
])

_ABOUT_KEYWORDS = frozenset([
    # DE
    "über uns", "ueber uns", "über mich", "wer wir sind", "unternehmen",
    # FR
    "à propos", "a propos", "qui sommes", "qui nous sommes", "notre entreprise",
    # IT
    "chi siamo", "su di noi", "la nostra azienda",
    # EN
    "about us", "about", "company", "portrait", "our team",
])

_SERVICES_KEYWORDS = frozenset([
    # DE
    "leistungen", "dienstleistungen", "angebot", "was wir tun",
    "lösungen", "losungen", "unsere leistungen",
    # FR
    "services", "nos services", "prestations", "offres", "solutions",
    # IT
    "servizi", "prestazioni", "cosa facciamo",
    # EN
    "what we do",
])

_TEAM_KEYWORDS = frozenset([
    # DE
    "team", "mitarbeiter", "unser team", "geschäftsleitung", "geschaeftsleitung",
    "vorstand", "ansprechpartner",
    # FR
    "équipe", "equipe", "notre équipe", "notre equipe", "collaborateurs",
    # IT
    "il nostro team", "collaboratori", "management",
    # EN
    "our team", "people", "leadership", "meet the team",
])

_PRODUCTS_KEYWORDS = frozenset([
    # DE
    "produkte", "sortiment", "shop",
    # FR
    "produits", "boutique",
    # IT
    "prodotti", "negozio",
    # EN
    "products", "product", "catalog", "catalogue",
])

_REFERENCES_KEYWORDS = frozenset([
    # DE
    "referenzen", "projekte", "kunden", "fallstudien",
    # FR
    "références", "references", "projets", "clients",
    # IT
    "referenze", "progetti", "clienti",
    # EN
    "references", "portfolio", "case studies", "our clients", "projects",
])

_NEWS_KEYWORDS = frozenset([
    # DE
    "aktuelles", "neuigkeiten", "medien", "presse",
    # FR
    "actualités", "actualites", "médias", "medias", "presse",
    # IT
    "notizie", "novità", "novita", "media", "stampa",
    # EN
    "news", "blog", "press",
])

_JOBS_KEYWORDS = frozenset([
    # DE
    "karriere", "stellen", "offene stellen", "jobs",
    # FR
    "carrière", "carriere", "emplois", "offres d'emploi",
    # IT
    "carriera", "lavora con noi", "posizioni aperte",
    # EN
    "jobs", "careers", "join us", "open positions",
])

# Priority order — determines fetch sequence when max_pages limits total pages,
# and classification order for the full sitemap page inventory (§Layer A).
# impressum and privacy first because they carry legal/address/contact data.
_SUBPAGE_PRIORITY: list[tuple[str, frozenset[str]]] = [
    ("impressum",  _IMPRESSUM_KEYWORDS),
    ("privacy",    _PRIVACY_KEYWORDS),
    ("contact",    _CONTACT_KEYWORDS),
    ("about",      _ABOUT_KEYWORDS),
    ("team",       _TEAM_KEYWORDS),
    ("services",   _SERVICES_KEYWORDS),
    ("products",   _PRODUCTS_KEYWORDS),
    ("references", _REFERENCES_KEYWORDS),
    ("news",       _NEWS_KEYWORDS),
    ("jobs",       _JOBS_KEYWORDS),
]

# Types worth spending a crawl budget slot on (text-rich, identity/content signal).
# news/jobs/products/references are inventoried but not fetched by default —
# low signal-per-byte for identity/NOGA/AI use, high volume risk (e.g. paginated
# job boards or catalogs). Fetch them later on demand from the profile UI.
#
# `privacy` is deliberately NOT here despite sitting at priority 2: it is absent
# from crawler_extract._TEXT_PAGES, so its main text was never extracted — it
# only ever contributed the unconditional email/phone/social/UID regexes, all of
# which impressum already supplies. It was consuming a budget slot ahead of
# contact/about and returning almost nothing. Still inventoried, just not fetched.
_FETCH_WORTHY_TYPES = frozenset({
    "impressum", "contact", "about", "team", "services",
})

# Phase A (identity) budget: the confidence ladder in resolve_company_extract
# reads UID from impressum/contact, address from JSON-LD/<address>/impressum
# text, and name from domain+title. Nothing in it reads about/team/services, so
# these are the only types worth fetching before identity is settled.
IDENTITY_PAGE_TYPES = frozenset({"impressum", "contact"})

# Phase B (content) — everything else is fair game; see crawl_site_full.
CONTENT_PAGE_TYPES = frozenset(t for t, _ in _SUBPAGE_PRIORITY) - IDENTITY_PAGE_TYPES

# Fetch-selection uses only the fetch-worthy subset (still priority-ordered) so
# raising the crawl budget later never starts spending it on job/news/product
# listings. classify_all_urls (inventory) uses the full _SUBPAGE_PRIORITY instead.
_FETCH_PRIORITY: list[tuple[str, frozenset[str]]] = [
    (t, kw) for t, kw in _SUBPAGE_PRIORITY if t in _FETCH_WORTHY_TYPES
]

# ── Browser profile pool (UA + matching client hints) ──────────────────────────
# Deterministic selection per company_id so the UA + Sec-Ch-Ua headers stay
# consistent across the homepage + subpages of a single crawl session.
#
# Each entry pairs a User-Agent with the Client-Hint headers a real instance of
# that browser sends. Sending a Chrome UA *without* Sec-Ch-Ua headers (the old
# behaviour) is itself a bot tell that Cloudflare/Akamai flag — couple them so
# they can never disagree. Firefox/Safari send no Sec-Ch-Ua (sec_ch_ua=None).

@dataclass(frozen=True)
class BrowserProfile:
    user_agent: str
    sec_ch_ua: str | None       # None for Firefox/Safari (they don't send it)
    platform: str               # Sec-Ch-Ua-Platform value (quoted)
    impersonate: str            # curl_cffi impersonation target


_PROFILE_POOL: list[BrowserProfile] = [
    BrowserProfile(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"', '"Windows"', "chrome131",
    ),
    BrowserProfile(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"', '"macOS"', "chrome131",
    ),
    BrowserProfile(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        None, '"Windows"', "firefox133",
    ),
    BrowserProfile(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"', '"Linux"', "chrome131",
    ),
    BrowserProfile(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        None, '"macOS"', "safari17_0",
    ),
]


def pick_browser_profile(company_id: int) -> BrowserProfile:
    """Deterministic browser profile for this company (UA + matching client hints)."""
    return _PROFILE_POOL[company_id % len(_PROFILE_POOL)]


def pick_user_agent(company_id: int) -> str:
    """Return a deterministic UA for this company so all its pages share the same UA."""
    return _PROFILE_POOL[company_id % len(_PROFILE_POOL)].user_agent


def client_hint_headers(profile: BrowserProfile) -> dict[str, str]:
    """Sec-Ch-Ua* headers consistent with the profile. Empty for non-Chromium UAs."""
    if not profile.sec_ch_ua:
        return {}
    return {
        "Sec-Ch-Ua": profile.sec_ch_ua,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": profile.platform,
    }


# ── Accept-Language header (shared by HTTP and Playwright) ─────────────────────

ACCEPT_LANGUAGE = "de-CH,de;q=0.9,fr;q=0.8,it;q=0.7,en;q=0.6"

# ── Misc patterns ──────────────────────────────────────────────────────────────

_CONTACT_FORM_PATTERNS = re.compile(
    r'<(form|input)[^>]*(contact|kontakt|message|nachricht|name|email)',
    re.IGNORECASE,
)
# Byte-level twin of the above, so has_contact_form can run straight off the
# response body without first materialising a full str copy of the document
# (that copy is up to 4x the byte size for non-ASCII pages, and every
# CRAWL_PAGE_WORKERS thread was holding one at once).
_CONTACT_FORM_PATTERNS_BYTES = re.compile(
    rb'<(form|input)[^>]*(contact|kontakt|message|nachricht|name|email)',
    re.IGNORECASE,
)

_CF_MARKERS = frozenset(["cf-ray", "cf-cache-status", "__cf_bm", "cf_clearance"])
_CF_BODY_PATTERNS = re.compile(
    r"(cloudflare|checking your browser|ddos protection|please wait|just a moment)",
    re.IGNORECASE,
)

_JS_APP_ROOT = re.compile(
    r'<(div|section)\s+id=["\']?(app|root|__nuxt|__next|ng-app)["\']?\s*/?>',
    re.IGNORECASE,
)

# Pages larger than this are truncated before parsing/storing (prevents OOM).
# 2 MB of markup is already far beyond any real Swiss SME page (typical: <300 KB)
# and nothing downstream reads past the identity/contact blocks. Kept low because
# this multiplies by PAGE_WORKERS and by the soup tree's ~10x expansion factor.
MAX_PAGE_BYTES: int = 2 * 1024 * 1024  # 2 MB

# Hard cap on RAW (as-received-on-the-wire, possibly compressed) bytes read for
# a single response — independent of MAX_PAGE_BYTES, which bounds the DECODED
# size. Rejects pathologically large or slow transfers outright, regardless of
# what they'd decompress to.
MAX_RAW_BYTES: int = MAX_PAGE_BYTES * 20  # 100 MB

# Raw bytes are fed to the decompressor in small increments so a single
# malicious chunk can't force one huge decompress() call before we get a
# chance to check the running total (see read_bounded_body).
_RAW_READ_CHUNK: int = 8_192

# Only encodings whose Python binding exposes a hard per-call OUTPUT size bound
# (stdlib zlib's `max_length`) are accepted for the bounded-decode path — that
# bound is what actually defeats a decompression ("zip") bomb, where a tiny
# compressed body is crafted to expand to gigabytes. Real browsers also offer
# br/zstd, but neither of the installed Python bindings (brotli, zstandard)
# exposes a per-call output cap, so we deliberately don't advertise them in
# BOUNDED_ACCEPT_ENCODING — a minor bot-fingerprint trade for airtight bounding.
# A server that sends br/zstd anyway (ignoring our Accept-Encoding) is refused
# (fail closed) rather than decoded unbounded.
BOUNDED_ACCEPT_ENCODING: str = "gzip, deflate"


class DecompressionBombError(Exception):
    """Raised when a response's Content-Encoding can't be safely output-bounded."""


def _make_bounded_decoder(content_encoding: str):
    """Return an incremental zlib decompressor, or None for identity (no decoding).

    Raises DecompressionBombError for any encoding we can't safely bound —
    callers must treat that as a refused fetch, not fall back to unbounded decode.
    """
    enc = (content_encoding or "").split(";")[0].strip().lower()
    if enc in ("", "identity"):
        return None
    if enc in ("gzip", "x-gzip"):
        return zlib.decompressobj(zlib.MAX_WBITS | 16)
    if enc == "deflate":
        return zlib.decompressobj()
    raise DecompressionBombError(f"refusing unbounded Content-Encoding: {enc!r}")


async def read_bounded_body(
    resp: Any,
    *,
    cap: int = MAX_PAGE_BYTES,
    max_raw: int = MAX_RAW_BYTES,
) -> bytes:
    """Read an httpx streaming Response's body, decoding it ourselves with the
    decoded output hard-capped at `cap` bytes — regardless of the server's
    declared compression ratio. This is the actual zip-bomb defense: httpx's
    own automatic decoder (used by aiter_bytes) calls decompress() with no
    output-size bound, so a single crafted chunk can materialize gigabytes in
    memory before any of our size checks run. Reading via aiter_raw (undecoded)
    and decompressing ourselves in small increments with zlib's max_length lets
    every single decompress() call be capped, so a decompression bomb can never
    produce more than `cap` bytes in memory no matter the compression ratio.

    Truncates (does not raise) on a merely oversized body or malformed/partial
    compressed data. Raises DecompressionBombError only when the encoding
    itself has no safe bound (see _make_bounded_decoder) — callers should treat
    that the same as a fetch failure.
    """
    content_encoding = resp.headers.get("content-encoding", "")
    decoder = _make_bounded_decoder(content_encoding)  # may raise DecompressionBombError

    out = bytearray()
    raw_total = 0
    retried_raw_deflate = False
    async for raw_chunk in resp.aiter_raw(chunk_size=_RAW_READ_CHUNK):
        raw_total += len(raw_chunk)
        if raw_total > max_raw:
            logger.debug("Raw body exceeded %d bytes — aborting read", max_raw)
            break
        if decoder is None:
            piece = raw_chunk
        else:
            remaining = cap - len(out)
            if remaining <= 0:
                break
            try:
                piece = decoder.decompress(raw_chunk, remaining)
            except zlib.error:
                # Some servers send raw deflate (no zlib header) despite
                # Content-Encoding: deflate technically meaning zlib-wrapped.
                if content_encoding.strip().lower() == "deflate" and not retried_raw_deflate:
                    retried_raw_deflate = True
                    decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                    try:
                        piece = decoder.decompress(raw_chunk, remaining)
                    except zlib.error:
                        logger.debug("Malformed deflate body — truncating")
                        break
                else:
                    logger.debug("Malformed compressed body (%s) — truncating", content_encoding)
                    break
        out.extend(piece)
        if len(out) >= cap:
            break
    return bytes(out[:cap])


def _ip_blocked(ip_str: str) -> bool:
    """True if an address must not be crawled (internal / metadata / non-routable)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → block
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local  # 169.254.169.254 cloud metadata is link-local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


async def resolve_is_public(host: str) -> bool:
    """SSRF guard core: resolve `host` and return False if any address it
    resolves to is internal/private/metadata/reserved. Shared by the httpx
    per-request hook (ssrf_request_guard) and any fetch path that can't use
    httpx's event hooks (e.g. curl_cffi, which follows redirects inside libcurl
    with no per-hop interception point of its own).
    """
    if not host:
        return False
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(None, socket.getaddrinfo, host, None)
    except OSError:
        return False
    return not any(_ip_blocked(info[4][0]) for info in infos)


async def ssrf_request_guard(request: Any) -> None:
    """SSRF guard — blocks crawler requests (and redirect hops, since httpx runs
    request hooks per hop) to non-public addresses. Every crawler fetch — page
    content, robots.txt/sitemap.xml discovery — follows URLs harvested from
    external sources and redirects, so without this a crawled/redirected URL
    pointing at localhost / an internal service / the cloud metadata endpoint
    (169.254.169.254) would be fetched server-side. Lives here (not in
    crawler_http) so crawler_sitemap can use it too without a circular import
    (crawler_http lazily imports crawler_sitemap inside a function body).

    Pass as an httpx event hook: event_hooks={"request": [ssrf_request_guard]}.
    """
    import httpx

    url = request.url
    if url.scheme not in ("http", "https"):
        raise httpx.RequestError(f"SSRF guard: blocked scheme {url.scheme!r}", request=request)
    host = url.host
    if not host or not await resolve_is_public(host):
        logger.warning("crawler.ssrf_blocked host=%s url=%s", host, str(url)[:120])
        raise httpx.RequestError(f"SSRF guard: {host!r} resolves to a non-public address", request=request)


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class PageResult:
    """Metadata about one crawled page. Deliberately does NOT carry the HTML.

    The HTML lives in S3 (`s3_key_html`) and is re-downloaded by web_extract.
    Keeping the bytes here as well used to pin every page of every concurrently
    crawled site in RAM for the whole batch — phase B (10 sites x 60 pages) held
    600 full documents at once, which is what OOM-killed the 1Gi crawler pod.
    Nothing ever read the field.

    lang/word_count/image_count/video_count/has_contact_form are **descriptive
    only** — they land in company_web_pages and are surfaced in the company
    detail Website panel, and nothing scores, filters or decides on them. They
    are therefore None on phase-A (identity) pages, which skip computing them
    entirely; see _make_page_result's `metrics` flag.
    """
    page_type: str
    url: str
    final_url: str
    http_status: int
    lang: str | None
    word_count: int | None
    image_count: int | None
    video_count: int | None
    has_contact_form: bool | None
    s3_key_html: str | None = None
    bot_blocked: bool = False


@dataclass
class CrawlResult:
    needs_playwright: bool = False
    bot_blocked: bool = False
    bot_protection_type: str | None = None
    failure_status: str | None = None
    failure_detail: str | None = None
    pages: list[PageResult] = field(default_factory=list)
    # Full site inventory (page_type, url) from sitemap discovery — includes pages
    # that were NOT fetched (beyond the crawl budget). See classify_all_urls.
    inventory: list[tuple[str, str]] = field(default_factory=list)


# ── Per-domain rate limiter ────────────────────────────────────────────────────
# Module-level dict persists across asyncio.run() calls within the same process.
# Safe under the job worker's concurrent crawls too: asyncio.gather runs all
# tasks on one thread's event loop, and the read-check-write below has no
# `await` in between, so concurrent companies (crawled as separate tasks)
# can't race each other here — only truly parallel (multi-process/thread)
# callers would need a real lock.

_domain_last_access: "OrderedDict[str, float]" = OrderedDict()

# The dict is process-lifetime, and the crawler walks ~700k distinct domains, so
# an unbounded dict grows forever inside a long-lived pod. Only the most recent
# domains can still be inside their delay window, so an LRU cap loses nothing.
_MAX_TRACKED_DOMAINS = 20_000


async def rate_limit(url: str, delay: float) -> None:
    """Enforce a minimum delay between requests to the same domain.

    delay=0 disables rate limiting.
    """
    if delay <= 0:
        return
    domain = urlparse(url).netloc.lower()
    since = time.monotonic() - _domain_last_access.get(domain, 0.0)
    if since < delay:
        await asyncio.sleep(delay - since)
    _domain_last_access[domain] = time.monotonic()
    _domain_last_access.move_to_end(domain)
    while len(_domain_last_access) > _MAX_TRACKED_DOMAINS:
        _domain_last_access.popitem(last=False)


# ── HTML utilities ─────────────────────────────────────────────────────────────

def parse_soup(html: bytes | str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def page_text(soup: BeautifulSoup) -> str:
    """The page's visible text — ONE full tree traversal.

    `soup.get_text()` is pure-Python tree walking and is the single most
    expensive thing done per page after the parse itself. Callers that need both
    a word count and the text (i.e. everyone) must traverse once and share the
    result rather than calling count_words + detect_page_language back to back.
    """
    return soup.get_text(separator=" ", strip=True)


def detect_page_language(soup: BeautifulSoup, text: str | None = None) -> str | None:
    """Language of the page. Pass `text` (from page_text) to avoid re-traversing.

    Without it this walks the whole tree a second time purely to take the first
    2000 characters — the metadata paths below usually return first, but on the
    many Swiss SME sites with no lang= attribute the fallback always fires.
    """
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        return str(html_tag["lang"])[:16]
    meta = soup.find("meta", attrs={"http-equiv": re.compile("content-language", re.I)})
    if meta and meta.get("content"):
        return str(meta["content"])[:16]
    # Content-based fallback when HTML metadata is absent (many Swiss SME sites don't set lang=).
    # Delegates to the shared lingua detector already used by the NOGA pipeline.
    try:
        from app.services.ml.language_detection import detect_purpose_language
        body = (text if text is not None else page_text(soup))[:2000]
        return detect_purpose_language(body)
    except Exception:
        return None


def count_media(soup: BeautifulSoup) -> tuple[int, int]:
    images = len(soup.find_all("img"))
    videos = len(soup.find_all("video")) + len(
        soup.find_all("iframe", src=re.compile(r"(youtube|vimeo|youtu\.be)", re.I))
    )
    return images, videos


def count_words(soup: BeautifulSoup) -> int:
    return len(soup.get_text(separator=" ", strip=True).split())


def has_contact_form(html: str | bytes) -> bool:
    """Detect a contact form. Accepts raw bytes to avoid a full str copy."""
    if isinstance(html, (bytes, bytearray)):
        return bool(_CONTACT_FORM_PATTERNS_BYTES.search(html))
    return bool(_CONTACT_FORM_PATTERNS.search(html))


def find_subpage_links(
    soup: BeautifulSoup,
    base_url: str,
    max_subpages: int = 4,
) -> dict[str, str]:
    """Return priority-ordered subpage URLs from nav/footer links.

    Returns at most max_subpages entries in _FETCH_PRIORITY order (only the
    fetch-worthy types — see _FETCH_WORTHY_TYPES).
    Fragment-only links, cross-domain links, and duplicate URLs are excluded.
    """
    base_parsed = urlparse(base_url)
    base_host = base_parsed.netloc
    base_no_fragment = base_parsed._replace(fragment="").geturl()

    type_to_url: dict[str, str] = {}
    seen_urls: set[str] = {base_no_fragment}

    for tag in soup.find_all("a", href=True):
        text = (tag.get_text(separator=" ", strip=True) or "").lower()
        href = str(tag["href"]).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        abs_no_fragment = parsed._replace(fragment="").geturl()

        if parsed.netloc != base_host:
            continue
        if abs_no_fragment in seen_urls:
            continue

        for page_type, keywords in _FETCH_PRIORITY:
            if page_type in type_to_url:
                continue
            for kw in keywords:
                if kw in text or kw in href.lower():
                    type_to_url[page_type] = abs_no_fragment
                    seen_urls.add(abs_no_fragment)
                    break

    # Select by _FETCH_PRIORITY, NOT by the order links happen to appear in the
    # DOM — and only after scanning every link.
    #
    # This used to `break` out of the link loop as soon as max_subpages types had
    # been collected, then slice `type_to_url` (which is insertion-ordered, i.e.
    # DOM-ordered). Both halves were wrong in the same direction, and the loser
    # was always the impressum: nav links (services/about/team) come first in the
    # DOM, the impressum sits in the FOOTER, i.e. last. On a site with a rich nav
    # the budget was spent before the crawler ever reached the one page the
    # identity ladder actually reads — UID and address both live there.
    # Observed on taxware.ch, which links /de/site/impressum in plain HTML in its
    # footer and still had it skipped in favour of services + team.
    by_priority = [
        (page_type, type_to_url[page_type])
        for page_type, _ in _FETCH_PRIORITY
        if page_type in type_to_url
    ]
    return dict(by_priority[:max_subpages])


# Path fragments that explode a full-site crawl without adding signal:
# pagination, filters, archives, and per-item listing pages.
_CRAWL_TRAP_PATTERNS: tuple[str, ...] = (
    "/page/", "/seite/", "/tag/", "/tags/", "/category/", "/kategorie/",
    "/archiv", "/archive", "/feed", "/rss", "/wp-json", "/wp-admin",
    "/cart", "/warenkorb", "/checkout", "/login", "/logout", "/signin",
    "/search", "/suche", "/recherche", "/filter", "/sort",
    "?s=", "?q=", "?search=", "?page=", "?paged=",
    "/print/", "?print=", "/calendar", "/kalender", "/event/",
)

# Extensions we never want to spend a fetch on (binary/asset, not HTML text).
_NON_HTML_SUFFIXES: tuple[str, ...] = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    ".mp4", ".mp3", ".avi", ".mov", ".wmv", ".webm", ".wav", ".ogg",
    ".css", ".js", ".json", ".xml", ".txt", ".csv", ".exe", ".dmg", ".apk",
)


def is_page_like_url(url: str) -> bool:
    """True if `url` could plausibly BE a company's website page.

    Host-independent half of `is_crawlable_page_url`, for validating a URL that
    has no base host to compare against — i.e. a search-result candidate.

    A candidate is whatever Google returned, and nothing filtered it: PDFs,
    dataset diffs and EU-law pages were all stored as candidate "websites" and
    then fetched as a company homepage. A .pdf can never be a company's site, and
    when one is crawled its text yields OTHER companies' UIDs — which is how a
    SHAB-notices PDF ends up producing a MISMATCH verdict for three unrelated
    companies at once.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return not parsed.path.lower().endswith(_NON_HTML_SUFFIXES)


def is_crawlable_page_url(url: str, base_host: str) -> bool:
    """Return True if `url` is worth spending a full-site crawl slot on.

    Rejects: off-host links, non-HTTP schemes, binary/asset extensions, and
    known crawl traps (pagination, faceted filters, archives, feeds, carts).
    Without the trap filter a single WooCommerce or WordPress site can expand
    into tens of thousands of near-duplicate URLs and consume the entire budget.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc != base_host:
        return False

    path_lower = parsed.path.lower()
    if path_lower.endswith(_NON_HTML_SUFFIXES):
        return False

    full_lower = (parsed.path + ("?" + parsed.query if parsed.query else "")).lower()
    return not any(trap in full_lower for trap in _CRAWL_TRAP_PATTERNS)


def classify_page_type(url: str) -> str:
    """Best-effort page type for an arbitrary URL, by path keyword.

    Falls back to 'other' — phase B crawls whole sites, so most pages have no
    recognised type and that is fine; page_type is a retrieval hint, not a gate.
    """
    path = urlparse(url).path.lower()
    for page_type, keywords in _SUBPAGE_PRIORITY:
        if any(kw in path for kw in keywords):
            return page_type
    return "other"


def extract_internal_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """All crawlable same-host links on a page, de-duplicated, fragments stripped.

    The frontier expander for the phase-B full-site crawl.
    """
    base_host = urlparse(base_url).netloc
    out: list[str] = []
    seen: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = str(tag["href"]).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            abs_url = urljoin(base_url, href)
        except (ValueError, TypeError):
            continue
        abs_url = normalize_page_url(abs_url)
        if abs_url in seen:
            continue
        if not is_crawlable_page_url(abs_url, base_host):
            continue
        seen.add(abs_url)
        out.append(abs_url)

    return out


def normalize_page_url(url: str) -> str:
    """Canonical form for frontier de-duplication: no fragment, no trailing slash.

    `/support/donate` and `/support/donate/` are the same page but were two
    distinct frontier entries and two distinct `visited` keys, so both got
    fetched, stored to S3 and extracted. Sites link to both forms routinely.
    The root path keeps its slash — "https://x.ch" and "https://x.ch/" are the
    same, and stripping it there would produce a schemeless-looking bare host.
    """
    parsed = urlparse(url)._replace(fragment="")
    if len(parsed.path) > 1 and parsed.path.endswith("/"):
        parsed = parsed._replace(path=parsed.path.rstrip("/"))
    return parsed.geturl()


def classify_urls_by_path(
    urls: list[str],
    base_url: str,
    *,
    exclude_types: set[str] | None = None,
    max_needed: int = 4,
) -> dict[str, str]:
    """Classify a flat URL list (e.g. from a sitemap) into subpage types by path.

    Matches the same `_FETCH_PRIORITY` keyword sets used for nav-link discovery
    (fetch-worthy types only), against the URL path only (no anchor text — there
    is none for sitemap URLs). Used to fill subpage slots that homepage nav links
    didn't cover.
    """
    exclude = exclude_types or set()
    base_host = urlparse(base_url).netloc
    type_to_url: dict[str, str] = {}

    for url in urls:
        parsed = urlparse(url)
        if parsed.netloc != base_host:
            continue
        path = parsed.path.lower()
        for page_type, keywords in _FETCH_PRIORITY:
            if page_type in exclude or page_type in type_to_url:
                continue
            # Compare against path segments; keywords may contain spaces, so also
            # check a hyphen/underscore-normalised form of the path.
            norm = path.replace("-", " ").replace("_", " ").replace("/", " ")
            if any(kw in norm for kw in keywords):
                type_to_url[page_type] = parsed._replace(fragment="").geturl()
                break
        if len(type_to_url) >= max_needed:
            break

    return type_to_url


# Hard cap on how many sitemap URLs get persisted as an inventory row per company.
# Bounds table growth against pathological sitemaps (huge news/product catalogs)
# — discover_site_overview already caps at 300 raw URLs; this caps what we keep.
_MAX_INVENTORY_URLS = 60


def classify_all_urls(
    urls: list[str],
    base_url: str,
    *,
    max_urls: int = _MAX_INVENTORY_URLS,
) -> list[tuple[str, str]]:
    """Classify every same-origin sitemap URL into a page_type for the site inventory.

    Unlike classify_urls_by_path (which keeps only the first URL per type, to fill
    a handful of crawl slots), this keeps every matching URL — the point is to show
    what pages exist on the site, not just pick ones to fetch. Unmatched same-origin
    URLs are classified "other". Order follows the input (sitemap) order; capped at
    max_urls total to bound company_web_pages growth.

    Returns a list of (page_type, url), de-duplicated by URL.
    """
    base_host = urlparse(base_url).netloc
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for url in urls:
        parsed = urlparse(url)
        if parsed.netloc != base_host:
            continue
        clean = parsed._replace(fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)

        path = parsed.path.lower()
        norm = path.replace("-", " ").replace("_", " ").replace("/", " ")
        page_type = "other"
        for candidate_type, keywords in _SUBPAGE_PRIORITY:
            if any(kw in norm for kw in keywords):
                page_type = candidate_type
                break

        out.append((page_type, clean))
        if len(out) >= max_urls:
            break

    return out


# ── Bot/JS detection ───────────────────────────────────────────────────────────

def detect_bot_block(
    status_code: int,
    headers: dict[str, str],
    body: str,
) -> tuple[bool, str | None]:
    """Return (is_blocked, protection_type).

    protection_type: cloudflare | captcha | http_403 | js_challenge | None
    """
    lowered = {k.lower(): v for k, v in headers.items()}

    if any(k in lowered for k in _CF_MARKERS):
        if status_code in (403, 429, 503):
            return True, "cloudflare"
        if _CF_BODY_PATTERNS.search(body[:2000]):
            return True, "js_challenge"

    if status_code in (403, 429):
        return True, "http_403"

    body_lower = body[:5000].lower()
    if "captcha" in body_lower or "recaptcha" in body_lower or "hcaptcha" in body_lower:
        return True, "captcha"

    return False, None


def detect_js_required(html: str, word_count: int) -> bool:
    """Heuristic: page is a JS app shell with no rendered content."""
    if word_count > 50:
        return False
    if _JS_APP_ROOT.search(html):
        return True
    noscript = html.lower().count("<noscript")
    script = html.lower().count("<script")
    return script > 3 and noscript > 0 and word_count < 20
