from app.services.scoring import (
    compute_zefix_score,
    compute_zefix_score_breakdown,
    fallback_result_score,
    is_irrelevant_result,
    score_result,
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


def test_score_result_exact_ch_domain_gives_extra_bonus():
    # domain "muster.ch" exactly matches company "Muster AG" → +10 swiss tld + 15 exact name
    result = {"title": "Muster", "link": "https://muster.ch", "snippet": ""}
    score = score_result(result, company_name="Muster AG", municipality=None, canton=None, position=4)
    # name in title:    words_a={"muster","ag"}, words_b={"muster"} → 1/2=0.5 → int(0.5*30)=15
    # domain overlap:   "muster" in "muster.ch", 1/1 words → int(1.0*15)=15
    # swiss tld:        +10
    # exact name match: +15
    # position 4:       +0
    assert score == 55


def test_score_result_partial_ch_domain_no_extra_bonus():
    # "muster-solutions.ch" does NOT exactly match "Muster AG" — no +15
    result = {"title": "Muster Solutions", "link": "https://muster-solutions.ch", "snippet": ""}
    score = score_result(result, company_name="Muster AG", municipality=None, canton=None, position=4)
    # name in title:  words_a={"muster","ag"}, words_b={"muster","solutions"} → 1/2 → 15
    # domain overlap: "muster" in "muster-solutions.ch" → 1/1 words → 15
    # swiss tld:      +10   (no exact match bonus)
    # position 4:     +0
    assert score == 40


def test_score_result_position_bonus_tapers():
    result = {"title": "Muster", "link": "https://muster.ch", "snippet": ""}
    base = score_result(result, company_name="Muster AG", municipality=None, canton=None, position=4)
    pos0 = score_result(result, company_name="Muster AG", municipality=None, canton=None, position=0)
    pos2 = score_result(result, company_name="Muster AG", municipality=None, canton=None, position=2)
    assert pos0 == base + 30  # position 0 → +30
    assert pos2 == base + 15  # position 2 → +15


def test_score_result_directory_domain_ignores_position():
    result = {"title": "Muster AG", "link": "https://local.ch/muster", "snippet": ""}
    score0 = score_result(result, company_name="Muster AG", municipality=None, canton=None, position=0)
    score4 = score_result(result, company_name="Muster AG", municipality=None, canton=None, position=4)
    assert score0 == 0
    assert score4 == 0


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
