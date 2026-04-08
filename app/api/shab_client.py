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
    page_size: int = 100,
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

    def _to_int(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    results: list[dict[str, Any]] = []
    page = 0
    with httpx.Client(timeout=timeout) as client:
        while True:
            # SHAB currently uses zero-based `page`; keep `pageNumber` in sync
            # for compatibility with older wrappers/proxies.
            params["page"] = page
            params["pageNumber"] = page + 1
            resp = client.get(f"{SHAB_API_BASE}/publications", params=params)
            resp.raise_for_status()

            data = resp.json()
            meta: dict[str, Any] = data if isinstance(data, dict) else {}
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

            # Stop on obvious end-of-data condition first.
            if not items:
                break

            # Prefer explicit pagination metadata when available. SHAB may enforce
            # its own per-page cap (often 100) even when `pageSize` is larger.
            page_request = meta.get("pageRequest") if isinstance(meta, dict) else None
            total_pages = (
                _to_int(meta.get("totalPages"))
                or _to_int(meta.get("numberOfPages"))
                or _to_int(meta.get("pageCount"))
            )
            current_page = (
                _to_int(meta.get("page"))
                or (_to_int(page_request.get("page")) if isinstance(page_request, dict) else None)
                or _to_int(meta.get("pageNumber"))
                or page
            )
            total_items = (
                _to_int(meta.get("totalElements"))
                or _to_int(meta.get("totalEntries"))
                or _to_int(meta.get("total"))
                or _to_int(meta.get("count"))
            )
            is_last = meta.get("last") is True
            has_paging_meta = any(
                x is not None for x in (total_pages, total_items)
            ) or ("last" in meta)

            if is_last:
                break
            if total_pages is not None and current_page >= total_pages:
                break
            if total_items is not None and len(results) >= total_items:
                break

            # Fallback for list-style responses without page metadata.
            if not has_paging_meta and len(items) < page_size:
                break

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
