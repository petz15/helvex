"""Client for the SHAB (Schweizerisches Handelsamtsblatt) REST API.

Public API, no authentication required.
Base URL: https://www.shab.ch/api/v1
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

SHAB_API_BASE = "https://www.shab.ch/api/v1"

# HR sub-rubric constants
SUBR_NEW = "HR01"       # Neueintragung (new registration)
SUBR_MUTATION = "HR02"  # Mutation (change/update)
SUBR_DELETION = "HR03"  # Löschung (deletion)

HR_SUBRUBRICS = (SUBR_NEW, SUBR_MUTATION, SUBR_DELETION)


def fetch_hr_publications(
    from_date: date,
    to_date: date,
    *,
    page_size: int = 2000,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Fetch all SHAB HR publications for a date range.

    Returns a flat list of publication dicts.  Each entry's ``meta.id`` is the
    UUID needed to call :func:`fetch_publication_detail`.  The list endpoint
    does not include ``meta.uid`` — use the detail endpoint to get it.
    """
    params: dict[str, Any] = {
        "publicationStates": "PUBLISHED",
        "rubric": "HR",
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "pageSize": page_size,
    }

    results: list[dict[str, Any]] = []
    page = 1
    while True:
        params["pageNumber"] = page
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{SHAB_API_BASE}/publications", params=params)
            resp.raise_for_status()

        data = resp.json()
        if isinstance(data, list):
            items: list = data
        else:
            # Unwrap common wrapper formats
            items = (
                data.get("publications")
                or data.get("items")
                or data.get("content")
                or []
            )

        results.extend(items)
        if len(items) < page_size:
            break  # last page reached
        page += 1

    return results


def fetch_publication_detail(publication_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Fetch a single SHAB publication by UUID.

    The ``meta`` object of the response includes ``uid`` (CHE-XXX.XXX.XXX)
    when the publication relates to a Swiss commercial-register entry, and
    ``subRubric`` (HR01 / HR02 / HR03) indicating the type of change.
    """
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(f"{SHAB_API_BASE}/publications/{publication_id}")
        resp.raise_for_status()
    return resp.json()
