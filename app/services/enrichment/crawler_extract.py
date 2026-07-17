"""Deterministic structured-data extraction from crawled company HTML.

No API cost: trafilatura main-text extraction + regex / schema.org parsers.
Consumes raw HTML (already stored in S3 by the crawlers) and produces a single
resolved record per company, deduplicated across all of its crawled pages.

Verification-aware: when the site exposes a Swiss UID it is compared to the
company's Zefix UID. A match is near-certain proof the crawl hit the right site
(confidence ≈ 1.0); a mismatch strongly suggests a wrong search result and is
penalised so get_best_web_extract() prefers a better candidate.

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

from app.services.enrichment.crawler_common import parse_soup
from app.services.ml.language_detection import detect_purpose_language as _lingua_detect

logger = logging.getLogger(__name__)

# ── Regexes ──────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Swiss company identifier, e.g. CHE-123.456.789
_UID_RE = re.compile(r"CHE[-\s]?(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})", re.IGNORECASE)

# Swiss street line: name + suffix + house number (DE/FR/IT variants).
_STREET_RE = re.compile(
    r"([A-ZÄÖÜ][\wäöüàâéèêëïîôûç.\-]*\s?"
    r"(?:strasse|straße|str\.?|gasse|weg|platz|allee|ring|hof|rue|route|chemin|"
    r"avenue|av\.?|via|viale|piazza|corso|quai|boulevard|bd\.?)\.?\s+\d+[a-zA-Z]?)",
    re.IGNORECASE,
)
# Swiss postal code + town: 4 digits + capitalised town.
# Trailing /SecondName handles bilingual cities (e.g. "2500 Biel/Bienne").
_PLZ_CITY_RE = re.compile(
    r"\b(\d{4})\s+([A-ZÄÖÜ][A-Za-zÀ-ÿ.\-]+(?:[ \-][A-ZÄÖÜ][A-Za-zÀ-ÿ.\-]+){0,2}"
    r"(?:\/[A-ZÄÖÜ][A-Za-zÀ-ÿ\-]+)?)"
)

# Preposition-based Swiss addresses without an explicit street-type suffix.
# Covers rural patterns like "Im Schwand 3", "Am Bach 8", "Auf der Höhe 12".
_STREET_NO_SUFFIX_RE = re.compile(
    r"\b((?:Im|Am|An\s+der|Auf\s+der|In\s+der|Beim?|Zur?|Hinter|Unter|Ober|Neben|Vor)\s+"
    r"[A-Za-zÀ-ÿäöüÄÖÜ]+(?:\s+[A-Za-zÀ-ÿäöüÄÖÜ]+)?\s+\d+[a-zA-Z]?)",
    re.IGNORECASE,
)

# Role labels (DE/FR/IT/EN) → capture the following 1–3 capitalised name tokens.
_PERSON_RE = re.compile(
    r"(?:Gesch[äa]ftsf[üu]hrer(?:in)?|Inhaber(?:in)?|Gr[üu]nder(?:in)?|Eigent[üu]mer(?:in)?|"
    r"Direktor(?:in)?|Gesch[äa]ftsleitung|Verwaltungsratspr[äa]sident(?:in)?|Verwaltungsrat|"
    r"CEO|CFO|CTO|Managing\s+Director|Founder|Owner|"
    r"Pr[ée]sident(?:e)?|Directeur|Directrice|Administrateur|G[ée]rant(?:e)?|"
    r"Amministratore|Titolare|Direttore)"
    r"[:\s\-–]+((?:Dr\.?\s+|Prof\.?\s+)?[A-ZÄÖÜ][a-zäöüàâéèêëïîôûç]+"
    r"(?:\s+[A-ZÄÖÜ][a-zäöüàâéèêëïîôûç'\-]+){1,2})"
)

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{4,}")
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ]{3,}")

# Junk emails to drop (asset hashes, tracking, CMS vendors, placeholders).
_EMAIL_BLOCKLIST = (
    "example.com", "example.org", "sentry.io", "wixpress.com", "wix.com",
    "domain.com", "email.com", "yourcompany", "your-email", "test.com",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
)
# Generic mailbox locals that make good primary contacts.
_ROLE_LOCALS = ("info", "kontakt", "contact", "hello", "office", "mail", "welcome", "anfrage")
# Free webmail domains — valid but weak (not a company-owned address).
_FREEMAIL = ("gmail.", "hotmail.", "outlook.", "yahoo.", "gmx.", "bluewin.", "icloud.", "web.de")

# Legal-form tokens stripped before name matching.
_LEGAL_FORMS = frozenset([
    "ag", "sa", "gmbh", "sarl", "sàrl", "sagl", "llc", "ltd", "inc", "co",
    "kg", "ohg", "gbr", "holding", "group", "gruppe", "company", "the",
])

# Tokens so ubiquitous on Swiss/European business sites that their presence in
# page body text is essentially no evidence of identity. Used to separate
# "distinctive" tokens from noise when computing unverified name confidence.
_GENERIC_NAME_TOKENS = frozenset([
    "swiss", "suisse", "svizzera", "schweiz", "schweizer", "helvetia", "suiza",
    "solutions", "solution", "services", "service", "consulting", "management",
    "international", "global", "systems", "system", "tech", "technology",
    "digital", "media", "design", "concept", "gruppe", "partner", "partners",
    "home", "center", "centre", "studio", "office", "industries", "enterprise",
    "enterprises", "innovations", "innovation", "network", "networks",
])

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
_STOPWORDS = frozenset([
    # DE
    "und", "oder", "der", "die", "das", "den", "dem", "ein", "eine", "einen",
    "für", "mit", "von", "auf", "aus", "bei", "wir", "sie", "ihre", "unsere",
    "sind", "wird", "werden", "kann", "auch", "über", "unser", "uns", "sich",
    "mehr", "alle", "zum", "zur", "des", "ist", "haben", "nicht", "durch",
    "sowie", "wie", "als", "bis", "nach", "vor", "dass", "diese", "dieser",
    "wenn", "dann", "schon", "immer", "hier", "dazu", "damit", "sein", "ihren",
    # FR
    "les", "des", "une", "vous", "nous", "pour", "avec", "dans", "sur", "est",
    "votre", "notre", "nos", "vos", "plus", "tout", "tous", "par", "aux", "ont",
    "cette", "leur", "leurs", "sont", "être", "fait", "ainsi", "entre",
    # IT
    "che", "con", "per", "del", "della", "dei", "delle", "una", "sono", "nostra",
    "vostra", "anche", "come", "alla", "nel", "gli", "sono", "questo", "questa",
    # EN
    "the", "and", "for", "with", "our", "your", "you", "are", "from", "this",
    "that", "all", "more", "have", "has", "can", "will", "their", "they", "was",
    "which", "about", "also", "been", "were", "into", "than", "them", "these",
    # generic web / boilerplate
    "home", "kontakt", "contact", "impressum", "datenschutz", "cookie", "cookies",
    "menu", "newsletter", "copyright", "rights", "reserved", "www", "http", "https",
    "email", "phone", "tel", "page", "site", "website", "team", "willkommen",
    "accueil", "mentions", "légales", "benvenuti", "privacy", "terms",
])


# ── Per-page container ────────────────────────────────────────────────────────

@dataclass
class PageSignals:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    socials: dict[str, str] = field(default_factory=dict)
    uid: str | None = None
    address: str | None = None
    description: str | None = None
    languages: set[str] = field(default_factory=set)
    title: str = ""
    text: str = ""


# ── Field extractors ─────────────────────────────────────────────────────────

def _clean_emails(raw: list[str]) -> set[str]:
    try:
        from email_validator import EmailNotValidError, validate_email
    except ImportError:  # pragma: no cover
        validate_email = None  # type: ignore[assignment]

    out: set[str] = set()
    for e in raw:
        el = e.strip().lower().rstrip(".")
        if any(bad in el for bad in _EMAIL_BLOCKLIST):
            continue
        if len(el) > 100 or el.count("@") != 1:
            continue
        if validate_email is not None:
            try:
                validate_email(el, check_deliverability=False)
            except EmailNotValidError:
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

    # tel: hrefs are the highest-signal source — collect them first.
    tel_blobs: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.lower().startswith("tel:"):
            tel_blobs.append(href[4:])

    for blob in tel_blobs + [html_str]:
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
                if "share" in href.lower() or "intent" in href.lower():
                    continue
                socials[key] = href
                break
    return socials


def _extract_uid(html_str: str) -> str | None:
    m = _UID_RE.search(html_str)
    if not m:
        return None
    candidate = f"CHE-{m.group(1)}.{m.group(2)}.{m.group(3)}"
    try:
        from stdnum.ch import uid as stdnum_uid
    except ImportError:  # pragma: no cover
        return candidate
    # Checksum validation rejects regex false-positives (random digit runs
    # that happen to match the CHE-xxx.xxx.xxx shape but aren't a real UID).
    if not stdnum_uid.is_valid(candidate):
        return None
    return stdnum_uid.format(candidate)


def _extract_languages(soup) -> set[str]:
    langs: set[str] = set()
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        langs.add(str(html_tag["lang"])[:2].lower())
    for link in soup.find_all("link", attrs={"hreflang": True}):
        hl = str(link["hreflang"])[:2].lower()
        if hl and hl != "x-":
            langs.add(hl)
    langs = {lang for lang in langs if len(lang) == 2 and lang.isalpha()}
    # Lingua content-based fallback when no HTML metadata declares a language
    if not langs:
        body = soup.get_text(separator=" ", strip=True)
        detected = _lingua_detect(body[:2000])
        if detected:
            langs.add(detected)
    return langs


def _address_from_text(text: str) -> str | None:
    """Parse a Swiss postal address from free text (impressum/contact fallback)."""
    if not text:
        return None
    plz = _PLZ_CITY_RE.search(text)
    if not plz:
        return None
    plz_city = f"{plz.group(1)} {plz.group(2).strip()}"
    # Find a street line appearing shortly before the PLZ/city.
    window = text[max(0, plz.start() - 120): plz.start()]
    street_match = None
    for street_match in _STREET_RE.finditer(window):
        pass  # keep the last (closest to PLZ)
    if street_match:
        return f"{street_match.group(1).strip()}, {plz_city}"[:300]
    # Fallback: preposition-based addresses without an explicit street suffix
    # (e.g. "Im Schwand 3", "Am Bach 8") — common in rural cantons.
    no_suffix = _STREET_NO_SUFFIX_RE.search(window)
    if no_suffix:
        return f"{no_suffix.group(1).strip()}, {plz_city}"[:300]
    return plz_city[:300]


def _extract_persons(text: str) -> list[str]:
    """Pull management/contact names that follow an explicit role label."""
    if not text:
        return []
    seen: list[str] = []
    for m in _PERSON_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" -–")
        # Reject obvious false positives (single token already excluded by regex).
        if len(name) < 5 or len(name) > 60:
            continue
        if name not in seen:
            seen.append(name)
        if len(seen) >= 8:
            break
    return seen


# Lazily-loaded NER-only spaCy pipelines, keyed by language. None means the
# model isn't installed (e.g. local dev) — cached so we don't retry per page.
# Loaded only by the ml-worker process, where these models are bundled.
_SPACY_NER_MODELS = {
    "de": "de_core_news_md", "fr": "fr_core_news_sm",
    "it": "it_core_news_sm", "en": "en_core_web_sm",
}
_SPACY_PERSON_LABEL = {"de": "PER", "fr": "PER", "it": "PER", "en": "PERSON"}
_spacy_ner_cache: dict[str, object] = {}


def _get_spacy_ner(lang: str):
    if lang in _spacy_ner_cache:
        return _spacy_ner_cache[lang]
    nlp = None
    try:
        import spacy
        nlp = spacy.load(_SPACY_NER_MODELS[lang])
        keep = {"ner", "tok2vec", "transformer"}
        nlp.disable_pipes(*[p for p in nlp.pipe_names if p not in keep])
    except Exception:  # noqa: BLE001 — model not installed on this pod, or load failed
        nlp = None
    _spacy_ner_cache[lang] = nlp
    return nlp


def _extract_persons_ner(text: str, lang: str | None) -> list[str]:
    """NER-based person extraction — catches names not adjacent to a role label
    (e.g. team-page bios), complementing _extract_persons' regex pass."""
    if not text:
        return []
    model_lang = lang if lang in _SPACY_NER_MODELS else "de"
    nlp = _get_spacy_ner(model_lang)
    if nlp is None:
        return []
    label = _SPACY_PERSON_LABEL[model_lang]
    out: list[str] = []
    seen: set[str] = set()
    try:
        doc = nlp(text[:5000])
    except Exception:  # noqa: BLE001
        return []
    for ent in doc.ents:
        if ent.label_ != label:
            continue
        name = re.sub(r"\s+", " ", ent.text).strip(" -–")
        if len(name) < 5 or len(name) > 60 or " " not in name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= 8:
            break
    return out


