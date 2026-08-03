"""Company-level website verdict — does this company have a website, and how many?

The company-facing verdict is *crawl-authoritative only* (identity rework phase 3,
"cut pre-crawl scoring" — see docs/code-review/web-identity-rework.md §3.4/§4). URL
search results (Serper / ScrapingDog) are still scored per company (in
google_search_results_raw) and that score orders which candidate the crawler tries
first, but a snippet match is weak evidence and no longer drives a persisted
verdict on its own: until a company has actually been crawled and produced a
company_web_extract row, its verdict stays unknown (NULL), not "confirmed"/"likely".

This also keeps the old "force scored[0] into website_url" behaviour retired:
website_url is only set when the verdict indicates a genuine own-domain match.
Companies whose crawl turns up only social profiles, directory listings, or
nothing get a negative verdict and a NULL website_url instead of a forced guess.

Verdicts (most → least certain):
    verified        crawl found Swiss UID matching Zefix, or name+address verified
    confirmed       own-domain found with high extract confidence (no UID proof)
    likely          own-domain candidate exists, mid confidence
    social_only     crawled, no own-domain cleared the bar, but a social profile was found
    directory_only  crawled, only directory/registry listings (moneyhouse, local.ch, …)
    none            searched and/or crawled to a conclusion — this company has no website
    unreachable     every candidate was bot-blocked / errored, so identity is undecided
                    (distinct from `none`: nothing was disproved, it is worth retrying)
    (NULL)          unknown — identity phase still in flight

`none` covers three concluded paths, all of which used to leave a NULL that was
indistinguishable from "not looked at yet": no crawlable candidate existed at
all, every candidate was tried and rejected, or the crawl produced extracts that
cleared no tier. See crawler_crud.get_identity_outcome.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import crud
from app.services.scoring import scoring

VERIFIED = "verified"
CONFIRMED = "confirmed"
LIKELY = "likely"
SOCIAL_ONLY = "social_only"
DIRECTORY_ONLY = "directory_only"
NONE = "none"
UNREACHABLE = "unreachable"

# Verdicts for which website_url should hold a real own-domain link.
POSITIVE = frozenset({VERIFIED, CONFIRMED, LIKELY})

# ── Per-candidate identity categories (web-pipeline holistic rework, Layer B) ──
# Categorical outcome for a SINGLE crawled candidate — replaces reasoning about
# a bare confidence float with a labelled bucket. AMBIGUOUS and RELATED_ENTITY
# are cross-candidate/cross-entity outcomes computed in compute_verdict, not here
# (RELATED_ENTITY needs a parent/subsidiary graph lookup — not yet built).
MATCH_UID = "MATCH_UID"
MATCH_STRONG = "MATCH_STRONG"
MATCH_WEAK = "MATCH_WEAK"
MISMATCH = "MISMATCH"
UNKNOWN = "UNKNOWN"


def categorize_identity(
    confidence: float | None,
    uid_matches: bool | None,
    has_evidence: bool,
    thr: "Thresholds",
) -> tuple[str, float]:
    """Map one candidate's confidence + evidence into (category, probability).

    probability is currently just `confidence` (the deterministic combine
    already computed in resolve_company_extract) — a future trained model
    replaces the combine step behind this same signature, per the identity
    rework's staging plan (ledger-first, GBM later).
    """
    conf = confidence or 0.0
    if uid_matches is False:
        return MISMATCH, conf
    if uid_matches is True:
        return MATCH_UID, conf
    if not has_evidence:
        return UNKNOWN, conf
    if conf >= thr.confirmed_conf:
        return MATCH_STRONG, conf
    return MATCH_WEAK, conf


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
    status: str | None  # None ⇒ unknown, not yet crawled — see module docstring
    website_url: str | None
    website_count: int  # distinct genuine websites (>=2 ⇒ multiple)
    web_score: int | None  # crawl-confidence-based (0–100) or floored search score for negatives
    # True when ≥2 distinct-domain candidates tied on comparable strong evidence
    # at the winning tier — website_url was still auto-picked (never left blank
    # pending review), via the tiebreak in compute_verdict, but the pick was a
    # judgment call worth surfacing rather than hiding behind a confident-looking url.
    ambiguous: bool = False


# ── Search-only (provisional) ────────────────────────────────────────────────

def classify_search_results(
    scored_results: list[dict],
    directory_domains: set[str],
    thr: Thresholds,
) -> Verdict:
    """Score-only classification of search results — crawl-queue prioritizer.

    NOT wired into the persisted company-level verdict (see module docstring —
    identity rework phase 3 demoted this to ordering input only). Kept as a
    standalone helper: still useful for reasoning about what a raw search score
    implies, e.g. to help order which candidate the crawler tries first.

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
    # == not `is`: raw-SQL rows (get_web_extracts_with_urls uses text()) can come
    # back as 0/1 ints rather than the bool singleton on some drivers/dialects
    # (observed with SQLite; Postgres/psycopg2 already returns real bools) — `is`
    # would silently misclassify those as the None branch. `==` preserves the
    # None/True/False tri-state (None == True and None == False are both False).
    if uid_matches_zefix == True or name_address_verified:  # noqa: E712
        return VERIFIED
    if uid_matches_zefix == False:  # noqa: E712
        return None  # site belongs to another company — never counts as this company's
    # Purpose-semantic boost: linear 0 → +0.15 over sim range [0.30, 1.00].
    if purpose_sim is not None and purpose_sim > 0.30:
        conf = min(1.0, conf + 0.15 * (purpose_sim - 0.30) / 0.70)
    if conf >= thr.confirmed_conf:
        return CONFIRMED
    if conf >= thr.likely_conf:
        return LIKELY
    return None


