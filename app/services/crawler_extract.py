"""Deterministic structured-data extraction from crawled company HTML.

No API cost: trafilatura main-text extraction + regex / schema.org parsers.
Consumes raw HTML (already stored in S3 by the crawlers) and produces a single
resolved record per company, deduplicated across all of its crawled pages.

The optional LLM enrichment layer (description/services summary via Claude Haiku)
is intentionally NOT implemented here — see ROADMAP.md.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.services.crawler_common import parse_soup

logger = logging.getLogger(__name__)

# ── Regexes ──────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Swiss company identifier, e.g. CHE-123.456.789
_UID_RE = re.compile(r"CHE[-\s]?(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})", re.IGNORECASE)

# Junk emails to drop (asset hashes, tracking, CMS vendors, placeholders).
_EMAIL_BLOCKLIST = (
    "example.com", "example.org", "sentry.io", "wixpress.com", "wix.com",
    "domain.com", "email.com", "yourcompany", "your-email", "test.com",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
)

# Social platforms → canonical key, matched by host substring.
_SOCIAL_HOSTS: list[tuple[str, str]] = [
    ("linkedin.", "linkedin"),
    ("xing.", "xing"),
    ("facebook.", "facebook"),
    ("instagram.", "instagram"),
    ("youtube.", "youtube"),
    ("youtu.be", "youtube"),
    ("twitter.", "twitter"),
    ("x.com", "twitter"),
    ("tiktok.", "tiktok"),
]

# Lightweight multilingual stopwords for service-keyword mining (DE/FR/IT/EN).
# Kept small on purpose — the heavier keyword pipeline is reused downstream.
_STOPWORDS = frozenset([
    # DE
    "und", "oder", "der", "die", "das", "den", "dem", "ein", "eine", "einen",
    "für", "mit", "von", "auf", "aus", "bei", "wir", "sie", "ihre", "unsere",
    "sind", "wird", "werden", "kann", "auch", "über", "unser", "uns", "sich",
    "mehr", "alle", "zum", "zur", "des", "ist", "haben", "nicht", "durch",
    # FR
    "les", "des", "une", "vous", "nous", "pour", "avec", "dans", "sur", "est",
    "votre", "notre", "nos", "vos", "plus", "tout", "tous", "par", "aux",
    # IT
    "che", "con", "per", "del", "della", "dei", "delle", "una", "sono", "nostra",
    "vostra", "anche", "come", "alla", "nel", "gli",
    # EN
    "the", "and", "for", "with", "our", "your", "you", "are", "from", "this",
    "that", "all", "more", "have", "has", "can", "will", "their", "they", "was",
    # generic web
    "home", "kontakt", "contact", "impressum", "datenschutz", "cookie", "cookies",
    "menu", "newsletter", "copyright", "rights", "reserved", "www", "http", "https",
])

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{4,}")


# ── Per-page + resolved containers ───────────────────────────────────────────

@dataclass
class PageSignals:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    socials: dict[str, str] = field(default_factory=dict)
    uid: str | None = None
    address: str | None = None
    description: str | None = None
    languages: set[str] = field(default_factory=set)
    text: str = ""


# ── Field extractors ─────────────────────────────────────────────────────────

def _clean_emails(raw: list[str]) -> set[str]:
    out: set[str] = set()
    for e in raw:
        el = e.strip().lower().rstrip(".")
        if any(bad in el for bad in _EMAIL_BLOCKLIST):
            continue
        if len(el) > 100:
            continue
        out.add(el)
    return out


def _extract_emails(html_str: str, soup) -> set[str]:
    found = set(_EMAIL_RE.findall(html_str))
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr:
                found.add(addr)
    return _clean_emails(list(found))


def _extract_phones(html_str: str, soup) -> set[str]:
    out: set[str] = set()
    try:
        import phonenumbers
    except ImportError:  # pragma: no cover
        return out

    candidates = [html_str]
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.lower().startswith("tel:"):
            candidates.append(href[4:])

    for blob in candidates:
        try:
            for match in phonenumbers.PhoneNumberMatcher(blob, "CH"):
                if phonenumbers.is_valid_number(match.number):
                    out.add(phonenumbers.format_number(
                        match.number, phonenumbers.PhoneNumberFormat.E164
                    ))
        except Exception:  # noqa: BLE001
            continue
        if len(out) >= 10:
            break
    return out


def _extract_socials(soup) -> dict[str, str]:
    socials: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        host = urlparse(href).netloc.lower()
        if not host:
            continue
        for needle, key in _SOCIAL_HOSTS:
            if needle in host and key not in socials:
                # Skip bare share/intent links
                if "share" in href.lower() or "intent" in href.lower():
                    continue
                socials[key] = href
                break
    return socials


def _extract_uid(html_str: str) -> str | None:
    m = _UID_RE.search(html_str)
    if not m:
        return None
    return f"CHE-{m.group(1)}.{m.group(2)}.{m.group(3)}"


def _extract_languages(soup) -> set[str]:
    langs: set[str] = set()
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        langs.add(str(html_tag["lang"])[:2].lower())
    for link in soup.find_all("link", attrs={"hreflang": True}):
        hl = str(link["hreflang"])[:2].lower()
        if hl and hl != "x-":
            langs.add(hl)
    return {l for l in langs if len(l) == 2 and l.isalpha()}


def _walk_jsonld(node, out: PageSignals) -> None:
    """Pull Organization/LocalBusiness fields from a parsed JSON-LD node."""
    if isinstance(node, list):
        for n in node:
            _walk_jsonld(n, out)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _walk_jsonld(node["@graph"], out)

    types = node.get("@type", "")
    type_str = " ".join(types) if isinstance(types, list) else str(types)
    is_org = any(t in type_str for t in ("Organization", "LocalBusiness", "Corporation", "Store"))

    if is_org:
        if not out.description and node.get("description"):
            out.description = str(node["description"])[:1000]
        tel = node.get("telephone")
        if tel:
            out.phones.add(str(tel))
        email = node.get("email")
        if email:
            out.emails |= _clean_emails([str(email).replace("mailto:", "")])
        same = node.get("sameAs")
        if same:
            for url in (same if isinstance(same, list) else [same]):
                host = urlparse(str(url)).netloc.lower()
                for needle, key in _SOCIAL_HOSTS:
                    if needle in host and key not in out.socials:
                        out.socials[key] = str(url)
        addr = node.get("address")
        if isinstance(addr, dict) and not out.address:
            parts = [
                addr.get("streetAddress"), addr.get("postalCode"),
                addr.get("addressLocality"), addr.get("addressCountry"),
            ]
            joined = ", ".join(str(p) for p in parts if p)
            if joined:
                out.address = joined[:300]

    # Recurse into nested dict values that may carry more org nodes.
    for v in node.values():
        if isinstance(v, (list, dict)):
            _walk_jsonld(v, out)


def _extract_jsonld(soup, out: PageSignals) -> None:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        try:
            _walk_jsonld(data, out)
        except Exception:  # noqa: BLE001
            logger.debug("JSON-LD walk failed", exc_info=True)


def _extract_meta_description(soup) -> str | None:
    for attrs in (
        {"property": "og:description"},
        {"name": "description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return str(tag["content"]).strip()[:1000]
    return None


def _main_text(html_str: str) -> str:
    try:
        import trafilatura
        txt = trafilatura.extract(
            html_str, include_comments=False, include_tables=False,
            no_fallback=True, favor_precision=True,
        )
        return txt or ""
    except Exception:  # noqa: BLE001
        return ""


# ── Public API ───────────────────────────────────────────────────────────────

def extract_page(html: bytes, page_type: str = "") -> PageSignals:
    """Extract deterministic signals from a single page's HTML."""
    out = PageSignals()
    if not html:
        return out
    html_str = html.decode("utf-8", errors="replace")
    soup = parse_soup(html)

    out.emails |= _extract_emails(html_str, soup)
    out.phones |= _extract_phones(html_str, soup)
    out.socials.update(_extract_socials(soup))
    out.uid = _extract_uid(html_str)
    out.languages |= _extract_languages(soup)
    out.description = _extract_meta_description(soup)
    _extract_jsonld(soup, out)
    # Mine service keywords mainly from homepage/about/services pages.
    if page_type in ("", "homepage", "about", "services"):
        out.text = _main_text(html_str)
    return out