def _walk_jsonld(node, out: PageSignals) -> None:
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


def _normalize_extruct_item(item: dict) -> dict | None:
    """Reshape an extruct microdata/RDFa item into the {"@type", ...props} shape
    _walk_jsonld already understands, so both syntaxes share one merge path."""
    raw_type = item.get("type")
    if not raw_type:
        return None
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    short_types = [str(t).rsplit("/", 1)[-1].rsplit(":", 1)[-1] for t in types]
    normalized: dict = {"@type": short_types}
    normalized.update(item.get("properties") or {})
    return normalized


def _extract_microdata_rdfa(html_str: str, out: PageSignals) -> None:
    """Catch Organization/LocalBusiness data marked up as Microdata or RDFa —
    older Swiss SME sites often predate widespread JSON-LD adoption."""
    try:
        import extruct
    except ImportError:  # pragma: no cover
        return
    try:
        data = extruct.extract(html_str, syntaxes=["microdata", "rdfa"], uniform=True)
    except Exception:  # noqa: BLE001
        logger.debug("extruct extraction failed", exc_info=True)
        return
    for syntax_key in ("microdata", "rdfa"):
        for item in data.get(syntax_key, []) or []:
            normalized = _normalize_extruct_item(item)
            if normalized:
                try:
                    _walk_jsonld(normalized, out)
                except Exception:  # noqa: BLE001
                    continue


