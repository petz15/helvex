"""Scoring logic for matching Google Search results to a company profile."""

import json
import logging
import math
import re
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger(__name__)


def web_score_from_extract(
    confidence: float | None,
    uid_matches_zefix: bool | None = None,
) -> int | None:
    """Derive web_score directly from crawl-extract confidence (0–1 → 0–100).

    The extractor's confidence already integrates UID match, address, zone-weighted
    name, and signal coverage — so this single number is more principled than the
    old delta approach. A UID mismatch overrides to a low floor (the page belongs to
    a different company; the crawl should be rejected, not just penalised).
    """
    if confidence is None:
        return None
    if uid_matches_zefix is False:
        return min(20, round(confidence * 100))
    return round(confidence * 100)


def adjust_web_score_for_extraction(
    base_web_score: int | float | None,
    *,
    uid_matches_zefix: bool | None,
    name_address_verified: bool,
) -> int | None:
    """Deprecated: use web_score_from_extract() for post-crawl scoring.

    Kept for backward-compatibility with rescore paths that only have the
    search-snippet score and 2 extract bits (no full confidence value).
    """
    if base_web_score is None:
        return None
    delta = 0
    if uid_matches_zefix is True:
        delta += 40
    elif uid_matches_zefix is False:
        delta -= 50
    if name_address_verified:
        delta += 20
    return max(0, min(100, round(base_web_score) + delta))


# Domains that are business directories or government registries — never crawl these
# as company websites; they show aggregated data, not the company's own site.
_DIRECTORY_DOMAINS = {
    "wikipedia.org",
    "zefix.admin.ch",
    "uid.admin.ch",
    "moneyhouse.ch",
    "shab.ch",
    "search.ch",
    "yelp.com",
    "local.ch",
    "yellowpages.ch",
    "yellowpages.swiss",
    "yellowpages.com",
    "directories.ch",
    "scout24.ch",
    "homegate.ch",
    "flatfox.ch",
    "newhome.ch",
    "immoscout24.ch",
    "immowelt.ch",
    "companyhouse.ch",
    "handelsregister.ch",
    "hr-register.ch",
    "rocketreach.co",
    "rocketreach.com",
    "kununu.com",
    "crunchbase.com",
    "tiger.ch",
    "help.ch",
    "kompass.ch",
    "kompass.com",
    "spheriq.ch",
    "treuhandsuisse.ch",
    "treuhandsuisse-zh.ch",
    "treuhandvergleich.ch",
    "consultingvergleich.ch",
    "fiduciairesuisse-vd.ch",
    "business-monitor.ch",
    "graph.swiss",
    "swiss-arc.ch",
    "northdata.com",
    "northdata.de",
    "northdata.eu",
    "northdata.ch",
    "provenexpert.com",
    "bestatter1.ch",
    "die-bestatter.ch",
    "auditorstats.ch",
    "maptons.com",
    "pappers.ch",
    "kanzleiwelten.com",
    "lixt.com",
    "swissbiotech.org",
    "ofri.ch",
    "region-emmental.ch",
    "bloomberg.com",
    "yandex.ru",
    "autolina.ch",
    "autoscout24.ch",
    "comparis.ch",
    "admin.ch",
    "sogenda.ch",
    "ccis.ch",
    "konsumentenschutz.ch",
    "konsumentenbewertung.ch",
    "psychologie.ch",
    "startups.ch",
    "gr-firmen.ch",
    "firma.ch",
    "firmenguru.ch",
    "promove.ch",
    "jobup.ch",
    "jobscout24.ch",
    "emplois-fribourg.ch",
    "jobs.ch",
}

# Social-media domains — we extract social profiles from crawl data already;
# crawling these as if they were a company website gives misleading results.
_SOCIAL_DOMAINS = {
    "linkedin.com",
    "xing.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "pinterest.com",
}

# Combined set of domains that should never be used as crawl targets.
CRAWL_BLOCKED_DOMAINS: frozenset[str] = frozenset(_DIRECTORY_DOMAINS | _SOCIAL_DOMAINS)


def get_default_directory_domains() -> set[str]:
    """Return a copy of built-in directory domains excluded in Google scoring."""
    return set(_DIRECTORY_DOMAINS)

_NEWS_DOMAINS = {
    "news.google.com",
    "20min.ch",
    "gastrojournal.ch",
    "nzz.ch",
    "srf.ch",
    "swissinfo.ch",
    "blick.ch",
    "aargauerzeitung.ch",
    "bernerzeitung.ch",
    "derbund.ch",
    "tagesanzeiger.ch",
    "luzernerzeitung.ch",
    "stgallerzeitung.ch",
    "suedostschweiz.ch",
    "nau.ch",
    "watson.ch",
}

_SOCIAL_LEAD_DOMAINS = {
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "xing.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
}

# Legal form words excluded when matching company name against domain
_LEGAL_FORM_WORDS = {"ag", "gmbh", "sa", "sarl", "sàrl", "kg", "og", "llc", "ltd", "inc", "co", "spa"}

# URL path pattern for municipal/local company directories (not in _DIRECTORY_DOMAINS).
# Matches German /verzeichnis/, French /membres/, /annuaire/, /repertoire/, Italian /elenco/.
_LOCAL_DIRECTORY_PATH_RE = re.compile(
    r"(?:unternehmens|firmen|branchen|betriebs)?verzeichnis"
    r"|/membres/"          # FR: association members listing
    r"|/annuaire/"         # FR: business directory
    r"|/repertoire/"       # FR: repertoire/listing
    r"|/bottin/"           # FR: business directory
    r"|/elenco-aziende/"   # IT: company listing
    r"|/aziende/",         # IT: companies listing
    re.IGNORECASE,
)

