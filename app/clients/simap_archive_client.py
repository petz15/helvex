"""HTTP client for the pre-2024 SIMAP archive at archiv.simap.ch.

The archive covers 2007–2023 and uses a different API than the current simap.ch
platform.  There is no authentication required.  The API is a REST JSON service
discovered by inspecting the Vite JS bundle of the SPA.

Key endpoints
-------------
POST https://archiv.simap.ch/api/search
    Query params: pageNo (1-based), recordsPerPage
    JSON body:    see _build_search_body()
    Returns:      {"total": N, "pages": N, "publication": [...]}

GET https://archiv.simap.ch/api/detail?meldungsnummer={id}
    Returns the full nested XML-mapped detail for one publication.
"""
from __future__ import annotations

import httpx

_BASE = "https://archiv.simap.ch/api"
_TIMEOUT = 30.0


def _build_search_body(
    from_date: str | None,
    to_date: str | None,
    pub_type: str = "OB02",
) -> dict:
    """Build the POST body using the field names from the JS getSearchDTO()."""
    body: dict = {}
    if pub_type:
        body["type_cd_ob"] = pub_type
    if from_date:
        body["stat_tm_1"] = from_date
    if to_date:
        body["stat_tm_2"] = to_date
    return body


def search_archive_awards(
    from_date: str | None,
    to_date: str | None,
    page_no: int = 1,
    records_per_page: int = 1000,
    client: httpx.Client | None = None,
) -> dict:
    """Return one page of OB02 (award notice) search results.

    Args:
        from_date: ISO date string YYYY-MM-DD or None for no lower bound.
        to_date: ISO date string YYYY-MM-DD or None for no upper bound.
        page_no: 1-based page number.
        records_per_page: Results per page (max tested: 1000).
        client: Optional shared httpx.Client for connection reuse.

    Returns:
        Dict with keys: total (int), pages (int), publication (list[dict]).
    """
    body = _build_search_body(from_date, to_date)
    params = {"pageNo": page_no, "recordsPerPage": records_per_page}
    if client:
        r = client.post(f"{_BASE}/search", params=params, json=body, timeout=_TIMEOUT)
    else:
        r = httpx.post(f"{_BASE}/search", params=params, json=body, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_archive_detail(meldungsnummer: int, client: httpx.Client | None = None) -> dict:
    """Fetch the full publication detail for a single archive entry.

    Returns the raw parsed JSON.  Top-level keys include 'OB02' for award
    notices.  Contractor info lives under OB02.OB02.SPEC.OB02.AWARD.PRIM.CONTRACTOR.LIST.
    """
    params = {"meldungsnummer": meldungsnummer}
    if client:
        r = client.get(f"{_BASE}/detail", params=params, timeout=_TIMEOUT)
    else:
        r = httpx.get(f"{_BASE}/detail", params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()