def _extract_meta_description(soup) -> str | None:
    for attrs in ({"property": "og:description"}, {"name": "description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return str(tag["content"]).strip()[:1000]
    return None


def _extract_title(soup) -> str:
    tag = soup.find("meta", attrs={"property": "og:site_name"})
    if tag and tag.get("content"):
        return str(tag["content"]).strip()[:200]
    if soup.title and soup.title.string:
        return str(soup.title.string).strip()[:200]
    return ""


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


def _extract_from_address_tag(soup) -> str | None:
    """Parse Swiss address from semantic <address> HTML elements."""
    for addr in soup.find_all("address"):
        text = addr.get_text(separator="\n", strip=True)
        result = _address_from_text(text)
        if result:
            return result
    return None


def _contact_page_text(html_str: str) -> str:
    """Text extraction for impressum/contact pages.

    Bypasses trafilatura (favor_precision strips contact blocks as boilerplate)
    and uses BeautifulSoup instead so address/phone/email lines are preserved.
    Tries <address> tags first; falls back to stripped full-body text.
    """
    soup = parse_soup(html_str)
    addr_tags = soup.find_all("address")
    if addr_tags:
        return "\n".join(t.get_text(separator="\n", strip=True) for t in addr_tags)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:10000]


# ── Public API ───────────────────────────────────────────────────────────────

# Pages whose main text we mine for keywords / address / persons.
_TEXT_PAGES = frozenset({"", "homepage", "about", "services", "impressum", "contact"})


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
    out.title = _extract_title(soup)
    _extract_jsonld(soup, out)
    _extract_microdata_rdfa(html_str, out)
    # Semantic <address> tag — more targeted than free-text regex, less than JSON-LD.
    if not out.address:
        out.address = _extract_from_address_tag(soup)
    if page_type in _TEXT_PAGES:
        # Impressum/contact pages: trafilatura's precision filter discards contact
        # blocks as boilerplate, so use BeautifulSoup text extraction instead.
        if page_type in ("impressum", "contact"):
            out.text = _contact_page_text(html_str)
        else:
            out.text = _main_text(html_str)
    return out


def _rank_emails(emails: set[str], site_domain: str | None) -> list[str]:
    """Order emails best-first: same-domain role boxes > same-domain > role > rest."""
    def score(e: str) -> tuple:
        local, _, domain = e.partition("@")
        same_domain = bool(site_domain) and site_domain in domain
        is_role = any(local.startswith(r) for r in _ROLE_LOCALS)
        is_free = any(f in domain for f in _FREEMAIL)
        # Higher tuple sorts first (we reverse).
        return (same_domain, is_role, not is_free)
    return sorted(emails, key=lambda e: (score(e), e), reverse=True)


def _mine_keywords(text: str, top_n: int = 15) -> list[str]:
    """Bigram-preferring keyword miner. Multi-word phrases beat single tokens."""
    if not text:
        return []
    seq = [w.lower() for w in _TOKEN_RE.findall(text)]
    uni: Counter[str] = Counter()
    big: Counter[str] = Counter()
    for i, w in enumerate(seq):
        if len(w) >= 4 and w not in _STOPWORDS:
            uni[w] += 1
        if i + 1 < len(seq):
            a, b = w, seq[i + 1]
            if a not in _STOPWORDS and b not in _STOPWORDS and len(a) >= 4 and len(b) >= 4:
                big[f"{a} {b}"] += 1

    out: list[str] = [bg for bg, c in big.most_common(top_n) if c >= 2]
    used = set(" ".join(out).split())
    for w, c in uni.most_common(top_n * 3):
        if len(out) >= top_n:
            break
        if c >= 3 and w not in used:
            out.append(w)
            used.add(w)
    return out[:top_n]


def _name_tokens(name: str) -> set[str]:
    base = name
    try:
        from cleanco import basename
        base = basename(name) or name
    except ImportError:  # pragma: no cover
        pass
    return {
        t for t in (w.lower() for w in _TOKEN_RE.findall(base))
        if len(t) >= 3 and t not in _LEGAL_FORMS
    }


def _address_matches_company(
    extracted_address: str | None, company_zip: str | None, company_city: str | None,
) -> bool:
    """True if the extracted address contains both the company's zip and city."""
    if not extracted_address or not company_zip or not company_city:
        return False
    return company_zip.strip() in extracted_address and company_city.strip().lower() in extracted_address.lower()


def _name_match_ratio(company_name: str | None, haystack: str) -> float:
    """Fraction of distinctive company-name tokens present in the page haystack."""
    if not company_name:
        return 0.0
    toks = _name_tokens(company_name)
    if not toks:
        return 0.0
    hl = haystack.lower()
    hits = sum(1 for t in toks if t in hl)
    return hits / len(toks)


def _zone_weighted_name_ratio(
    company_name: str | None,
    site_url: str | None,
    page_titles: list[str],
    all_text_parts: list[str],
) -> float:
    """Return 0.0–1.0 identity confidence from name signals alone.

    Zones: domain SLD (near-proof) >> page titles (strong) >> body text (weak).
    Generic tokens like 'swiss', 'solutions' are excluded from the match pool
    because they appear on almost every Swiss business page and contribute
    no evidence of identity.
    """
    if not company_name:
        return 0.0

    all_toks = _name_tokens(company_name)
    if not all_toks:
        return 0.0

    distinctive = all_toks - _GENERIC_NAME_TOKENS
    # If all tokens are generic, fall back to all tokens with a heavy penalty applied later.
    toks = distinctive if distinctive else all_toks
    generic_only = not distinctive

    def _hit_ratio(tok_set: set, haystack: str) -> float:
        h = haystack.lower()
        return sum(1 for t in tok_set if t in h) / len(tok_set)

    # Zone 1: second-level domain (highest signal — company registered domain)
    domain_ratio = 0.0
    if site_url:
        try:
            sld = urlparse(site_url).netloc.lower().lstrip("www.").split(".")[0]
            domain_ratio = _hit_ratio(toks, sld)
        except Exception:
            pass

    # Zone 2: page title(s) — weaker than domain but still prominent placement
    title_ratio = _hit_ratio(toks, " ".join(page_titles)) if page_titles else 0.0

    # Zone 3: body text — very weak; body text on any page can match incidentally.
    # Only count distinctive tokens here; generic-only names get zero body credit.
    body_ratio = 0.0
    if not generic_only:
        body_ratio = _hit_ratio(toks, " ".join(all_text_parts)[:5000])

    if domain_ratio > 0:
        composite = 0.65 * domain_ratio + 0.25 * title_ratio + 0.10 * body_ratio
    elif title_ratio > 0:
        # Title match without domain: medium confidence
        composite = 0.55 * title_ratio + 0.45 * body_ratio * 0.25
    else:
        # Body-only: very low — most generic words appear anywhere
        composite = body_ratio * 0.12

    # Generic-only names (e.g. "Swiss Solutions GmbH") get an additional 60% penalty
    # because even distinctive-only scoring above may overcount.
    if generic_only:
        composite *= 0.4

    return round(min(1.0, composite), 3)


def _norm_uid(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.replace(" ", "").replace(".", "").replace("-", "").upper() or None


def resolve_company_extract(
    pages: list[tuple[str, bytes]],
    *,
    company_name: str | None = None,
    zefix_uid: str | None = None,
    site_url: str | None = None,
    company_zip: str | None = None,
    company_city: str | None = None,
    page_types: list[str] | None = None,
) -> dict:
    """Aggregate per-page signals into one resolved, verification-aware record.

    pages: list of (page_type, html_bytes). Optional context (company_name,
    zefix_uid, site_url, company_zip, company_city) drives UID verification,
    name/address matching, and email ranking. page_types: the full list of
    page_types fetched for this candidate (may include 'impressum', 'contact'
    etc. not present in pages when S3 download failed). Returns a dict ready to
    persist via crud.crawler.upsert_web_extract, or {} if nothing useful was found.
    """
    emails: set[str] = set()
    phones: set[str] = set()
    socials: dict[str, str] = {}
    languages: set[str] = set()
    uid_by_page: dict[str, str] = {}  # page_type -> uid (impressum preferred below)
    address: str | None = None
    description: str | None = None
    titles: list[str] = []
    kw_text_parts: list[str] = []
    impressum_text_parts: list[str] = []
    all_text_parts: list[str] = []

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
        if sig.uid:
            uid_by_page[page_type] = sig.uid
        if sig.title:
            titles.append(sig.title)
        if sig.address and (address is None or page_type == "impressum"):
            address = sig.address
        if sig.description and (description is None or page_type == "homepage"):
            description = sig.description
        if sig.text:
            all_text_parts.append(sig.text)
            if page_type in ("", "homepage", "about", "services"):
                kw_text_parts.append(sig.text)
            if page_type in ("impressum", "contact", "about"):
                impressum_text_parts.append(sig.text)

    impressum_text = "\n".join(impressum_text_parts) or "\n".join(all_text_parts)

    # Address fallback: parse Swiss postal address from impressum text.
    if address is None:
        address = _address_from_text(impressum_text)

    # Persons: management/contact names from impressum/about — regex (role-labelled,
    # high precision) first, then NER additions (catches names with no role label).
    persons = _extract_persons(impressum_text)
    lang_hint = sorted(languages)[0] if languages else None
    for name in _extract_persons_ner(impressum_text, lang_hint):
        if name not in persons:
            persons.append(name)
    persons = persons[:8]

    # Description fallback: first paragraph of main text.
    if not description and all_text_parts:
        para = all_text_parts[0].strip().split("\n", 1)[0].strip()
        if len(para) >= 40:
            description = para[:1000]

    # ── Verification: site UID vs Zefix UID ──────────────────────────────────
    # Prefer impressum UID — it's the authoritative legal page.
    # Homepage may carry a parent/holding company's UID instead.
    uid = (
        uid_by_page.get("impressum")
        or uid_by_page.get("contact")
        or next(iter(uid_by_page.values()), None)
    )
    site_uid_n = _norm_uid(uid)
    zefix_uid_n = _norm_uid(zefix_uid)
    uid_matches: bool | None = None
    if site_uid_n and zefix_uid_n:
        uid_matches = site_uid_n == zefix_uid_n

    # Name match against title + site URL + leading body text.
    # Used only for the name_address_verified threshold check (needs full haystack).
    haystack = " ".join([
        " ".join(titles),
        site_url or "",
        " ".join(all_text_parts)[:5000],
    ])
    name_ratio = _name_match_ratio(company_name, haystack)

    # Zone-weighted name confidence (domain >> title >> body, generic tokens discounted).
    zone_name_conf = _zone_weighted_name_ratio(company_name, site_url, titles, all_text_parts)

    # Graded address verification: full (zip+city) > partial (one of the two) > none.
    addr_full_match = _address_matches_company(address, company_zip, company_city)
    addr_partial_match = (
        not addr_full_match
        and bool(address)
        and (
            (company_zip and company_zip.strip() in address)
            or (company_city and company_city.strip().lower() in address.lower())
        )
    )
    addr_score = 1.0 if addr_full_match else (0.35 if addr_partial_match else 0.0)

    # Strongest fallback path: all name tokens + exact address verified, no UID needed.
    name_address_verified = uid_matches is None and name_ratio >= 0.999 and addr_full_match

    site_domain = urlparse(site_url).netloc.lower().lstrip("www.") if site_url else None
    ranked_emails = _rank_emails(emails, site_domain)
    service_keywords = _mine_keywords(" ".join(kw_text_parts) or " ".join(all_text_parts))

    # Site-quality bonus: impressum page (legal requirement for Swiss businesses) and
    # contact page are high-confidence signals that this is a real, active business site.
    # Add one extra "signal" for each, lifting the base coverage score by ~1/7 per page.
    _crawled_types = set(page_types or [t for t, _ in pages])
    has_impressum_page = "impressum" in _crawled_types or "contact" in _crawled_types
    signals_present = sum(bool(x) for x in (
        emails, phones, socials, uid, address, description, service_keywords, persons,
    )) + (1 if has_impressum_page else 0)
    if signals_present == 0 and uid_matches is None:
        return {}

    # ── Confidence model (layered, additive) ─────────────────────────────────
    #
    # Three identity layers contribute in decreasing priority; signal coverage
    # (base) adds a small residual bonus. Each layer's contribution shrinks once
    # stronger layers have already established identity, so a confirmed UID makes
    # address and name merely corroborating rather than decisive.
    #
    #   Layer 1 — UID verification  (0.80 base, dominates)
    #   Layer 2 — address match     (0–0.10 on top of UID; 0–0.55 without UID)
    #   Layer 3 — zone name match   (0–0.06 on top of UID; 0–0.35 without UID)
    #   Layer 4 — signal coverage   (≤0.10 residual, diminishes as identity firms up)
    #
    base = min(1.0, signals_present / 7.0)

    if uid_matches is True:
        # UID is near-proof — address and name add small incremental certainty.
        confidence = round(
            min(1.0, 0.80 + 0.10 * addr_score + 0.06 * zone_name_conf + 0.04 * base), 2
        )
        method = "deterministic+uid_verified"
        if addr_full_match:
            method += "+address"
        elif addr_partial_match:
            method += "+address_partial"

    elif uid_matches is False:
        # UID contradicts the company — heavy penalty. Address and name can
        # partially recover (e.g. a subsidiary page showing the parent's UID)
        # but confidence stays well below 0.35.
        confidence = round(
            min(0.35, 0.03 + 0.18 * addr_score + 0.09 * zone_name_conf + 0.05 * base), 2
        )
        method = "deterministic+uid_mismatch"

    elif name_address_verified:
        # All name tokens + full address verified — solid without UID.
        confidence = round(min(0.90, 0.70 + 0.20 * base), 2)
        method = "deterministic+name_address_verified"

    else:
        # General unverified case.
        # Address carries the most weight (55%) because it's hard to fake incidentally.
        # Zone-weighted name (35%) rewards domain/title matches and penalises
        # body-text-only generic matches. Coverage is the residual 10%.
        #
        # Example scores (base ≈ 0.71):
        #   Domain match + full address → 0.55 + 0.35 + 0.07 → capped 0.75
        #   Full address only           → 0.55 + 0 + 0.07   → 0.62
        #   Domain name only            → 0 + 0.35 + 0.07   → 0.42
        #   Body-only generic match     → 0 + 0.04 + 0.07   → 0.11
        confidence = round(
            min(0.75, 0.55 * addr_score + 0.35 * zone_name_conf + 0.10 * base), 2
        )
        method = "deterministic"
        if addr_full_match:
            method += "+address_verified"
        elif addr_partial_match:
            method += "+address_partial"
        elif zone_name_conf >= 0.40:
            method += "+name_match"

    return {
        "emails": ranked_emails[:20] or None,
        "phones": sorted(phones)[:20] or None,
        "socials": socials or None,
        "uid": uid,
        "uid_matches_zefix": uid_matches,
        "name_address_verified": name_address_verified,
        "address": address,
        "persons": persons or None,
        "languages": sorted(languages) or None,
        "description": description,
        "service_keywords": service_keywords or None,
        "extraction_method": method,
        "confidence": confidence,
        "page_count": len(pages),
    }