# Phrases that ONLY appear on directory listing pages (never on a company's own site).
# Matched against lowercased decoded HTML.
_DIRECTORY_CLAIM_PHRASES: tuple[str, ...] = (
    "sind sie inhaber",
    "sind sie der inhaber",
    "inhaber dieses eintrags",
    "eintrag beanspruchen",
    "als inhaber registrieren",
    "als inhaber anmelden",
    "unternehmen beanspruchen",
    "claim this listing",
    "claim your listing",
    "is this your business",
    "revendiquer cette fiche",
    "êtes-vous le propriétaire",
    "revendiquer cet établissement",
)

# "Similar companies" blocks appear in the sidebar of virtually every directory listing.
_DIRECTORY_SIMILAR_PHRASES: tuple[str, ...] = (
    "ähnliche unternehmen",
    "ähnliche firmen",
    "similar companies",
    "entreprises similaires",
    "aziende simili",
)

# Title-tag suffix patterns: "Company Name | Branchenbuch" or "Company | Vergleich"
_DIRECTORY_TITLE_SUFFIX_RE = re.compile(
    r"[|\-–]\s*(?:branchenbuch|verzeichnis|vergleich|eintrag|annuaire|repertoire|répertoire|registre|registro)",
    re.IGNORECASE,
)

# Words to exclude when extracting keywords from the purpose field
_STOPWORDS = {
    "die", "der", "das", "und", "oder", "mit", "von", "für", "des", "dem",
    "den", "ein", "eine", "einer", "eines", "sich", "auf", "zu", "ist",
    "sowie", "als", "auch", "nicht", "nach", "bei", "alle", "durch", "wird",
    "the", "and", "of", "in", "for", "to", "a", "an", "with", "its",
    "gesellschaft", "unternehmen", "betrieb", "zweck", "aktien", "gmbh",
}


def get_default_google_stopwords() -> set[str]:
    """Return a copy of built-in stopwords used for purpose keyword extraction."""
    return set(_STOPWORDS)


_URL_EXCLUDE_KEYWORDS: tuple[str, ...] = ()
_URL_EXCLUDE_KEYWORDS_RAW: str | None = None

# Built-in global URL exclusions (case-insensitive substring match). This list is
# always applied, and `GOOGLE_URL_EXCLUDE_KEYWORDS` extends it.
_DEFAULT_URL_EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "treuhandsuisse",
    "northdata",
    "kununu",
)


def _get_url_exclude_keywords() -> tuple[str, ...]:
    """Return normalized, lowercase keywords from settings.google_url_exclude_keywords.

    Config format: comma-separated list (case-insensitive substring match; NOT regex).
    The configured list is appended to a built-in default list.

    Example value (starts with treuhandsuisse):
      "treuhandsuisse, jobs, karriere, /careers, /stellen, /job/, /vacancies, /support, /kontakt"

    Notes:
      - Each entry is matched as a substring against the full URL.
      - If any keyword matches, the URL is always excluded (score 0 / irrelevant).

    Cached with simple globals to allow runtime override in tests.
    """
    global _URL_EXCLUDE_KEYWORDS, _URL_EXCLUDE_KEYWORDS_RAW

    raw = (settings.google_url_exclude_keywords or "").strip()
    if not raw:
        _URL_EXCLUDE_KEYWORDS = _DEFAULT_URL_EXCLUDE_KEYWORDS
        _URL_EXCLUDE_KEYWORDS_RAW = ""
        return _URL_EXCLUDE_KEYWORDS

    if raw == _URL_EXCLUDE_KEYWORDS_RAW:
        return _URL_EXCLUDE_KEYWORDS

    # Start with defaults, then extend with configured list.
    seen: set[str] = set()
    keywords: list[str] = []
    for kw in _DEFAULT_URL_EXCLUDE_KEYWORDS:
        kw_norm = (kw or "").strip().lower()
        if kw_norm and kw_norm not in seen:
            seen.add(kw_norm)
            keywords.append(kw_norm)

    for part in raw.split(","):
        kw_norm = part.strip().lower()
        if kw_norm and kw_norm not in seen:
            seen.add(kw_norm)
            keywords.append(kw_norm)

    _URL_EXCLUDE_KEYWORDS = tuple(keywords)
    _URL_EXCLUDE_KEYWORDS_RAW = raw
    return _URL_EXCLUDE_KEYWORDS


def _url_is_globally_excluded(url: str) -> bool:
    keywords = _get_url_exclude_keywords()
    if not keywords:
        return False
    url_lc = (url or "").lower()
    if not url_lc:
        return False
    return any(kw in url_lc for kw in keywords)


def _is_directory_domain(domain: str, directory_domains: set[str] | None = None) -> bool:
    # Always include the hardcoded baseline; DB overrides are additive, not a replacement.
    domains = _DIRECTORY_DOMAINS | (directory_domains or set())
    return any(domain == d or domain.endswith("." + d) for d in domains)


def _is_news_domain(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in _NEWS_DOMAINS)


def _word_overlap_ratio(a: str, b: str) -> float:
    """Fraction of words in *a* that appear in *b* (case-insensitive)."""
    words_a = set(re.findall(r"\w+", a.lower()))
    words_b = set(re.findall(r"\w+", b.lower()))
    if not words_a:
        return 0.0
    return len(words_a & words_b) / len(words_a)