_AMBIGUITY_MARGIN = 0.05  # confidence gap within which two distinct domains "tie"


def _pick_best_candidate(
    candidates: list[tuple[str, float, bool, str]],
) -> tuple[str, float, bool]:
    """Auto-pick among tied candidates for one tier.

    candidates: (root_domain, confidence, name_address_verified, url).
    Tiebreak (independent of raw confidence, applied first): prefer
    name_address_verified — corroborating evidence beyond the score itself —
    then fall back to highest confidence. This is a partial implementation of
    the identity rework's 4-step tiebreak (own-verified-socials-link and
    UID-bearing-within-tier aren't differentiable with data we currently
    collect; registry-phone doesn't exist yet — see ROADMAP).

    Returns (url, confidence, is_ambiguous) — is_ambiguous is True when ≥2
    distinct domains are within _AMBIGUITY_MARGIN of the winner.
    """
    best = max(candidates, key=lambda c: (c[2], c[1]))
    distinct_domains = {c[0] for c in candidates}
    is_ambiguous = len(distinct_domains) >= 2 and any(
        d != best[0] and (best[1] - c) <= _AMBIGUITY_MARGIN
        for d, c, _, _ in candidates
    )
    return best[3], best[1], is_ambiguous


def compute_verdict(
    db: Session,
    company_id: int,
    thr: Thresholds,
) -> Verdict:
    """Authoritative, crawl-only verdict — see module docstring (identity rework
    phase 3). No search-snippet fallback: a company with no company_web_extract
    rows yet is unknown (Verdict(None, ...)), whether or not it's been searched.
    """
    from app.crud import crawler as crawler_crud
    rows = crawler_crud.get_web_extracts_with_urls(db, company_id)

    # (root_domain, confidence, name_address_verified, url) per tier.
    verified: list[tuple[str, float, bool, str]] = []
    confirmed: list[tuple[str, float, bool, str]] = []
    likely: list[tuple[str, float, bool, str]] = []

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
        entry = (dom, conf, bool(row.name_address_verified), url)
        if tier == VERIFIED:
            verified.append(entry)
        elif tier == CONFIRMED:
            confirmed.append(entry)
        elif tier == LIKELY:
            likely.append(entry)

    if verified or confirmed or likely:
        if verified:
            status, winning = VERIFIED, verified
        elif confirmed:
            status, winning = CONFIRMED, confirmed
        else:
            status, winning = LIKELY, likely
        url, best_conf, ambiguous = _pick_best_candidate(winning)

        strong_domains = {d for d, *_ in verified} | {d for d, *_ in confirmed}
        if strong_domains:
            count = len(strong_domains)
        else:
            count = len({d for d, *_ in likely}) or 1
        # web_score driven by crawl confidence — more principled than the old ±delta.
        web_score = round(best_conf * 100)
        return Verdict(status, url, count, web_score, ambiguous=ambiguous)

    if rows:
        # A crawl was attempted and produced extract rows, but none cleared any
        # tier (e.g. UID mismatches, or evidence too thin) — genuine negative
        # evidence. Never fall back to the pre-crawl snippet score here: that
        # would let a weak search-result guess overrule an actual crawl finding.
        return Verdict(NONE, None, 0, 0)

    # No extract rows. That is only "unknown" while the identity phase is still
    # in flight — once it concludes without producing one, the company has a real
    # answer that must be shown rather than left as an empty cell:
    #   no_candidates → search turned up nothing crawlable at all
    #   exhausted     → every candidate was tried, none was them
    #   unreachable   → the site(s) blocked or errored, so we still don't know
    # The first two are genuine "no website" findings; the third deliberately is
    # NOT, and gets its own status so it can be retried rather than written off.
    outcome = crawler_crud.get_identity_outcome(db, company_id)
    if outcome == "unreachable":
        return Verdict(UNREACHABLE, None, 0, None)
    if outcome in ("no_candidates", "exhausted"):
        return Verdict(NONE, None, 0, 0)

    # Still in flight — unknown. A raw search snippet is not trusted as a
    # company-facing verdict (identity rework phase 3, "cut pre-crawl scoring");
    # classify_search_results still exists as a crawl-queue-ordering helper, but
    # its output is not surfaced here.
    return Verdict(None, None, 0, None)
