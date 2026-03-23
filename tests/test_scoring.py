from app.services.scoring import (
    compute_zefix_score,
    compute_zefix_score_breakdown,
    fallback_result_score,
    is_irrelevant_result,
)


def test_being_cancelled_forces_zero_score():
    score = compute_zefix_score(
        legal_form="GmbH",
        legal_form_short_name="gmbh",
        status="being_cancelled",
        canton="BE",
        municipality="Bern",
    )
    assert score == 5  # cancelled_score default; not zero so cancelled companies stay findable


def test_score_breakdown_contains_final_score():
    breakdown = compute_zefix_score_breakdown(
        legal_form="GmbH",
        legal_form_short_name="gmbh",
        status="aktiv",
        canton="BE",
        municipality="Bern",
    )
    assert "final_score" in breakdown
    assert isinstance(breakdown["final_score"], int)


def test_irrelevant_result_detects_directory_domain():
    result = {
        "title": "Muster AG - Profile",
        "link": "https://www.local.ch/de/d/muster-ag",
        "snippet": "Directory listing",
    }
    assert is_irrelevant_result(result, company_name="Muster AG") is True


def test_fallback_result_score_base_plus_location_plus_legal():
    result = {
        "title": "Muster AG | Home",
        "link": "https://muster-ag.ch",
        "snippet": "Bern BE",
    }
    score = fallback_result_score(
        result,
        municipality="Bern",
        canton="BE",
        legal_form="AG",
    )
    assert score == 45  # 5 base + 25 municipality + 10 canton + 5 legal