def _domain_is_exact_name_match(domain: str, company_name: str) -> bool:
    """Return True when the domain base name (sans TLD) exactly matches
    the company name stripped of legal-form words and hyphens.

        "Muster AG"       + "muster.ch"       → True
        "Bau Schmidt GmbH" + "bau-schmidt.ch"  → True
        "Muster AG"       + "muster-solutions.ch" → False
    """
    if domain.endswith(".swiss"):
        base = domain[:-6]
    elif domain.endswith(".ch"):
        base = domain[:-3]
    else:
        return False

    domain_norm = re.sub(r"[-.]", "", base)

    name_words = [
        w for w in re.findall(r"\w+", company_name.lower())
        if len(w) >= 3 and w not in _LEGAL_FORM_WORDS
    ]
    if not name_words:
        return False

    # Full match: all meaningful words concatenated equal the domain base ("bau-schmidt.ch" → "bauers")
    if domain_norm == "".join(name_words):
        return True
    # Partial match: domain base equals the first meaningful word alone ("symbiont.ch" for "Symbiont Consulting GmbH")
    return len(name_words) > 1 and domain_norm == name_words[0]


def _domain_name_overlap(domain: str, company_name: str) -> float:
    """Fraction of meaningful company name words found in the domain string.

    Uses substring containment so concatenated domains match correctly:
    "aarestadt" and "gastro" are both substrings of "aarestadtgastro.ch" → 1.0.
    Strips legal form suffixes (AG, GmbH, …) and short tokens before comparing.
    """
    domain_lower = domain.lower()
    name_words = [
        w for w in re.findall(r"\w+", company_name.lower())
        if len(w) >= 3 and w not in _LEGAL_FORM_WORDS
    ]
    if not name_words:
        return 0.0
    hits = sum(1 for w in name_words if w in domain_lower)
    return hits / len(name_words)


def _root_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc.removeprefix("www.")
    except Exception:
        return ""


def _purpose_keywords(
    purpose: str | None,
    max_keywords: int = 8,
    stopwords: set[str] | None = None,
) -> list[str]:
    """Extract meaningful content words from a company's purpose text."""
    if not purpose:
        return []
    words = re.findall(r"\b[a-zA-ZäöüÄÖÜ]{4,}\b", purpose.lower())
    stopword_set = stopwords or _STOPWORDS
    return [w for w in words if w not in stopword_set][:max_keywords]


def _extract_address_parts(address: str) -> tuple[str | None, str | None]:
    """Return (zip_code, street_name) extracted from a Swiss address string.

    zip_code  — first 4-digit sequence found (e.g. "3001")
    street_name — leading alphabetic word(s) before the first street number,
                  lowercased (e.g. "musterstrasse", "rue du moulin").
    Returns None for each part that cannot be extracted.
    """
    zip_code = None
    street_name = None

    zip_match = re.search(r"\b(\d{4})\b", address)
    if zip_match:
        zip_code = zip_match.group(1)

    # Street name: take the run of non-digit words at the start of the address
    # (stops at the first digit, e.g. house number)
    street_match = re.match(r"^([^\d,]+?)(?:\s+\d|\s*,|$)", address.strip())
    if street_match:
        candidate = street_match.group(1).strip().lower()
        if len(candidate) >= 5:  # ignore very short tokens (noise)
            street_name = candidate

    return zip_code, street_name


# ── score_result weights ──────────────────────────────────────────────────────
_W_NAME_TITLE = 30       # max pts: company name match in result title
_W_NAME_SNIPPET = 20     # max pts: company name match in snippet
_W_DOMAIN_OVERLAP = 15   # max pts: domain words overlap with company name
_W_MUNICIPALITY = 25     # pts: municipality found in combined text
_W_CANTON = 10           # pts: canton abbreviation found
_W_ZIP = 15              # pts: zip code found
_W_STREET = 15           # pts: street name found
_W_KEYWORDS_HIGH = 15    # pts: 3+ purpose keywords in snippet
_W_KEYWORDS_LOW = 8      # pts: 1-2 purpose keywords in snippet
_W_LEGAL_FORM = 5        # pts: legal form abbreviation in domain/title
_W_SWISS_TLD = 10        # pts: .ch / .swiss TLD bonus
_W_EXACT_NAME = 15       # pts: exact company name matches domain base
_POS_BONUS = (30, 20, 15, 10)  # pts by Google result position 0–3
_W_SOCIAL_PENALTY = -30  # pts: social media domain penalty


def score_result(
    result: dict,
    *,
    company_name: str,
    municipality: str | None,
    canton: str | None,
    purpose: str | None = None,
    legal_form: str | None = None,
    address: str | None = None,
    directory_domains: set[str] | None = None,
    purpose_stopwords: set[str] | None = None,
    position: int = 0,
) -> int:
    """Score a single Google search result against a company profile (0-100).

    Weight constants are defined at module level (_W_* and _POS_BONUS).
    """
    title = result.get("title", "") or ""
    snippet = result.get("snippet", "") or ""
    link = result.get("link", "") or ""

    if not link:
        return 0

    # Global exclusion (e.g. jobs/careers/support pages) always wins.
    if _url_is_globally_excluded(link):
        return 0

    # --- Directory / news domain → always 0, no further scoring ---
    domain = _root_domain(link)
    if _is_news_domain(domain):
        return 0
    if _is_directory_domain(domain, directory_domains):
        return 0

    # --- Local directory URL path → always 0 (e.g. /unternehmensverzeichnis/) ---
    if _LOCAL_DIRECTORY_PATH_RE.search(link):
        return 0

    combined_lower = f"{title} {snippet}".lower()
    snippet_lower = snippet.lower()

    # --- Name in title ---
    score = int(_word_overlap_ratio(company_name, title) * _W_NAME_TITLE)

    # --- Name in snippet ---
    score += int(_word_overlap_ratio(company_name, snippet) * _W_NAME_SNIPPET)

    # --- Domain name matches company name ---
    score += int(_domain_name_overlap(domain, company_name) * _W_DOMAIN_OVERLAP)

    # --- Location match ---
    if municipality and municipality.lower() in combined_lower:
        score += _W_MUNICIPALITY
    if canton and canton.upper() in f"{title} {snippet}".upper():
        score += _W_CANTON
    if address:
        zip_code, street_name = _extract_address_parts(address)
        if zip_code and zip_code in f"{title} {snippet}":
            score += _W_ZIP
        if street_name and street_name in combined_lower:
            score += _W_STREET

    # --- Purpose keywords in snippet ---
    keywords = _purpose_keywords(purpose, stopwords=purpose_stopwords)
    if keywords:
        hits = sum(1 for kw in keywords if kw in snippet_lower)
        if hits >= 3:
            score += _W_KEYWORDS_HIGH
        elif hits >= 1:
            score += _W_KEYWORDS_LOW

    # --- Legal form presence in domain or title ---
    if legal_form:
        lf_lower = legal_form.lower()
        abbrevs = re.findall(r"\b\w{2,6}\b", lf_lower)
        if any(a in domain or a in title.lower() for a in abbrevs if len(a) >= 2):
            score += _W_LEGAL_FORM

    # --- Swiss TLD bonus (+extra when domain base matches company name exactly) ---
    if domain.endswith(".ch") or domain.endswith(".swiss"):
        score += _W_SWISS_TLD
        if _domain_is_exact_name_match(domain, company_name):
            score += _W_EXACT_NAME

    # --- Google rank bonus ---
    score += _POS_BONUS[position] if position < len(_POS_BONUS) else 0

    # --- Social media penalty ---
    if any(domain == d or domain.endswith("." + d) for d in _SOCIAL_LEAD_DOMAINS):
        score += _W_SOCIAL_PENALTY

    return max(0, min(100, score))


