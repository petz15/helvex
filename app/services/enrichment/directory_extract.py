"""Text extraction from business directory profile pages.

No API cost: trafilatura main-text + meta-description extraction + simple
heuristics for ratings and review counts. Works generically across all directory
sites — no site-specific parsers needed for the text/context use case.
"""
from __future__ import annotations

import re

_MAX_RAW_TEXT = 5000
_MAX_DESCRIPTION = 500

_RATING_RE = re.compile(
    r"(?:(?:Bewertung|Durchschnitt|Note|Rating|Score|Évaluation)[:\s]*)?(\d[.,]\d)\s*(?:/\s*5|von 5|out of 5|Sterne|stars|★)",
    re.IGNORECASE,
)
_REVIEW_COUNT_RE = re.compile(
    r"(\d[\d\s',.]*)\s*(?:Bewertungen?|Rezensionen?|Kommentare?|Erfahrungsberichte?|reviews?|avis|évaluations?)",
    re.IGNORECASE,
)


def extract_directory_page(html_bytes: bytes, url: str = "") -> dict:
    """Extract structured data from a directory page HTML.

    Returns a dict with:
      raw_text     — trafilatura main text, capped at _MAX_RAW_TEXT chars
      description  — meta description or first substantial paragraph
      rating       — float if a star rating was found, else None
      review_count — int if a review count was found, else None
      categories   — list[str] — empty list if none found
    """
    html_str: str
    try:
        html_str = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html_str = ""

    raw_text = _main_text(html_str)
    description = _meta_description(html_str) or _first_paragraph(raw_text)
    rating = _extract_rating(html_str, raw_text)
    review_count = _extract_review_count(html_str, raw_text)
    categories = _extract_categories(html_str)

    return {
        "raw_text": raw_text[:_MAX_RAW_TEXT] if raw_text else None,
        "description": description[:_MAX_DESCRIPTION] if description else None,
        "rating": rating,
        "review_count": review_count,
        "categories": categories or None,
    }


def _main_text(html_str: str) -> str:
    try:
        import trafilatura
        txt = trafilatura.extract(
            html_str,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_precision=False,
        )
        return (txt or "").strip()
    except Exception:
        return ""


def _meta_description(html_str: str) -> str:
    try:
        m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{10,})["\']',
            html_str, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']{10,})["\'][^>]+name=["\']description["\']',
            html_str, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
        m = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{10,})["\']',
            html_str, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def _first_paragraph(text: str) -> str:
    if not text:
        return ""
    for line in text.split("\n"):
        line = line.strip()
        if len(line) >= 60:
            return line[:_MAX_DESCRIPTION]
    return text[:_MAX_DESCRIPTION]


def _clean_number(s: str) -> int | None:
    cleaned = re.sub(r"[\s'.,]", "", s)
    try:
        return int(cleaned)
    except ValueError:
        return None


def _extract_rating(html_str: str, raw_text: str) -> float | None:
    for source in (raw_text, html_str):
        m = _RATING_RE.search(source)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                pass
        # Schema.org ratingValue in HTML
        rv = re.search(r'"ratingValue"[:\s]+"?(\d[.,]\d)"?', source)
        if rv:
            try:
                return float(rv.group(1).replace(",", "."))
            except ValueError:
                pass
    return None


def _extract_review_count(html_str: str, raw_text: str) -> int | None:
    for source in (raw_text, html_str):
        m = _REVIEW_COUNT_RE.search(source)
        if m:
            n = _clean_number(m.group(1))
            if n and n > 0:
                return n
        # Schema.org reviewCount
        rc = re.search(r'"reviewCount"[:\s]+"?(\d+)"?', source)
        if rc:
            n = _clean_number(rc.group(1))
            if n and n > 0:
                return n
    return None


def _extract_categories(html_str: str) -> list[str]:
    cats: list[str] = []
    # Schema.org itemType or category
    for m in re.finditer(r'"category"[:\s]+"([^"]{3,60})"', html_str):
        c = m.group(1).strip()
        if c and c not in cats:
            cats.append(c)
    # Breadcrumb items often reflect categories
    for m in re.finditer(
        r'(?:breadcrumb[^>]*>|itemtype="[^"]*BreadcrumbList[^"]*"[^>]*>)'
        r'.*?<(?:a|span)[^>]*>([A-ZÄÖÜa-zäöüàâéèêëïîôûç][^<]{3,50})</(?:a|span)>',
        html_str, re.DOTALL | re.IGNORECASE,
    ):
        c = m.group(1).strip()
        if c and c.lower() not in ("home", "startseite", "accueil", "schweiz", "switzerland") and c not in cats:
            cats.append(c)
    return cats[:10]
