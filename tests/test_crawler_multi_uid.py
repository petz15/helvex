"""Multi-UID false-positive/false-mismatch guard.

_extract_uid() used to return only the FIRST checksum-valid UID found
anywhere in the raw HTML. A page can legitimately carry more than one UID —
a group-structure impressum listing a subsidiary and its parent, an agency
credit in the footer, a partner/client logo strip — so whichever one
happened to appear earliest in the HTML source always won, even when the
target company's own correct UID was also present later on the same page.
That silently produced a false `uid_matches_zefix=False` (MISMATCH) for a
genuinely correct site, which triggers reject_url_candidate/quarantine
downstream in handle_web_extract.

_extract_uids() now collects every distinct valid UID on the page;
resolve_company_extract prefers whichever one matches the target's Zefix
UID, regardless of extraction order.
"""
from app.services.enrichment.crawler_extract import _extract_uids, resolve_company_extract

# Two independently-valid CHE UIDs (real checksum-valid, unrelated numbers).
_UID_A = "CHE-987.654.007"  # appears first in the HTML — NOT the target company
_UID_B = "CHE-123.456.009"  # appears second — this IS the target company's real UID

_MULTI_UID_IMPRESSUM = f"""
<html><body>
<p>Website erstellt von Agentur XY, {_UID_A}</p>
<address>Musterstrasse 1, 8000 Zuerich</address>
<p>Muster AG, {_UID_B}</p>
</body></html>
""".encode()


def test_extract_uids_finds_all_distinct_valid_uids_in_order():
    uids = _extract_uids(_MULTI_UID_IMPRESSUM.decode())
    assert uids == [_UID_A, _UID_B]


def test_resolve_company_extract_matches_target_uid_even_when_not_first():
    """The regression case: target's UID (_UID_B) appears SECOND on the page,
    behind an unrelated agency-credit UID (_UID_A). Must still resolve to a
    match, not a false MISMATCH."""
    pages = [("impressum", _MULTI_UID_IMPRESSUM)]
    result = resolve_company_extract(pages, zefix_uid=_UID_B)

    assert result["uid"] == _UID_B
    assert result["uid_matches_zefix"] is True


def test_resolve_company_extract_still_reports_mismatch_when_target_uid_absent():
    """Sanity check: when the target's UID genuinely isn't on the page at
    all, this must still correctly report a mismatch (not silently pass)."""
    pages = [("impressum", _MULTI_UID_IMPRESSUM)]
    result = resolve_company_extract(pages, zefix_uid="CHE-111.111.116")

    assert result["uid_matches_zefix"] is False


# ── Real-world formatting regressions (remarkt.ch, 2026-08-18) ────────────────

def test_uid_with_typographic_hyphen_is_found():
    r"""CMSes substitute U+2011 for the ASCII hyphen; it is visually identical.

    `_UID_RE`'s `[-\s]?` matched none of the Unicode dashes, so an impressum
    printing `CHE‑130.637.800` yielded NO UID — losing the single decisive piece
    of identity evidence and collapsing a correct site to low confidence.
    """
    from app.services.enrichment.crawler_extract import _extract_uids

    for sep in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        assert _extract_uids(f"CHE{sep}130.637.800") == ["CHE-130.637.800"], (
            f"UID with U+{ord(sep):04X} separator was not extracted"
        )


def test_uid_written_with_html_entities_is_found():
    """_extract_uids runs on RAW HTML, where entities are never decoded."""
    from app.services.enrichment.crawler_extract import _extract_uids

    assert _extract_uids("CHE&nbsp;130.637.800") == ["CHE-130.637.800"]
    assert _extract_uids("CHE&#8209;130.637.800") == ["CHE-130.637.800"]


def test_uid_checksum_still_rejects_lookalikes():
    """The looser separator class is only safe because of checksum validation."""
    from app.services.enrichment.crawler_extract import _extract_uids

    assert _extract_uids("CHE-123.456.789") == []
    assert _extract_uids("call 044-123-456-789 now") == []


def test_address_matches_when_zefix_seat_is_the_second_address_listed():
    """A company may publish several addresses; Zefix may hold the later one.

    remarkt.ch's impressum lists Grienweg 16, 4226 Breitenbach first and the
    registered seat Kastelstrasse 444, 4204 Himmelried second. Matching only the
    single stored `address` scored a correct site as having no address match.
    """
    from app.services.enrichment.crawler_extract import resolve_company_extract

    impressum = (
        "<html><body><h1>Impressum</h1>"
        "<p>ReMarkt<br>Grienweg 16<br>4226 Breitenbach</p>"
        "<p>Werkstatt: Kastelstrasse 444<br>4204 Himmelried</p>"
        "<p>CHE\u2011130.637.800</p>"
        "</body></html>"
    ).encode()

    data = resolve_company_extract(
        [("impressum", impressum)],
        company_name="ReMarkt",
        zefix_uid="CHE-130.637.800",
        site_url="https://www.remarkt.ch/impressum",
        company_zip="4204",
        company_city="Himmelried",
        page_types=["impressum"],
    )

    assert data["uid"] == "CHE-130.637.800"
    assert data["uid_matches_zefix"] is True
    assert data["confidence"] >= 0.80, (
        f"a UID-verified site should not be low confidence (got {data['confidence']})"
    )