def is_irrelevant_result(
    result: dict,
    *,
    company_name: str,
    directory_domains: set[str] | None = None,
) -> bool:
    """Return True when a search result is likely not the company's own website.

    Heuristics:
      - Directory/social/government registry domain, or
      - Very low company-name overlap in both title and snippet.
    """
    title = result.get("title", "") or ""
    snippet = result.get("snippet", "") or ""
    link = result.get("link", "") or ""

    if not link:
        return True

    if _url_is_globally_excluded(link):
        return True

    domain = _root_domain(link)
    if _is_news_domain(domain):
        return True
    if _is_directory_domain(domain, directory_domains):
        return True

    title_overlap = _word_overlap_ratio(company_name, title)
    snippet_overlap = _word_overlap_ratio(company_name, snippet)
    return title_overlap < 0.2 and snippet_overlap < 0.2


def fallback_result_score(
    result: dict,
    *,
    municipality: str | None,
    canton: str | None,
    legal_form: str | None = None,
    address: str | None = None,
    directory_domains: set[str] | None = None,
) -> int:
    """Fallback website score used when top results are mostly irrelevant.

    Formula: base 5 + location (municipality/canton/zip/street) + legal-form presence.
    """
    title = result.get("title", "") or ""
    snippet = result.get("snippet", "") or ""
    link = result.get("link", "") or ""

    if not link:
        return 0

    if _url_is_globally_excluded(link):
        return 0

    # Directory / news domains must never be selected as the company website
    domain = _root_domain(link)
    if _is_news_domain(domain):
        return 0
    if _is_directory_domain(domain, directory_domains):
        return 0

    combined = f"{title} {snippet}"
    combined_lower = combined.lower()

    score = 5

    if municipality and municipality.lower() in combined_lower:
        score += 25
    if canton and canton.upper() in combined.upper():
        score += 10
    if address:
        zip_code, street_name = _extract_address_parts(address)
        if zip_code and zip_code in combined:
            score += 15
        if street_name and street_name in combined_lower:
            score += 15

    if legal_form:
        lf_lower = legal_form.lower()
        abbrevs = re.findall(r"\b\w{2,6}\b", lf_lower)
        if any(a in domain or a in title.lower() for a in abbrevs if len(a) >= 2):
            score += 5

    return max(0, min(100, score))


def is_social_lead_domain(url: str) -> bool:
    """Return True when URL belongs to a social domain treated as high lead value."""
    domain = _root_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in _SOCIAL_LEAD_DOMAINS)


def classify_domain(url: str, directory_domains: set[str] | None = None) -> str:
    """Bucket a result URL by domain type for website-presence classification.

    Returns one of: "own" | "social" | "directory" | "news" | "none".
    "own" means a candidate company-owned website (not a directory/social/news
    aggregator). Used by app.services.website_status to decide whether a company
    actually has its own website versus only a social profile or directory listing.
    """
    domain = _root_domain(url)
    if not domain:
        return "none"
    if is_social_lead_domain(url):
        return "social"
    if _is_news_domain(domain):
        return "news"
    if _is_directory_domain(domain, directory_domains):
        return "directory"
    if _LOCAL_DIRECTORY_PATH_RE.search(url) or _url_is_globally_excluded(url):
        return "directory"
    return "own"


# Points lost per organic rank below #1, per ad shown above the result, and per
# competing SERP feature (local pack / knowledge graph) pushing it further down.
_SEO_RANK_PENALTY = 8
_SEO_AD_PENALTY = 12
_SEO_FEATURE_PENALTY = 5


def find_organic_position(results: list[dict], url: str | None) -> int | None:
    """1-based organic rank of `url`'s domain within stored Google results.

    `results` is the google_search_results_raw list (each row has a 0-based `position`).
    Returns None when the domain isn't present or has no recorded position.
    """
    if not url:
        return None
    domain = _root_domain(url)
    if not domain:
        return None
    has_positions = any(r.get("position") is not None for r in results)
    ordered = sorted(results, key=lambda r: r.get("position", 9999)) if has_positions else results
    for r in ordered:
        pos = r.get("position")
        if pos is not None and _root_domain(r.get("link", "")) == domain:
            return pos + 1
    return None


