"""Client for the ScrapingDog Google Search API.

Documentation: https://www.scrapingdog.com/google-search-api/
Returns the full provider JSON (search_information, local_results, organic_results, etc.)
alongside a parsed list of GoogleSearchResult for the scoring pipeline.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.schemas.company import GoogleSearchResult

_SCRAPINGDOG_URL = "https://api.scrapingdog.com/google/"

# ScrapingDog language codes that map from our purpose_language values
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
    num: int = 10,
    zip_code: str | None = None,
    municipality: str | None = None,
    purpose_language: str | None = None,
) -> tuple[list[GoogleSearchResult], dict]:
    """Search for a company's website using the ScrapingDog Google Search API.

    Returns:
        A tuple of (scored_results, raw_response_dict).
        raw_response_dict is the complete JSON from ScrapingDog (includes
        search_information, local_results, organic_results, pagination).

    Raises:
        ValueError: If the ScrapingDog API key is not configured.
        httpx.HTTPStatusError: If the API returns a non-2xx response.
    """
    if not settings.scrapingdog_api_key:
        raise ValueError("SCRAPINGDOG_API_KEY must be set to use ScrapingDog search.")

    # Build location string: "PLZ Municipality, Switzerland"
    location_parts = []
    if zip_code:
        location_parts.append(zip_code.strip())
    if municipality:
        location_parts.append(municipality.strip())
    location = " ".join(location_parts)
    if location:
        location += ", Switzerland"
    else:
        location = "Switzerland"

    lang = _LANG_MAP.get((purpose_language or "").lower(), "de")

    params = {
        "api_key": settings.scrapingdog_api_key,
        "query": company_name,
        "results": min(max(1, num), 100),
        "country": "ch",
        "domain": "google.ch",
        "language": lang,
        "location": location,
        "page": 0,
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.get(_SCRAPINGDOG_URL, params=params)
        if not response.is_success:
            body = response.text[:500]
            raise httpx.HTTPStatusError(
                f"ScrapingDog API HTTP {response.status_code}: {body}",
                request=response.request,
                response=response,
            )

    data: dict = response.json()
    organic = data.get("organic_results") or []
    parsed = [
        GoogleSearchResult(
            title=item.get("title", ""),
            link=item.get("link", ""),
            snippet=item.get("snippet"),
        )
        for item in organic
    ]
    return parsed, data
