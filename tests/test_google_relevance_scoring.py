from types import SimpleNamespace

from app.config import settings
from app.services.collection import _score_google_results_for_company
from app.services.scoring import is_irrelevant_result, is_social_lead_domain


def _company_stub() -> SimpleNamespace:
    return SimpleNamespace(
        name="Muster AG",
        municipality="Bern",
        canton="BE",
        purpose="Beratung und Softwareentwicklung",
        legal_form="AG",
        address=None,
    )


def test_social_domain_helper_detects_instagram_and_facebook():
    assert is_social_lead_domain("https://instagram.com/muster_ag") is True
    assert is_social_lead_domain("https://www.facebook.com/musterag") is True
    assert is_social_lead_domain("https://muster-ag.ch") is False


def test_social_top_results_get_bonus():
    company = _company_stub()
    raw_results = [
        {
            "title": "Muster AG Instagram",
            "link": "https://www.instagram.com/muster_ag",
            "snippet": "Bern BE",
        },
        {
            "title": "Muster AG Facebook",
            "link": "https://www.facebook.com/musterag",
            "snippet": "Bern BE",
        },
        {
            "title": "Muster AG",
            "link": "https://muster-ag.ch",
            "snippet": "Offizielle Website",
        },
    ]

    scored = _score_google_results_for_company(company, raw_results)
    social_scores = [r["score"] for r in scored if "instagram.com" in r["link"] or "facebook.com" in r["link"]]
    assert social_scores
    assert all(s >= 20 for s in social_scores)  # includes +15 social bonus on top of base/fallback components


def test_exclude_keywords_force_url_exclusion_even_for_normal_domains():
    old = settings.google_url_exclude_keywords
    try:
        settings.google_url_exclude_keywords = "jobs, karriere, /careers"

        company = _company_stub()
        raw_results = [
            {
                "title": "Muster AG — Offizielle Website",
                "link": "https://muster-ag.ch/jobs",
                "snippet": "Bern BE",
            }
        ]

        assert is_irrelevant_result(raw_results[0], company_name=company.name) is True

        scored = _score_google_results_for_company(company, raw_results)
        assert scored[0]["score"] == 0
    finally:
        settings.google_url_exclude_keywords = old
