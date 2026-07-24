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
