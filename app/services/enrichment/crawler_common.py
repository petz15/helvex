"""Shared utilities for HTTP and Playwright crawlers."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

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

# Priority order — determines fetch sequence when max_pages limits total pages.
# impressum and privacy first because they carry legal/address/contact data.
_SUBPAGE_PRIORITY: list[tuple[str, frozenset[str]]] = [
    ("impressum", _IMPRESSUM_KEYWORDS),
    ("privacy",   _PRIVACY_KEYWORDS),
    ("contact",   _CONTACT_KEYWORDS),
    ("about",     _ABOUT_KEYWORDS),
    ("services",  _SERVICES_KEYWORDS),
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
MAX_PAGE_BYTES: int = 5 * 1024 * 1024  # 5 MB


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class PageResult:
    page_type: str
    url: str
    final_url: str
    http_status: int
    html: bytes
    lang: str | None
    word_count: int
    image_count: int
    video_count: int
    has_contact_form: bool
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


# ── Per-domain rate limiter ────────────────────────────────────────────────────
# Module-level dict persists across asyncio.run() calls within the same process.
# The job worker calls asyncio.run() sequentially, so no concurrency issues.

_domain_last_access: dict[str, float] = {}


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


# ── HTML utilities ─────────────────────────────────────────────────────────────

def parse_soup(html: bytes | str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def detect_page_language(soup: BeautifulSoup) -> str | None:
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
        body = soup.get_text(separator=" ", strip=True)[:2000]
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


def has_contact_form(html_str: str) -> bool:
    return bool(_CONTACT_FORM_PATTERNS.search(html_str))


def find_subpage_links(
    soup: BeautifulSoup,
    base_url: str,
    max_subpages: int = 4,
) -> dict[str, str]:
    """Return priority-ordered subpage URLs from nav/footer links.

    Returns at most max_subpages entries in _SUBPAGE_PRIORITY order.
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

        for page_type, keywords in _SUBPAGE_PRIORITY:
            if page_type in type_to_url:
                continue
            for kw in keywords:
                if kw in text or kw in href.lower():
                    type_to_url[page_type] = abs_no_fragment
                    seen_urls.add(abs_no_fragment)
                    break

        if len(type_to_url) >= max_subpages:
            break

    return dict(list(type_to_url.items())[:max_subpages])


def classify_urls_by_path(
    urls: list[str],
    base_url: str,
    *,
    exclude_types: set[str] | None = None,
    max_needed: int = 4,
) -> dict[str, str]:
    """Classify a flat URL list (e.g. from a sitemap) into subpage types by path.

    Matches the same `_SUBPAGE_PRIORITY` keyword sets used for nav-link discovery,
    but against the URL path only (no anchor text — there is none for sitemap URLs).
    Used to fill subpage slots that homepage nav links didn't cover.
    """
    exclude = exclude_types or set()
    base_host = urlparse(base_url).netloc
    type_to_url: dict[str, str] = {}

    for url in urls:
        parsed = urlparse(url)
        if parsed.netloc != base_host:
            continue
        path = parsed.path.lower()
        for page_type, keywords in _SUBPAGE_PRIORITY:
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
