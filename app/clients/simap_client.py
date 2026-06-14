"""Client for the public simap.ch API.

All three endpoints used here are unauthenticated (no API key or OAuth required).
Base URL: https://www.simap.ch/api

Multi-value query parameters (newestPubTypes) must be sent as repeated keys,
not as a comma-joined string — the API rejects the comma form.
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://www.simap.ch/api"

AWARD_PUB_TYPES = [
    "award_tender",
    "award_study_contract",
    "award_competition",
    "direct_award",
]


def _build_search_url(
    from_date: date,
    to_date: date,
    *,
    last_item: str | None = None,
) -> str:
    parts: list[str] = []
    for t in AWARD_PUB_TYPES:
        parts.append(f"newestPubTypes={urllib.parse.quote(t)}")
    parts.append(f"newestPublicationFrom={from_date.isoformat()}")
    parts.append(f"newestPublicationUntil={to_date.isoformat()}")
    if last_item:
        parts.append(f"lastItem={urllib.parse.quote(last_item)}")
    return f"{_BASE}/publications/v2/project/project-search?{'&'.join(parts)}"


def search_award_projects(
    from_date: date,
    to_date: date,
    *,
    last_item: str | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Fetch one page of award projects from SIMAP.

    Returns ``{"projects": [...], "pagination": {"lastItem": str | None, "itemsPerPage": int}}``.
    Pass the returned ``pagination.lastItem`` as ``last_item`` to fetch the next page.
    An empty ``projects`` list means there are no more results.
    """
    url = _build_search_url(from_date, to_date, last_item=last_item)
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
    return resp.json()


def get_publication_detail(
    project_id: str,
    publication_id: str,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch full award publication detail including ``decision.vendors[]``.

    The ``decision.vendors`` list contains awarded vendors with ``vendorId``
    (SIMAP internal UUID), ``vendorName``, ``vendorAddress``, ``price``, and ``rank``.
    """
    url = f"{_BASE}/publications/v1/project/{project_id}/publication-details/{publication_id}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
    return resp.json()


def get_vendor_profile(
    vendor_id: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch public vendor profile.

    Returns the vendor's ``uidNo`` (CHE-xxx.xxx.xxx) when the vendor is a
    Swiss company registered in the commercial register.  Foreign companies
    return ``dunsNo`` instead and ``uidNo`` is null.
    """
    url = f"{_BASE}/vendors/v1/vendor/{vendor_id}/public"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
    return resp.json()


def _pick_translation(obj: dict[str, Any] | None, *langs: str) -> str | None:
    """Return the first non-empty value from a SIMAP Translation object."""
    if not obj:
        return None
    for lang in langs:
        v = obj.get(lang)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
    return None


def best_title(translation: dict[str, Any] | None) -> str:
    """Return the best available title from a Translation object (DE→FR→IT→EN)."""
    return _pick_translation(translation, "de", "fr", "it", "en") or ""