def extract_serp_features(google_search_full_raw: str | None) -> tuple[int, bool, bool]:
    """Return (ads_count, has_local_pack, has_knowledge_graph) from stored provider JSON.

    Handles both Serper (ads/places/knowledgeGraph) and ScrapingDog
    (paid_results/local_results/knowledge_graph) field naming.
    """
    if not google_search_full_raw:
        return 0, False, False
    try:
        full = json.loads(google_search_full_raw)
    except (json.JSONDecodeError, TypeError):
        return 0, False, False
    ads = full.get("ads") or full.get("paid_results") or []
    ads_count = len(ads) if isinstance(ads, list) else 0
    local = full.get("places") or full.get("local_results") or []
    has_local_pack = bool(local) if isinstance(local, list) else False
    has_knowledge_graph = bool(full.get("knowledgeGraph") or full.get("knowledge_graph"))
    return ads_count, has_local_pack, has_knowledge_graph


def compute_seo_visibility_score(
    organic_position: int | None,
    *,
    ads_count: int = 0,
    has_local_pack: bool = False,
    has_knowledge_graph: bool = False,
) -> int | None:
    """SEO visibility (0-100): how findable the company's own site actually is in Google.

    Distinct from web_score (URL-selection confidence). This measures the real-world
    search result page: a #1 organic rank buried under 3 ads is not great visibility.

    organic_position is 1-based (1 = top organic result). Returns None when the
    company's own site was not found in the organic results at all.
    """
    if organic_position is None or organic_position < 1:
        return None
    score = 100 - (organic_position - 1) * _SEO_RANK_PENALTY - ads_count * _SEO_AD_PENALTY
    if has_local_pack:
        score -= _SEO_FEATURE_PENALTY
    if has_knowledge_graph:
        score -= _SEO_FEATURE_PENALTY
    return max(0, min(100, score))


def is_directory_page(html: bytes, url: str) -> bool:
    """Return True when fetched HTML is a business directory listing, not the company's own site.

    Checks URL path patterns and three tiers of HTML signals without full DOM parsing.
    Called at crawl-extraction time so bad URL candidates can be rejected before storage.
    """
    if _LOCAL_DIRECTORY_PATH_RE.search(url):
        return True

    try:
        text_lower = html.decode("utf-8", errors="replace").lower()
    except Exception:  # noqa: BLE001
        return False

    # Tier 1 — claim-listing phrases: near-zero false-positive rate
    if any(phrase in text_lower for phrase in _DIRECTORY_CLAIM_PHRASES):
        return True

    # Tier 2 — title tag contains directory keyword after a separator
    m = re.search(r"<title[^>]*>([^<]{5,300})</title>", text_lower)
    if m and _DIRECTORY_TITLE_SUFFIX_RE.search(m.group(1)):
        return True

    # Tier 3 — "similar companies" sidebar present (ubiquitous on directory listing pages)
    if any(phrase in text_lower for phrase in _DIRECTORY_SIMILAR_PHRASES):
        return True

    return False


# ── Location helpers ────────────────────────────────────────────────────────
# Default origin: Muri bei Bern (lat, lon) — used for distance_to_muri_km helper
_ORIGIN = (46.9266, 7.4817)

# Approximate coordinates of canton capitals — fallback when municipality not found
_CANTON_COORDS: dict[str, tuple[float, float]] = {
    "AG": (47.391, 8.044), "AI": (47.331, 9.410), "AR": (47.388, 9.275),
    "BE": (46.948, 7.447), "BL": (47.485, 7.736), "BS": (47.560, 7.589),
    "FR": (46.807, 7.162), "GE": (46.204, 6.143), "GL": (47.040, 9.068),
    "GR": (46.850, 9.533), "JU": (47.366, 7.344), "LU": (47.050, 8.309),
    "NE": (47.000, 6.933), "NW": (46.958, 8.366), "OW": (46.897, 8.247),
    "SG": (47.424, 9.377), "SH": (47.696, 8.634), "SO": (47.209, 7.538),
    "SZ": (47.021, 8.651), "TG": (47.558, 8.897), "TI": (46.004, 8.951),
    "UR": (46.881, 8.645), "VD": (46.520, 6.632), "VS": (46.232, 7.360),
    "ZG": (47.166, 8.515), "ZH": (47.377, 8.542),
}

