"""Company-level website verdict — does this company have a website, and how many?

Aggregates two evidence sources into one company-level verdict, with no API cost:

  1. URL search results (Serper / ScrapingDog), already scored per company in
     google_search_results_raw — used for a *provisional* verdict before any crawl.
  2. Per-candidate crawl-verification extracts (company_web_extract), which carry
     uid_matches_zefix / name_address_verified / confidence — the *authoritative*
     verdict once the company has been crawled.

This replaces the old "force scored[0] into website_url" behaviour: website_url is
only set when the verdict indicates a genuine own-domain match. Companies whose
search/crawl turns up only social profiles, directory listings, or nothing get a
negative verdict and a NULL website_url instead of a forced wrong guess.

Verdicts (most → least certain):
    verified        crawl found Swiss UID matching Zefix, or name+address verified
    confirmed       own-domain found with high extract/search confidence (no UID proof)
    likely          own-domain candidate exists, mid confidence
    social_only     no own-domain cleared the bar, but a social profile was found
    directory_only  only directory/registry listings (moneyhouse, local.ch, …)
    none            searched/crawled, nothing credible
    (NULL)          unknown — not yet searched/crawled
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import crud
from app.services import scoring

VERIFIED = "verified"
CONFIRMED = "confirmed"
LIKELY = "likely"
SOCIAL_ONLY = "social_only"
DIRECTORY_ONLY = "directory_only"
NONE = "none"

# Verdicts for which website_url should hold a real own-domain link.
POSITIVE = frozenset({VERIFIED, CONFIRMED, LIKELY})


@dataclass
class Thresholds:
    confirmed_conf: float
    likely_conf: float
    confirmed_score: int
    likely_score: int


def load_thresholds(db: Session) -> Thresholds:
    """Read website-verdict thresholds from AppSetting (DB-configurable)."""
    def _f(key: str, default: float) -> float:
        try:
            return float(crud.get_setting(db, key, str(default)) or default)
        except (TypeError, ValueError):
            return default

    def _i(key: str, default: int) -> int:
        try:
            return int(float(crud.get_setting(db, key, str(default)) or default))
        except (TypeError, ValueError):
            return default

    return Thresholds(
        confirmed_conf=_f("website_confirmed_confidence", 0.65),
        likely_conf=_f("website_likely_confidence", 0.45),
        confirmed_score=_i("website_confirmed_search_score", 55),
        likely_score=_i("website_likely_search_score", 30),
    )


@dataclass
class Verdict:
    status: str
    website_url: str | None
    website_count: int  # distinct genuine websites (>=2 ⇒ multiple)
    web_score: int | None  # crawl-confidence-based (0–100) or floored search score for negatives


# ── Search-only (provisional) ────────────────────────────────────────────────

def classify_search_results(
    scored_results: list[dict],
    directory_domains: set[str],
    thr: Thresholds,
) -> Verdict:
    """Provisional verdict from scored search results, before any crawl.

    scored_results: google_search_results_raw rows ({link, score, ...}).
    """
    own: list[dict] = []
    has_social = False
    has_directory = False
    own_domains: set[str] = set()

    for r in scored_results:
        link = (r.get("link") or "").strip()
        if not link:
            continue
        bucket = scoring.classify_domain(link, directory_domains)
        if bucket == "own":
            own.append(r)
        elif bucket == "social":
            has_social = True
        elif bucket == "directory":
            has_directory = True

    if own:
        best = max(own, key=lambda r: r.get("score") or 0)
        best_score = int(best.get("score") or 0)
        # Count distinct own-domains that clear the likely bar — early multi-site hint.
        for r in own:
            if int(r.get("score") or 0) >= thr.likely_score:
                dom = scoring._root_domain(r.get("link") or "")
                if dom:
                    own_domains.add(dom)
        count = max(1, len(own_domains))
        if best_score >= thr.confirmed_score:
            return Verdict(CONFIRMED, best["link"], count, best_score)
        # Below the confirmed bar we keep the link (so the crawler can verify it)
        # but mark it merely "likely" — the post-crawl pass will confirm or drop it.
        return Verdict(LIKELY, best["link"], count, best_score)

    if has_social:
        return Verdict(SOCIAL_ONLY, None, 0, 10)   # searched; social profile only
    if has_directory:
        return Verdict(DIRECTORY_ONLY, None, 0, 5)  # only registry/directory entries
    return Verdict(NONE, None, 0, 0)                # nothing credible found


# ── Post-crawl (authoritative) ───────────────────────────────────────────────

def _extract_tier(
    uid_matches_zefix,
    name_address_verified,
    confidence,
    thr: Thresholds,
    purpose_sim: float | None = None,
) -> str | None:
    """Classify one crawl extract into verified | confirmed | likely | None.

    purpose_sim (0–1) is the cosine similarity between the company's Zefix purpose
    embedding and the site's description/service_keywords embedding, computed by the
    ML-worker enrich_web_purpose_sim job. When present it provides a small confidence
    boost (up to +0.15) that can lift a borderline extract from likely → confirmed.
    """
    conf = confidence or 0.0
    if uid_matches_zefix is True or name_address_verified:
        return VERIFIED
    if uid_matches_zefix is False:
        return None  # site belongs to another company — never counts as this company's
    # Purpose-semantic boost: linear 0 → +0.15 over sim range [0.30, 1.00].
    if purpose_sim is not None and purpose_sim > 0.30:
        conf = min(1.0, conf + 0.15 * (purpose_sim - 0.30) / 0.70)
    if conf >= thr.confirmed_conf:
        return CONFIRMED
    if conf >= thr.likely_conf:
        return LIKELY
    return None


def compute_verdict(
    db: Session,
    company_id: int,
    scored_results: list[dict],
    directory_domains: set[str],
    thr: Thresholds,
) -> Verdict:
    """Authoritative verdict combining crawl extracts with the search fallback.

    Crawl extracts win when present; otherwise falls back to the search verdict.
    """
    from app.crud import crawler as crawler_crud
    rows = crawler_crud.get_web_extracts_with_urls(db, company_id)

    verified: list[tuple[str, float]] = []   # (root_domain, confidence)
    confirmed: list[tuple[str, float]] = []
    likely: list[tuple[str, float]] = []
    best_url_by_tier: dict[str, tuple[float, str]] = {}

    for row in rows:
        url = row.url or ""
        dom = scoring._root_domain(url)
        if not dom:
            continue
        purpose_sim = getattr(row, "purpose_sim", None)
        tier = _extract_tier(row.uid_matches_zefix, row.name_address_verified, row.confidence, thr, purpose_sim)
        if tier is None:
            continue
        conf = row.confidence or 0.0
        if tier == VERIFIED:
            verified.append((dom, conf))
        elif tier == CONFIRMED:
            confirmed.append((dom, conf))
        elif tier == LIKELY:
            likely.append((dom, conf))
        # Track best url per tier (highest confidence) for website_url selection.
        prev = best_url_by_tier.get(tier)
        if prev is None or conf > prev[0]:
            best_url_by_tier[tier] = (conf, url)

    if verified or confirmed or likely:
        if verified:
            status = VERIFIED
            url = best_url_by_tier[VERIFIED][1]
            best_conf = best_url_by_tier[VERIFIED][0]
        elif confirmed:
            status = CONFIRMED
            url = best_url_by_tier[CONFIRMED][1]
            best_conf = best_url_by_tier[CONFIRMED][0]
        else:
            status = LIKELY
            url = best_url_by_tier[LIKELY][1]
            best_conf = best_url_by_tier[LIKELY][0]
        strong_domains = {d for d, _ in verified} | {d for d, _ in confirmed}
        if strong_domains:
            count = len(strong_domains)
        else:
            count = len({d for d, _ in likely}) or 1
        # web_score driven by crawl confidence — more principled than the old ±delta.
        web_score = round(best_conf * 100)
        return Verdict(status, url, count, web_score)

    # No usable crawl evidence — fall back to the search-only verdict.
    return classify_search_results(scored_results, directory_domains, thr)
