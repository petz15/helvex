"""Quick test: ScrapingDog Google Search API vs current Serper.dev response shape."""

import json
import sys
import httpx

SCRAPINGDOG_KEY = "6a1f5e48e007ef611a4eecc6"
SCRAPINGDOG_URL = "https://api.scrapingdog.com/google/"

TEST_QUERIES = [
    "Muster AG Zürich",
    "Bäckerei Müller Bern",
    "Treuhand Meier GmbH Basel",
]


def search_scrapingdog(query: str, num: int = 10) -> dict:
    params = {
        "api_key": SCRAPINGDOG_KEY,
        "query": query,
        "results": num,
        "country": "ch",
        "page": 0,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(SCRAPINGDOG_URL, params=params)
        resp.raise_for_status()
    return resp.json()


def main():
    for query in TEST_QUERIES:
        print(f"\n{'='*60}")
        print(f"Query: {query}")
        print("="*60)
        try:
            data = search_scrapingdog(query)
        except httpx.HTTPStatusError as exc:
            print(f"HTTP error: {exc}")
            print(f"Response body: {exc.response.text[:500]}")
            continue
        except Exception as exc:
            print(f"Error: {exc}")
            continue

        # Show raw top-level keys so we know the response shape
        print(f"Top-level keys: {list(data.keys())}")

        organic = data.get("organic_results") or data.get("organic") or []
        print(f"Organic results count: {len(organic)}")
        for i, item in enumerate(organic[:5], 1):
            print(f"  [{i}] title : {item.get('title', '')[:70]}")
            print(f"       link  : {item.get('link', '')}")
            print(f"       snippet: {str(item.get('snippet', ''))[:80]}")

        # Dump the full first result for field inspection
        if organic:
            print(f"\nFull first result fields: {list(organic[0].keys())}")


if __name__ == "__main__":
    sys.exit(main())
