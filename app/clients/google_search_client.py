"""Client for the Serper.dev Google Search API.

Documentation: https://serper.dev/
"""

import httpx

from app.config import settings
from app.schemas.company import GoogleSearchResult

_SERPER_URL = "https://google.serper.dev/search"

_LANG_MAP = {
    "de": "de",
    "fr": "fr",
    "it": "it",
    "en": "en",
    "rm": "de",  # Romansh — fall back to German
}


def search_website(
    company_name: str,
    *,
    num: int = 5,
    zip_code: str | None = None,
    municipality: str | None = None,
    purpose_language: str | None = None,
    api_key: str | None = None,
) -> tuple[list[GoogleSearchResult], dict]:
    """Search for a company's website using the Serper.dev API.

    Args:
        company_name: The company name to search for.
        num: Number of results to return (1-25).
        zip_code: Swiss postal code for localised results.
        municipality: Municipality name for localised results.
        purpose_language: Company purpose language (de/fr/it/en/rm).
        api_key: Override API key; falls back to SERPER_API_KEY env var.

    Returns:
        A list of :class:`GoogleSearchResult` instances.

    Raises:
        ValueError: If the Serper API key is not configured.
        httpx.HTTPStatusError: If the Serper API returns a non-2xx response.
    """
    key = api_key or settings.serper_api_key
    if not key:
        raise ValueError("SERPER_API_KEY must be set to use the search integration.")

    lang = _LANG_MAP.get((purpose_language or "").lower(), "de")

    # Build location string for Serper's `location` param: "Municipality, Switzerland"
    location_parts = []
    if municipality:
        location_parts.append(municipality.strip())
    location_parts.append("Switzerland")
    location = ", ".join(location_parts)

    payload = {
        "q": company_name,
        "num": min(max(1, num), 25),
        "gl": "ch",
        "hl": lang,
        "location": location,
    }
    headers = {
        "X-API-KEY": key,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(_SERPER_URL, json=payload, headers=headers)
        if not response.is_success:
            body = response.text[:500]
            raise httpx.HTTPStatusError(
                f"Serper API HTTP {response.status_code}: {body}",
                request=response.request,
                response=response,
            )

    data = response.json()
    items = data.get("organic", [])
    parsed = [
        GoogleSearchResult(
            title=item.get("title", ""),
            link=item.get("link", ""),
            snippet=item.get("snippet"),
        )
        for item in items
    ]
    return parsed, data