# Key Swiss municipalities → (lat, lon).  Lower-cased for lookup.
_MUNICIPALITY_COORDS: dict[str, tuple[float, float]] = {
    "muri bei bern": (46.927, 7.482), "bern": (46.948, 7.447),
    "köniz": (46.921, 7.410), "ostermundigen": (46.957, 7.494),
    "ittigen": (46.974, 7.481), "worb": (46.928, 7.565),
    "münsingen": (46.874, 7.564), "belp": (46.891, 7.497),
    "biel": (47.137, 7.247), "biel/bienne": (47.137, 7.247), "bienne": (47.137, 7.247),
    "thun": (46.758, 7.629), "interlaken": (46.686, 7.863),
    "solothurn": (47.209, 7.538), "olten": (47.352, 7.903), "grenchen": (47.193, 7.396),
    "aarau": (47.391, 8.044), "baden": (47.473, 8.306), "brugg": (47.484, 8.209),
    "wettingen": (47.467, 8.319), "rheinfelden": (47.559, 7.795),
    "liestal": (47.485, 7.736), "pratteln": (47.517, 7.693),
    "binningen": (47.536, 7.568), "reinach": (47.497, 7.590),
    "basel": (47.560, 7.589), "münchenbuchsee": (47.022, 7.456),
    "luzern": (47.050, 8.309), "lucerne": (47.050, 8.309),
    "kriens": (47.032, 8.281), "emmen": (47.075, 8.292),
    "zürich": (47.377, 8.542), "zurich": (47.377, 8.542),
    "winterthur": (47.501, 8.724), "uster": (47.349, 8.720),
    "dübendorf": (47.397, 8.618), "kloten": (47.450, 8.584),
    "dietikon": (47.403, 8.401), "horgen": (47.258, 8.597),
    "zug": (47.166, 8.515), "baar": (47.196, 8.527),
    "fribourg": (46.807, 7.162), "freiburg": (46.807, 7.162),
    "neuchâtel": (47.000, 6.933), "neuenburg": (47.000, 6.933),
    "delémont": (47.366, 7.344),
    "lausanne": (46.520, 6.632), "genève": (46.204, 6.143),
    "geneva": (46.204, 6.143), "genf": (46.204, 6.143),
    "sion": (46.232, 7.360), "sitten": (46.232, 7.360),
    "lugano": (46.004, 8.951), "bellinzona": (46.196, 9.024),
    "st. gallen": (47.424, 9.377), "schaffhausen": (47.696, 8.634),
    "frauenfeld": (47.558, 8.897), "chur": (46.850, 9.533),
    "schwyz": (47.021, 8.651), "altdorf": (46.881, 8.645),
    "stans": (46.958, 8.366), "sarnen": (46.897, 8.247),
    "glarus": (47.040, 9.068), "herisau": (47.388, 9.275),
    "appenzell": (47.331, 9.410),
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _resolve_coords(
    canton: str | None,
    municipality: str | None,
    lat: float | None,
    lon: float | None,
) -> tuple[float, float] | None:
    if lat is not None and lon is not None:
        return (lat, lon)
    coords: tuple[float, float] | None = None
    if municipality:
        coords = _MUNICIPALITY_COORDS.get(municipality.lower())
    if coords is None and canton:
        coords = _CANTON_COORDS.get(canton.upper())
    return coords


def _distance_score(
    origin_lat: float,
    origin_lon: float,
    canton: str | None,
    municipality: str | None,
    lat: float | None,
    lon: float | None,
    config: "dict[str, str] | None",
) -> int:
    """Return distance-based score using configurable tier thresholds."""
    coords = _resolve_coords(canton, municipality, lat, lon)
    if coords is None:
        return 0
    dist = _haversine_km(origin_lat, origin_lon, coords[0], coords[1])
    if dist <= 15:
        return _cfg_int(config, "scoring_dist_15km", 20)
    elif dist <= 40:
        return _cfg_int(config, "scoring_dist_40km", 10)
    elif dist <= 80:
        return _cfg_int(config, "scoring_dist_80km", 5)
    elif dist <= 130:
        return _cfg_int(config, "scoring_dist_130km", 0)
    else:
        return _cfg_int(config, "scoring_dist_far", -5)


def distance_to_muri_km(
    *,
    canton: str | None,
    municipality: str | None,
    lat: float | None = None,
    lon: float | None = None,
) -> float | None:
    """Return distance in km to Muri bei Bern (used for batch ordering)."""
    coords = _resolve_coords(canton, municipality, lat, lon)
    return _haversine_km(_ORIGIN[0], _ORIGIN[1], coords[0], coords[1]) if coords else None


def distance_to_origin_km(
    origin_lat: float,
    origin_lon: float,
    *,
    canton: str | None,
    municipality: str | None,
    lat: float | None = None,
    lon: float | None = None,
) -> float | None:
    """Return distance in km from a configurable origin to the company's resolved coordinates."""
    coords = _resolve_coords(canton, municipality, lat, lon)
    return _haversine_km(origin_lat, origin_lon, coords[0], coords[1]) if coords else None


# ── Individual scoring ───────────────────────────────────────────────────────

_DEFAULT_SCORING_CONFIG: dict[str, str] = {
    # Comma-separated cluster label substrings — each match adds cluster_hit_points
    "scoring_target_clusters": "",
    "scoring_cluster_hit_points": "10",
    # Comma-separated cluster label substrings — each match subtracts cluster_exclude_points
    "scoring_exclude_clusters": "",
    "scoring_cluster_exclude_points": "10",
    # Comma-separated purpose keyword substrings — each match adds keyword_hit_points
    "scoring_target_keywords": "",
    "scoring_keyword_hit_points": "10",
    # Comma-separated purpose keyword substrings — each match subtracts keyword_exclude_points
    "scoring_exclude_keywords": "",
    "scoring_keyword_exclude_points": "10",
    # Distance tiers (haversine from configurable origin)
    "scoring_origin_lat": "46.9266",   # default: Muri bei Bern
    "scoring_origin_lon": "7.4817",
    "scoring_dist_15km":  "20",        # pts for ≤ 15 km
    "scoring_dist_40km":  "10",        # pts for ≤ 40 km
    "scoring_dist_80km":  "5",         # pts for ≤ 80 km
    "scoring_dist_130km": "0",         # pts for ≤ 130 km
    "scoring_dist_far":   "-5",        # pts for > 130 km
    # Legal form: "short_name:points" pairs, comma-separated (case-insensitive)
    "scoring_legal_form_scores": "gmbh:20,sarl:20,sàrl:20,einzelfirma:15,eg:15,kg:10,og:8,ag:8,sa:8,stiftung:3,verein:2",
    "scoring_legal_form_default": "5",
    # Fixed score for cancelled/dissolved companies (bypasses normalization)
    "scoring_cancelled_score": "5",
    # Data quality penalties
    "scoring_no_keywords_penalty": "10",       # deducted when purpose_keywords is empty
    "scoring_undefined_cluster_penalty": "10", # deducted when tfidf_cluster is undefined/missing
    # Claude input token optimisation
    "scoring_claude_max_purpose_chars": "800", # purpose text truncated to this many chars before sending to Claude
}

_CANCELLED_STATUS_TERMS = frozenset({"being_cancelled", "dissolved", "gelöscht", "radiation", "liquidation"})


def get_default_scoring_config() -> dict[str, str]:
    return dict(_DEFAULT_SCORING_CONFIG)


def _cfg_int(config: dict[str, str] | None, key: str, fallback: int) -> int:
    if not config:
        return fallback
    raw = config.get(key)
    if raw is None:
        return fallback
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback


def _cfg_float(config: dict[str, str] | None, key: str, fallback: float) -> float:
    if not config:
        return fallback
    raw = config.get(key)
    if raw is None:
        return fallback
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return fallback


def _cfg_terms(config: dict[str, str] | None, key: str, fallback: list[str]) -> list[str]:
    if not config:
        return fallback
    raw = (config.get(key) or "").strip()
    if not raw:
        return fallback
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def _parse_noga_targets(config: dict[str, str] | None) -> dict[str, int]:
    """Parse scoring_noga_targets setting into a {noga_code_or_section: points} dict.

    Format: pipe-separated "CODE:points" pairs, e.g. "J:25|64:30|641:35|M:15".
    More specific codes (longer strings) take priority over parent sections.
    """
    raw = (config or {}).get("scoring_noga_targets", "").strip()
    if not raw:
        return {}
    result: dict[str, int] = {}
    for part in raw.split("|"):
        part = part.strip()
        if ":" in part:
            code, _, pts_str = part.partition(":")
            code = code.strip().upper()
            try:
                result[code] = int(pts_str.strip())
            except ValueError:
                pass
    return result


def _noga_score(noga_path: str | None, noga_code: str | None, noga_level: str | None, targets: dict[str, int]) -> int:
    """Return the flex score contribution from NOGA classification.

    Walks the noga_path from root→leaf (e.g. "J|64|641|6419|64190") and picks
    the most specific (deepest) matching target code.  This means a target on
    division 64 can be overridden by a more specific target on group 641.
    """
    if not targets:
        return 0
    best = 0
    path_parts: list[str] = []
    if noga_path:
        path_parts = [p.strip().upper() for p in noga_path.split("|") if p.strip()]
    elif noga_code:
        path_parts = [noga_code.upper()]
    for part in path_parts:
        if part in targets:
            best = targets[part]  # later = more specific, overrides parent
    return best


def _parse_legal_form_scores(config: dict[str, str] | None) -> dict[str, int]:
    raw = (config or {}).get("scoring_legal_form_scores") or _DEFAULT_SCORING_CONFIG["scoring_legal_form_scores"]
    result: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" in part:
            key, _, val = part.partition(":")
            try:
                result[key.strip().lower()] = int(val.strip())
            except ValueError:
                pass
    return result


def _is_cancelled(status: str | None) -> bool:
    norm = (status or "").lower().replace("-", "_").replace(" ", "_")
    return any(t in norm for t in _CANCELLED_STATUS_TERMS)


def compute_flex_score_breakdown(
    *,
    legal_form: str | None,
    legal_form_short_name: str | None,
    status: str | None,
    canton: str | None = None,
    municipality: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    purpose_keywords: str | None = None,
    tfidf_cluster: str | None = None,
    noga_path: str | None = None,
    noga_code: str | None = None,
    noga_level: str | None = None,
    config: dict[str, str] | None = None,
    # Legacy params accepted but ignored (kept for backward-compat with old call sites)
    capital_nominal: str | None = None,
    purpose: str | None = None,
    branch_offices: str | None = None,
) -> dict:
    cancelled_score = _cfg_int(config, "scoring_cancelled_score", 5)

    breakdown: dict = {
        "clusters": 0,
        "keywords": 0,
        "noga": 0,
        "distance": 0,
        "legal_form": 0,
        "data_quality": 0,
        "raw_total": 0,
        "final_score": 0,
        "cancelled": False,
    }

    if _is_cancelled(status):
        breakdown["cancelled"] = True
        breakdown["final_score"] = cancelled_score
        return breakdown

    # ── Cluster hits ──────────────────────────────────────────────────────────
    target_clusters = _cfg_terms(config, "scoring_target_clusters", [])
    cluster_pts = _cfg_int(config, "scoring_cluster_hit_points", 10)
    exclude_clusters = _cfg_terms(config, "scoring_exclude_clusters", [])
    cluster_excl_pts = _cfg_int(config, "scoring_cluster_exclude_points", 10)
    if tfidf_cluster:
        cluster_lower = tfidf_cluster.lower()
        if target_clusters:
            hits = sum(1 for tc in target_clusters if tc in cluster_lower)
            breakdown["clusters"] += hits * cluster_pts
        if exclude_clusters:
            excl_hits = sum(1 for ec in exclude_clusters if ec in cluster_lower)
            breakdown["clusters"] -= excl_hits * cluster_excl_pts

    # ── Keyword hits / penalties ───────────────────────────────────────────────
    target_keywords = _cfg_terms(config, "scoring_target_keywords", [])
    kw_pts = _cfg_int(config, "scoring_keyword_hit_points", 10)
    exclude_keywords = _cfg_terms(config, "scoring_exclude_keywords", [])
    kw_excl_pts = _cfg_int(config, "scoring_keyword_exclude_points", 10)
    if purpose_keywords:
        kw_lower = purpose_keywords.lower()
        if target_keywords:
            hits = sum(1 for kw in target_keywords if kw in kw_lower)
            breakdown["keywords"] += hits * kw_pts
        if exclude_keywords:
            excl_hits = sum(1 for ek in exclude_keywords if ek in kw_lower)
            breakdown["keywords"] -= excl_hits * kw_excl_pts

    # ── Data quality penalties ────────────────────────────────────────────────
    no_kw_penalty = _cfg_int(config, "scoring_no_keywords_penalty", 10)
    undef_cluster_penalty = _cfg_int(config, "scoring_undefined_cluster_penalty", 10)
    if not purpose_keywords or not purpose_keywords.strip():
        breakdown["data_quality"] -= no_kw_penalty
    _undef_cluster_terms = {"undefined", "unbekannt", "unknown", "none", "other", "sonstige"}
    if not tfidf_cluster or tfidf_cluster.lower().strip() in _undef_cluster_terms:
        breakdown["data_quality"] -= undef_cluster_penalty

    # ── NOGA classification bonus ─────────────────────────────────────────────
    noga_targets = _parse_noga_targets(config)
    breakdown["noga"] = _noga_score(noga_path, noga_code, noga_level, noga_targets)

    # ── Distance ──────────────────────────────────────────────────────────────
    origin_lat = _cfg_float(config, "scoring_origin_lat", _ORIGIN[0])
    origin_lon = _cfg_float(config, "scoring_origin_lon", _ORIGIN[1])
    breakdown["distance"] = _distance_score(origin_lat, origin_lon, canton, municipality, lat, lon, config)

    # ── Legal form ────────────────────────────────────────────────────────────
    lf_scores = _parse_legal_form_scores(config)
    lf_default = _cfg_int(config, "scoring_legal_form_default", 5)
    lf_key = (legal_form_short_name or legal_form or "").lower().strip()
    breakdown["legal_form"] = lf_scores.get(lf_key, lf_default) if lf_key else lf_default

    raw = (
        int(breakdown["clusters"])
        + int(breakdown["keywords"])
        + int(breakdown["noga"])
        + int(breakdown["distance"])
        + int(breakdown["legal_form"])
        + int(breakdown["data_quality"])
    )
    breakdown["raw_total"] = raw
    # Clamped to 0-100 for real-time use; recalculate job normalises properly
    breakdown["final_score"] = max(0, min(100, raw))
    return breakdown


def compute_flex_score(
    *,
    legal_form: str | None,
    legal_form_short_name: str | None,
    status: str | None,
    canton: str | None = None,
    municipality: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    purpose_keywords: str | None = None,
    tfidf_cluster: str | None = None,
    noga_path: str | None = None,
    noga_code: str | None = None,
    noga_level: str | None = None,
    config: dict[str, str] | None = None,
    # Legacy compat
    capital_nominal: str | None = None,
    purpose: str | None = None,
    branch_offices: str | None = None,
) -> int:
    return int(compute_flex_score_breakdown(
        legal_form=legal_form,
        legal_form_short_name=legal_form_short_name,
        status=status,
        canton=canton,
        municipality=municipality,
        lat=lat,
        lon=lon,
        purpose_keywords=purpose_keywords,
        tfidf_cluster=tfidf_cluster,
        noga_path=noga_path,
        noga_code=noga_code,
        noga_level=noga_level,
        config=config,
    )["final_score"])


# Backward-compat API kept for tests and older call sites.
def compute_zefix_score_breakdown(**kwargs) -> dict:
    return compute_flex_score_breakdown(**kwargs)


def compute_zefix_score(**kwargs) -> int:
    return compute_flex_score(**kwargs)


def compute_relevance_score(company) -> float | None:
    """Relevance score formula incorporating all available signals.

    Base formula (no web_score): ai×0.60 + noga_confidence×100×0.25 + keyword_density×100×0.15
    With web_score:               ai×0.50 + web_score×0.20 + noga_confidence×100×0.20 + keyword_density×100×0.10

    Any absent component's weight is redistributed proportionally among the rest.
    Returns None when all components are absent.
    """
    ai = company.ai_score
    noga_conf = company.noga_confidence  # float 0-1
    purpose_kw = company.purpose_keywords  # comma-separated string or None
    web = getattr(company, "web_score", None)  # 0-100, None if not yet crawled

    # keyword_density: 10+ keywords → 1.0, 0 keywords → 0.0
    if purpose_kw and purpose_kw.strip():
        kw_count = purpose_kw.count(",") + 1
        kw_density = min(kw_count, 10) / 10.0
    else:
        kw_density = 0.0

    noga_score = float(noga_conf) * 100.0 if noga_conf is not None else None
    web_score = float(web) if web is not None else None

    if web_score is not None:
        # Extended formula including web_score
        components = [
            (float(ai) if ai is not None else None, 0.50),
            (web_score, 0.20),
            (noga_score, 0.20),
            (kw_density * 100.0 if kw_density > 0.0 else None, 0.10),
        ]
    else:
        # Original formula without web_score
        components = [
            (float(ai) if ai is not None else None, 0.60),
            (noga_score, 0.25),
            (kw_density * 100.0 if kw_density > 0.0 else None, 0.15),
        ]

    present = [(v, w) for v, w in components if v is not None]
    if not present:
        return None
    total_w = sum(w for _, w in present)
    if total_w == 0.0:
        return None
    val = sum(v * w for v, w in present) / total_w
    return round(max(0.0, min(100.0, val)), 1)


def normalize_raw_scores(
    raw_scores: dict[int, int | None],
    cancelled_score: int = 5,
) -> dict[int, int]:
    """Min-max normalize raw scores to 0-100. Cancelled (None) → cancelled_score."""
    non_cancelled = {cid: s for cid, s in raw_scores.items() if s is not None}
    result: dict[int, int] = {}
    if non_cancelled:
        min_s = min(non_cancelled.values())
        max_s = max(non_cancelled.values())
        for cid, raw in non_cancelled.items():
            result[cid] = round((raw - min_s) / (max_s - min_s) * 100) if max_s > min_s else 50
    cancelled_count = sum(1 for s in raw_scores.values() if s is None)
    for cid, raw in raw_scores.items():
        if raw is None:
            result[cid] = cancelled_score
    logger.debug(
        "normalize_raw_scores total=%d cancelled=%d min=%s max=%s",
        len(raw_scores),
        cancelled_count,
        min(non_cancelled.values()) if non_cancelled else None,
        max(non_cancelled.values()) if non_cancelled else None,
    )
    return result
