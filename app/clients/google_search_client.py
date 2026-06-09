"""Client for the Serper.dev Google Search API.

Documentation: https://serper.dev/
"""

import httpx

from app.config import settings
from app.schemas.company import GoogleSearchResult

_SERPER_URL = "https://google.serper.dev/search"


def search_website(company_name: str, *, num: int = 5, api_key: str | None = None) -> list[GoogleSearchResult]:
    """Search for a company's website using the Serper.dev API.

    Args:
        company_name: The company name to search for.
        num: Number of results to return (1-25).
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

    payload = {
        "q": company_name,
        "num": min(max(1, num), 25),
        "gl": "ch", # geographic location for search results
        "hl": "de", # language for search results -> TODO: make gl/hl configurable per request if needed
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
    return [
        GoogleSearchResult(
            title=item.get("title", ""),
            link=item.get("link", ""),
            snippet=item.get("snippet"),
        )
        for item in items
    ]
