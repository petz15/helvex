"""Company-level website verdict + per-candidate identity categorization —
web-pipeline holistic rework, Layer B phase 2 (categorical verdict).
"""
from datetime import datetime, timezone

from app.models.company import Company
from app.models.company_url_candidate import CompanyUrlCandidate
from app.models.company_web_extract import CompanyWebExtract
from app.services.enrichment import website_status as ws


def _thr():
    return ws.Thresholds(confirmed_conf=0.65, likely_conf=0.45, confirmed_score=55, likely_score=30)


# ── categorize_identity ──────────────────────────────────────────────────────

def test_categorize_identity_uid_match():
    category, prob = ws.categorize_identity(0.95, True, True, _thr())
    assert category == ws.MATCH_UID
    assert prob == 0.95


def test_categorize_identity_uid_mismatch():
    category, prob = ws.categorize_identity(0.10, False, True, _thr())
    assert category == ws.MISMATCH


def test_categorize_identity_no_evidence_is_unknown():
    category, _ = ws.categorize_identity(0.0, None, False, _thr())
    assert category == ws.UNKNOWN


def test_categorize_identity_strong_vs_weak_bucketing():
    strong, _ = ws.categorize_identity(0.70, None, True, _thr())
    weak, _ = ws.categorize_identity(0.20, None, True, _thr())
    assert strong == ws.MATCH_STRONG
    assert weak == ws.MATCH_WEAK


# ── _pick_best_candidate ──────────────────────────────────────────────────────

def test_pick_best_candidate_prefers_name_address_verified_over_raw_confidence():
    candidates = [
        ("a.ch", 0.70, False, "https://a.ch"),
        ("b.ch", 0.62, True, "https://b.ch"),
    ]
    url, conf, ambiguous = ws._pick_best_candidate(candidates)
    assert url == "https://b.ch"  # verified wins despite lower raw confidence
    assert ambiguous is True  # distinct domains within the margin


def test_pick_best_candidate_not_ambiguous_when_clear_winner():
    candidates = [
        ("a.ch", 0.90, False, "https://a.ch"),
        ("b.ch", 0.20, False, "https://b.ch"),
    ]
    url, conf, ambiguous = ws._pick_best_candidate(candidates)
    assert url == "https://a.ch"
    assert ambiguous is False


def test_pick_best_candidate_same_domain_never_ambiguous():
    """Two rows for the same domain (e.g. homepage + a subpage candidate) aren't
    a real identity conflict — only distinct domains count as ambiguous."""
    candidates = [
        ("a.ch", 0.70, False, "https://a.ch"),
        ("a.ch", 0.68, False, "https://a.ch/impressum"),
    ]
    _, _, ambiguous = ws._pick_best_candidate(candidates)
    assert ambiguous is False


# ── compute_verdict ───────────────────────────────────────────────────────────

def _seed_candidate_and_extract(db, *, company_id, url, confidence, uid_matches_zefix,
                                 name_address_verified=False):
    candidate = CompanyUrlCandidate(company_id=company_id, url=url, status="selected")
    db.add(candidate)
    db.flush()
    db.add(CompanyWebExtract(
        company_id=company_id,
        url_candidate_id=candidate.id,
        confidence=confidence,
        uid_matches_zefix=uid_matches_zefix,
        name_address_verified=name_address_verified,
        extracted_at=datetime.now(timezone.utc),
    ))
    db.commit()


def test_compute_verdict_verified_on_uid_match(db):
    db.add(Company(id=8001, uid="CHE-800.100.000", name="Muster AG"))
    db.commit()
    _seed_candidate_and_extract(
        db, company_id=8001, url="https://muster.ch",
        confidence=0.9, uid_matches_zefix=True,
    )
    verdict = ws.compute_verdict(db, 8001, _thr())
    assert verdict.status == ws.VERIFIED
    assert verdict.website_url == "https://muster.ch"
    assert verdict.ambiguous is False


def test_compute_verdict_no_snippet_fallback_after_failed_crawl(db):
    """Regression: a crawl that finds a UID mismatch (real negative evidence)
    must not fall back to the pre-crawl snippet score — that would let a weak
    search-result guess overrule an actual crawl finding."""
    db.add(Company(id=8002, uid="CHE-800.200.000", name="No Match AG"))
    db.commit()
    _seed_candidate_and_extract(
        db, company_id=8002, url="https://wrong-site.ch",
        confidence=0.10, uid_matches_zefix=False,
    )
    verdict = ws.compute_verdict(db, 8002, _thr())
    assert verdict.status == ws.NONE
    assert verdict.website_url is None


def test_compute_verdict_unknown_when_never_crawled(db):
    """Identity rework phase 3 ("cut pre-crawl scoring"): a company with no
    company_web_extract rows is unknown, regardless of how good its search
    snippet score was — the search-only fallback verdict was removed."""
    db.add(Company(id=8003, uid="CHE-800.300.000", name="Never Crawled AG"))
    db.commit()
    verdict = ws.compute_verdict(db, 8003, _thr())
    assert verdict.status is None
    assert verdict.website_url is None
