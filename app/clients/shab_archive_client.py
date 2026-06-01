"""Client for the SHAB public archive API (shab.ch).

Provides paginated access to historical SHAB publications and PDF download /
text extraction.  This is a separate data source from the Zefix SOGC feed used
by shab_client.py.

sogc_id convention used by the archive importer:
    "shab_{archive_id}"  (e.g. "shab_4447021")
Zefix SOGC IDs are stored as plain numeric strings and will never collide.
"""

from __future__ import annotations

import io
import logging
import random
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Retry / backoff ───────────────────────────────────────────────────────────

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 6
_BASE_DELAY = 1.0   # seconds for first retry
_BACKOFF_CAP = 120.0  # never wait more than 2 minutes


def _backoff(attempt: int) -> float:
    """Exponential back-off with ±20 % jitter, capped at _BACKOFF_CAP."""
    delay = min(_BASE_DELAY * (2.0 ** attempt), _BACKOFF_CAP)
    return delay * random.uniform(0.8, 1.2)


def _get_with_retry(
    url: str,
    *,
    params: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> httpx.Response:
    """HTTP GET with exponential back-off on transient errors and rate limits.

    Retries on:
    •  Network errors (timeout, connection reset)
    •  HTTP 429 — honours ``Retry-After`` header when present
    •  HTTP 5xx — server-side transient errors

    Raises ``httpx.HTTPStatusError`` on non-retryable 4xx responses.
    Raises ``RuntimeError`` after all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        if attempt > 0:
            wait = _backoff(attempt - 1)
            logger.warning(
                "SHAB archive retry %d/%d in %.1fs (last: %s)",
                attempt, _MAX_RETRIES, wait, last_exc,
            )
            time.sleep(wait)

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, params=params)

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After") or _backoff(attempt))
                last_exc = RuntimeError(f"HTTP 429 — waiting {retry_after:.0f}s")
                logger.warning("SHAB archive rate-limited, waiting %.0fs", retry_after)
                time.sleep(retry_after)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                continue

            resp.raise_for_status()
            return resp

        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc

    raise RuntimeError(
        f"SHAB archive request failed after {_MAX_RETRIES} retries "
        f"(url={url}): {last_exc}"
    )

SHAB_ARCHIVE_BASE = "https://www.shab.ch/api/v1/archive"
SHAB_TENANT = "shab"

# HR subrubrics we want to import
HR_SUBRUBRICS = frozenset({"HR01", "HR02", "HR03", "HR04"})

# Swiss UID  CHE-xxx.xxx.xxx  (also tolerates spaces/dashes between digit groups)
_UID_RE = re.compile(r"CHE[-\s]?(\d{3})[.\s-]?(\d{3})[.\s-]?(\d{3})", re.IGNORECASE)


# ── Archive list API ──────────────────────────────────────────────────────────

def fetch_archive_page(
    page: int,
    *,
    size: int = 100,
    date_start: str | None = None,
    date_end: str | None = None,
    tenant: str = SHAB_TENANT,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Fetch one page from the SHAB public archive.

    Actual response shape (verified 2026-06):
    {
        "content": [...entries...],
        "total": 411124,             # count within the date window (accurate)
        "pageRequest": {"page": 0, "size": 100, "sortOrders": []},
        "queryId": null
    }

    Hard cap: page * size <= ~50,000.  Requests beyond that return HTTP 200
    with an error payload (no "content" key).

    Date-range params (``date_start`` / ``date_end``, ISO "YYYY-MM-DD") use the
    ``searchPeriod=CUSTOM`` filter which scopes the cap to the window total —
    keep windows to ≤1 month (~20,000 entries) to stay safely under the cap.
    The API only exposes HR (Handelsregister) entries regardless of other filters.
    """
    params: dict[str, str] = {
        "includeContent": "false",
        "pageRequest.page": str(page),
        "pageRequest.size": str(size),
        "tenant": tenant,
    }
    if date_start or date_end:
        params["searchPeriod"] = "CUSTOM"
        if date_start:
            params["publicationDate.start"] = date_start
        if date_end:
            params["publicationDate.end"] = date_end

    resp = _get_with_retry(f"{SHAB_ARCHIVE_BASE}/public", params=params, timeout=timeout)
    data = resp.json()
    if not isinstance(data, dict) or "content" not in data:
        err = data.get("userMessageKey") or data.get("exceptionMessage") or "unknown error"
        raise RuntimeError(f"SHAB archive API error: {err}")
    return data


def get_entries(page_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the content list from a paginated API response."""
    content = page_data.get("content") or []
    return content if isinstance(content, list) else []


def get_total_elements(page_data: dict[str, Any]) -> int:
    """Return the claimed total element count."""
    return int(page_data.get("total") or 0)


def get_page_size(page_data: dict[str, Any]) -> int:
    """Return the page size used in this response."""
    pr = page_data.get("pageRequest") or {}
    return int(pr.get("size") or len(get_entries(page_data)) or 1)


def is_last_page(page_data: dict[str, Any]) -> bool:
    """True when the returned content is smaller than the requested page size."""
    entries = get_entries(page_data)
    size = get_page_size(page_data)
    return len(entries) < size


# ── PDF download & text extraction ───────────────────────────────────────────

def fetch_pdf_bytes(
    pub_id: int | str,
    *,
    tenant: str = SHAB_TENANT,
    timeout: float = 60.0,
) -> bytes:
    """Download the PDF for a SHAB archive publication and return raw bytes."""
    url = f"{SHAB_ARCHIVE_BASE}/{pub_id}/pdf"
    resp = _get_with_retry(url, params={"tenant": tenant}, timeout=timeout)
    return resp.content


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract plain text from PDF bytes using PyMuPDF (fitz).

    PyMuPDF (MuPDF) correctly decodes the Type1/TrueType font encodings used
    in SHAB PDFs (including accented characters such as é, à, ü) which pure-
    Python parsers (pypdf, pdfminer) cannot resolve.

    Returns an empty string if PyMuPDF is unavailable or the PDF is unreadable.
    """
    try:
        import fitz  # PyMuPDF — lazy import
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts: list[str] = []
        for page in doc:
            text = page.get_text() or ""
            if text.strip():
                parts.append(text)
        return "\n".join(parts).strip()
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_uid_from_text(text: str) -> str | None:
    """Extract the first Swiss company UID (CHE-xxx.xxx.xxx) from text."""
    match = _UID_RE.search(text)
    if not match:
        return None
    d1, d2, d3 = match.group(1), match.group(2), match.group(3)
    return f"CHE-{d1}.{d2}.{d3}"


# Canton code on the line immediately after the trilingual registry header line.
# e.g. "Handelsregister - Registre du commerce - Registro di commercio \nJU\n"
_CANTON_FROM_HEADER_RE = re.compile(
    r"Registro\s+di\s+commercio\s*\n([A-Z]{2})\b",
    re.IGNORECASE,
)

_VALID_CANTONS = frozenset([
    "AG","AI","AR","BE","BL","BS","FR","GE","GL","GR","JU","LU",
    "NE","NW","OW","SG","SH","SO","SZ","TG","TI","UR","VD","VS","ZG","ZH",
])


def extract_canton_from_text(text: str) -> str | None:
    """Extract the two-letter canton code from the SHAB PDF header line."""
    m = _CANTON_FROM_HEADER_RE.search(text)
    if m:
        code = m.group(1).upper()
        if code in _VALID_CANTONS:
            return code
    return None


def is_hr_heading(heading: str | None, rubric: str | None) -> bool:
    """Return True if the entry is a Handelsregister publication.

    Checks both the top-level ``heading`` ("hr") and the ``subheading``
    (normalised to uppercase: "HR01", "HR02", "HR03", "HR04").
    """
    if (heading or "").lower() == "hr":
        return True
    return (rubric or "").upper() in HR_SUBRUBRICS


def is_hr_rubric(rubric: str | None) -> bool:
    """Return True if *rubric* is a Handelsregister entry type."""
    return (rubric or "").upper() in HR_SUBRUBRICS


def parse_entry_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalise an archive list entry into a consistent shape.

    Actual field names observed in the SHAB archive API (verified 2026-06):
        id, heading, subheading (lowercase: hr01), tenant, title,
        submitter, publicationTime (ISO datetime)
    """
    archive_id = entry.get("id") or entry.get("archiveId") or entry.get("publicationId")
    # subheading is lowercase in the real API ("hr01"), heading is "hr"
    subheading = str(entry.get("subheading") or entry.get("subRubric") or entry.get("rubric") or "")
    rubric = subheading.upper()  # normalise to uppercase HR01 / HR02 / HR03
    # publicationTime is a full ISO datetime string; take just the date part
    pub_time = str(
        entry.get("publicationTime") or entry.get("publicationDate") or entry.get("date") or ""
    )
    pub_date = pub_time[:10]  # "2018-08-31"
    # No canton field in the list response — extract from PDF text later
    canton = str(entry.get("cantonCode") or entry.get("canton") or "").upper() or None
    title = str(entry.get("title") or entry.get("name") or entry.get("companyName") or "")
    pub_number = str(entry.get("publicationNumber") or entry.get("number") or "") or None
    heading = str(entry.get("heading") or "").lower()
    return {
        "archive_id": archive_id,
        "heading": heading,
        "rubric": rubric,
        "pub_date": pub_date,
        "canton": canton,
        "title": title,
        "pub_number": pub_number,
    }
