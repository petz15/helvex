"""Evidence ledger — web-pipeline holistic rework, Layer B phase 1.

Purely additive: resolve_company_extract's existing confidence-model inputs
(UID match, address match, zone-weighted name match, signal coverage) are also
persisted as a typed, inspectable evidence list. This phase does not change
`confidence`/`method` behavior — only tests that the ledger is emitted and
is faithful to the same inputs.
"""
from app.services.enrichment.crawler_extract import (
    _build_evidence_ledger,
    resolve_company_extract,
)

_IMPRESSUM_HTML = b"""
<html><body>
<address>Musterstrasse 1, 8000 Zuerich</address>
<p>CHE-123.456.009</p>
</body></html>
"""


def test_uid_match_is_decisive_positive_evidence():
    evidence = _build_evidence_ledger(
        uid="CHE-123.456.009", uid_matches=True,
        addr_full_match=False, addr_partial_match=False,
        zone_name_conf=0.0, base=0.0,
    )
    dims = {e["dimension"]: e for e in evidence}
    assert dims["uid_matches_zefix"]["direction"] == "+"
    assert dims["uid_matches_zefix"]["strength"] == "decisive"
    assert dims["uid_matches_zefix"]["value"] == "CHE-123.456.009"


def test_uid_mismatch_is_negative_evidence():
    evidence = _build_evidence_ledger(
        uid="CHE-999.999.999", uid_matches=False,
        addr_full_match=False, addr_partial_match=False,
        zone_name_conf=0.0, base=0.0,
    )
    dims = {e["dimension"]: e for e in evidence}
    assert dims["uid_mismatch"]["direction"] == "-"
    assert dims["uid_mismatch"]["strength"] == "strong"


def test_address_and_zone_name_and_coverage_bucketed_correctly():
    evidence = _build_evidence_ledger(
        uid=None, uid_matches=None,
        addr_full_match=True, addr_partial_match=False,
        zone_name_conf=0.62, base=0.43,
    )
    dims = {e["dimension"]: e for e in evidence}
    assert dims["address_match"]["strength"] == "strong"
    assert dims["address_match"]["value"] == "full"
    assert dims["zone_name_match"]["strength"] == "strong"  # >= 0.55
    assert dims["signal_coverage"]["strength"] == "weak"
    assert dims["signal_coverage"]["value"] == 0.43


def test_zone_name_confidence_strength_tiers():
    strong = _build_evidence_ledger(
        uid=None, uid_matches=None, addr_full_match=False, addr_partial_match=False,
        zone_name_conf=0.60, base=0.0,
    )
    medium = _build_evidence_ledger(
        uid=None, uid_matches=None, addr_full_match=False, addr_partial_match=False,
        zone_name_conf=0.35, base=0.0,
    )
    weak = _build_evidence_ledger(
        uid=None, uid_matches=None, addr_full_match=False, addr_partial_match=False,
        zone_name_conf=0.10, base=0.0,
    )
    none_ = _build_evidence_ledger(
        uid=None, uid_matches=None, addr_full_match=False, addr_partial_match=False,
        zone_name_conf=0.0, base=0.0,
    )
    assert {e["dimension"]: e["strength"] for e in strong}["zone_name_match"] == "strong"
    assert {e["dimension"]: e["strength"] for e in medium}["zone_name_match"] == "medium"
    assert {e["dimension"]: e["strength"] for e in weak}["zone_name_match"] == "weak"
    assert "zone_name_match" not in {e["dimension"] for e in none_}


def test_no_signals_produces_empty_ledger():
    evidence = _build_evidence_ledger(
        uid=None, uid_matches=None, addr_full_match=False, addr_partial_match=False,
        zone_name_conf=0.0, base=0.0,
    )
    assert evidence == []


def test_resolve_company_extract_persists_evidence_matching_confidence():
    pages = [("impressum", _IMPRESSUM_HTML)]
    result = resolve_company_extract(
        pages, company_name="Muster AG", zefix_uid="CHE-123.456.009",
        company_zip="8000", company_city="Zuerich",
    )
    assert result != {}
    assert result["confidence"] is not None
    assert result["evidence"] is not None
    dims = {e["dimension"] for e in result["evidence"]}
    # UID matched and address fully verified — both should show up as evidence,
    # consistent with the "deterministic+uid_verified+address" method/confidence.
    assert "uid_matches_zefix" in dims
    assert "address_match" in dims
    assert "uid_verified" in result["extraction_method"]
