"""Client adapter for HR publications backed by the Zefix Public REST API.

The importer consumes SHAB-like payloads (``meta.id``, ``meta.subRubric``,
``meta.uid``). This module now sources data from the Zefix SOGC endpoints and
normalizes responses to that shape.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from app.config import settings

ZEFIX_API_BASE = settings.zefix_api_base_url.rstrip("/")

# HR sub-rubric constants
SUBR_NEW = "HR01"       # Neueintragung (new registration)
SUBR_MUTATION = "HR02"  # Mutation (change/update)
SUBR_DELETION = "HR03"  # Löschung (deletion)

HR_SUBRUBRICS = (SUBR_NEW, SUBR_MUTATION, SUBR_DELETION)


def _get_auth() -> httpx.BasicAuth | None:
    if settings.zefix_api_username and settings.zefix_api_password:
        return httpx.BasicAuth(settings.zefix_api_username, settings.zefix_api_password)
    return None


def _guess_sub_rubric(publication_number: str, mutation_keys: list[str]) -> str:
    upper_no = (publication_number or "").upper()
    if upper_no.startswith("HR01"):
        return SUBR_NEW
    if upper_no.startswith("HR02"):
        return SUBR_MUTATION
    if upper_no.startswith("HR03"):
        return SUBR_DELETION

    joined = " ".join(mutation_keys).upper()
    if any(k in joined for k in ("NEW", "NEU", "ENTRY", "EINTRAG")):
        return SUBR_NEW
    if any(k in joined for k in ("DELETE", "DELETION", "CANCEL", "LOESCH", "LÖSCH")):
        return SUBR_DELETION
    if mutation_keys:
        return SUBR_MUTATION
    return ""


def _to_shab_like_item(item: dict[str, Any]) -> dict[str, Any]:
    # Backward compatibility in case upstream already returns SHAB-shaped objects.
    if "meta" in item and isinstance(item.get("meta"), dict):
        return item

    publication = item.get("sogcPublication") or {}
    company = item.get("companyShort") or {}
    mutation_types = publication.get("mutationTypes") or []
    mutation_keys = [str(m.get("key") or "") for m in mutation_types if isinstance(m, dict)]

    publication_number = str(publication.get("publicationNumber") or "")
    sub_rubric = _guess_sub_rubric(publication_number, mutation_keys)

    uid = company.get("uid")
    name = str(company.get("name") or "")
    sogc_id = publication.get("sogcId")
    sogc_date = publication.get("sogcDate")

    meta: dict[str, Any] = {
        "id": str(sogc_id) if sogc_id is not None else "",
        "subRubric": sub_rubric,
        "uid": uid,
        "title": {
            "de": name,
            "en": name,
        },
        "publicationDate": sogc_date,
    }
    return {"meta": meta, "zefix_raw": item}


def fetch_hr_publications(
    from_date: date,
    to_date: date,
    *,
    page_size: int = 100,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Fetch all HR publications for an inclusive date range from Zefix SOGC.

    The ``page_size`` parameter is kept for backward compatibility but is not
    used by the by-date endpoint.
    """
    _ = page_size

    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout) as client:
        day = from_date
        while day <= to_date:
            resp = client.get(
                f"{ZEFIX_API_BASE}/sogc/bydate/{day.isoformat()}",
                auth=_get_auth(),
            )
            resp.raise_for_status()

            data = resp.json()
            items = data if isinstance(data, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                normalized = _to_shab_like_item(item)
                sub = ((normalized.get("meta") or {}).get("subRubric") or "")
                if sub in HR_SUBRUBRICS:
                    results.append(normalized)

            day += timedelta(days=1)

    return results


def fetch_publication_detail(publication_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Fetch a single publication detail from Zefix and normalize shape."""
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(
            f"{ZEFIX_API_BASE}/sogc/{publication_id}",
            auth=_get_auth(),
        )
        resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return _to_shab_like_item(data)
    return {"meta": {"id": str(publication_id)}}