def _mine_keywords(text: str, top_n: int = 15) -> list[str]:
    if not text:
        return []
    counts: Counter[str] = Counter()
    for w in _WORD_RE.findall(text.lower()):
        if w in _STOPWORDS:
            continue
        counts[w] += 1
    return [w for w, c in counts.most_common(top_n) if c >= 2]


def resolve_company_extract(pages: list[tuple[str, bytes]]) -> dict:
    """Aggregate per-page signals into one resolved, deduplicated record.

    pages: list of (page_type, html_bytes). Returns a dict ready to persist via
    crud.crawler.upsert_web_extract. Returns {} if nothing useful was found.
    """
    emails: set[str] = set()
    phones: set[str] = set()
    socials: dict[str, str] = {}
    languages: set[str] = set()
    uid: str | None = None
    address: str | None = None
    description: str | None = None
    text_parts: list[str] = []

    for page_type, html in pages:
        try:
            sig = extract_page(html, page_type)
        except Exception:  # noqa: BLE001
            logger.debug("extract_page failed for %s", page_type, exc_info=True)
            continue
        emails |= sig.emails
        phones |= sig.phones
        languages |= sig.languages
        for k, v in sig.socials.items():
            socials.setdefault(k, v)
        uid = uid or sig.uid
        # Prefer address/description from impressum pages, else first found.
        if sig.address and (address is None or page_type == "impressum"):
            address = sig.address
        if sig.description and (description is None or page_type == "homepage"):
            description = sig.description
        if sig.text:
            text_parts.append(sig.text)

    service_keywords = _mine_keywords(" ".join(text_parts))

    # Heuristic confidence: each resolved signal class contributes.
    signals_present = sum(bool(x) for x in (
        emails, phones, socials, uid, address, description, service_keywords,
    ))
    confidence = round(min(1.0, signals_present / 6.0), 2)

    if signals_present == 0:
        return {}

    return {
        "emails": sorted(emails)[:20] or None,
        "phones": sorted(phones)[:20] or None,
        "socials": socials or None,
        "uid": uid,
        "address": address,
        "languages": sorted(languages) or None,
        "description": description,
        "service_keywords": service_keywords or None,
        "extraction_method": "deterministic",
        "confidence": confidence,
        "page_count": len(pages),
    }
