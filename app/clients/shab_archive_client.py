"""Client for the SHAB public archive API (shab.ch).

Provides paginated access to historical SHAB publications and PDF download /
text extraction.  This is a separate data source from the Zefix SOGC feed used
by shab_client.py.

sogc_id conventions:
    Modern (post-2012):  "shab_{archive_id}"      (e.g. "shab_4447021")
    Old bulk PDF:        "shab_old_{YYYYMMDD}_{pub_number}"
                         (e.g. "shab_old_20020103_000018")
Zefix SOGC IDs are plain numeric strings and never collide with either prefix.
"""

from __future__ import annotations

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
    The returned text preserves original newlines so that callers can run
    structured-field extraction (UID, canton) before normalising for storage.
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


def normalize_pdf_text(text: str) -> str:
    """Normalise raw PDF text for storage and downstream regex parsing.

    SHAB PDFs use hard line-breaks, so the raw extract looks like:
        "…mit Einzel-\\nunterschrift…"   (syllable break — word has no hyphen)
        "Kommandit-\\nGesellschaft…"     (compound break — hyphen is part of word)

    Three-step normalisation:
    1. Syllable-break hyphens: ``word-\\n`` followed by lowercase → drop hyphen
    2. Compound-break hyphens: ``word-\\n`` followed by uppercase → keep hyphen
    3. Remaining newlines → single space; runs of spaces → single space
    """
    # Step 1: syllable break — "Einzelunter-\nschrift" → "Einzelunterschrift"
    text = re.sub(r'-\n([a-zäöüß])', r'\1', text)
    # Step 2: compound break — "Kommandit-\nGesellschaft" → "Kommandit-Gesellschaft"
    text = re.sub(r'-\n([A-ZÄÖÜ])', r'-\1', text)
    # Step 3: collapse all remaining newlines and tidy whitespace
    text = re.sub(r'\n', ' ', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


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


# ── Old bulk-PDF download (pre-2012) ─────────────────────────────────────────

_DAILY_PDF_URL = "https://www.shab.ch/api/v1/archive/issue-of-today"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_daily_pdf_bytes(
    date_str: str,
    *,
    language: str = "de",
    tenant: str = SHAB_TENANT,
    timeout: float = 120.0,
) -> bytes | None:
    """Download the daily bulk SHAB PDF for dates before the per-publication API era.

    Returns raw PDF bytes on success, or None if the server returns 404 (no
    issue published that day — weekend or holiday).

    The endpoint requires a browser-like User-Agent header; without it the
    server returns 404 even for valid dates.

    URL: GET /api/v1/archive/issue-of-today?date=YYYY-MM-DD&language=de&tenant=shab
    """
    params = {"date": date_str, "language": language, "tenant": tenant}
    headers = {"User-Agent": _BROWSER_UA, "Accept": "application/pdf,*/*"}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        if attempt > 0:
            wait = _backoff(attempt - 1)
            logger.warning(
                "SHAB daily-PDF retry %d/%d for %s in %.1fs",
                attempt, _MAX_RETRIES, date_str, wait,
            )
            time.sleep(wait)

        try:
            with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
                resp = client.get(_DAILY_PDF_URL, params=params)

            if resp.status_code == 404:
                return None  # no issue that day (weekend / holiday)

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After") or _backoff(attempt))
                time.sleep(retry_after)
                last_exc = RuntimeError(f"HTTP 429 — waited {retry_after:.0f}s")
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                continue

            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type and len(resp.content) < 1000:
                last_exc = RuntimeError(f"Unexpected content-type: {content_type}")
                continue

            return resp.content

        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc

    raise RuntimeError(
        f"SHAB daily PDF failed after {_MAX_RETRIES} retries "
        f"(date={date_str}): {last_exc}"
    )


# ── Bulk PDF parsing ──────────────────────────────────────────────────────────

# End of the HR section: all three languages on ONE line (section content header).
# Safe against the TOC, which has a page-number digit interrupting the whitespace
# ("Schulden-\ndenrufe\n 32\nAppel…" → hyphen + digit breaks both patterns).
_HR_END_RE = re.compile(r"Schuldenrufe\s+Appel aux cr")

# Entry terminator — three distinct eras of the SHAB PDF format:
#
# Format 1/2 (2002–mid-2008): keyword on its own line, then (pub / CH) on the next.
#   "Tagebuch Nr. 9488 vom 21.12.2001\n(000018 / CH-400.3.002.852-4)"   — space before Nr
#   "Tagesregister-Nr. 3555 vom 28.04.2008\n(04460582 / CH-400.1.030.375-6)" — hyphen, 8-digit
# Note: "Tagebuch Nr." has a SPACE; "Tagesregister-Nr." has a HYPHEN — handled separately.
# Groups: (1) pub_number, (2) ch_number
_FMT12_RE = re.compile(
    r"(?:Tagebuch Nr\.|Tagesregister-Nr\.)\s+\d+\s+vom\s+\d{2}\.\d{2}\.\d{4}\s*\n"
    r"\((\d+)\s*/\s*(CH-[\d.]+-\d)\)",
    re.MULTILINE,
)

# Format 3 (2009–2012): all on one line with slash separators, CH first then pub.
#   "Tagesregister-Nr. 2987 vom 09.03.2010 / CH-400.1.031.931-8 / 05541344"
# Groups: (1) ch_number, (2) pub_number  ← swapped vs FMT12
_FMT3_RE = re.compile(
    r"Tagesregister-Nr\.\s+\d+\s+vom\s+\d{2}\.\d{2}\.\d{4}\s*/\s*(CH-[\d.]+-\d)\s*/\s*(\d+)",
    re.MULTILINE,
)

# Keep legacy name so existing callers that might import it still compile.
_TAGEBUCH_RE = _FMT12_RE

# Canton section header: two-letter code on its own line (optionally repeated)
_CANTON_HEADER_RE = re.compile(
    r"(?:^|\n)(AG|AI|AR|BE|BL|BS|FR|GE|GL|GR|JU|LU|NE|NW|OW|SG|SH|SO|SZ|TG|TI|UR|VD|VS|ZG|ZH)\n",
    re.MULTILINE,
)

# Subsection type (German keyword suffices — French/Italian also present but not needed)
_SUBTYPE_RE = re.compile(
    r"\b(Neueintragungen|Mutationen|L.schungen|Aufl.sungen|Konkurse|Zweigniederlassungen)\b",
    re.IGNORECASE,
)

# First line of an entry body.
# Pre-2008: "I CompanyName AG, ..."  (Roman numeral I as section marker)
# 2008+:    bullet glyph + company name.  The glyph encoding varies by PDF era:
#   2008-2011: PyMuPDF maps the bullet to \x84 (U+0084, C1 control)
#   2012+:     PyMuPDF maps it to a PUA code point (e.g. U+F06E from Wingdings encoding)
# Matching: I<space>, \x84, ■ (U+25A0 as-is), or any PUA character (U+E000–U+F8FF).
_ENTRY_START_RE = re.compile(r"(?:^|\n)(?:I\s+|[\x84■-]\s*)(.+?)(?:\n|$)")

# City from first line: ", [bisher ]in City,"
_CITY_RE = re.compile(r",\s+(?:bisher\s+)?in\s+([^,\n]+)")


def check_bulk_pdf_structure(pdf_text: str, date_str: str) -> dict[str, Any]:
    """Validate the structural markers of a daily SHAB bulk PDF.

    Returns a dict with:
        hr_end_found        bool   — False means HR section boundary not found.
        tagebuch_count      int    — total entry delimiters found (all format eras).
        warnings            list   — human-readable descriptions of any issues.
        critical            bool   — True when data integrity is at risk.

    Call this BEFORE parse_bulk_hr_entries.  If ``critical`` is True, skip
    importing and surface the issue via the Error Center.
    """
    m_end = _HR_END_RE.search(pdf_text)
    # Count delimiters across all three format eras
    fmt12_count = len(_FMT12_RE.findall(pdf_text))
    fmt3_count = len(_FMT3_RE.findall(pdf_text))
    tagebuch_count = fmt12_count + fmt3_count
    warnings: list[str] = []
    critical = False

    if not m_end:
        warnings.append(
            f"[{date_str}] HR section end marker ('Schuldenrufe Appel aux…') not found. "
            "The PDF may have changed structure — importing without this boundary risks "
            "including non-HR entries (Schuldenrufe, Beschaffungswesen, …) as HR records. "
            "Entries for this day have been skipped."
        )
        critical = True

    if tagebuch_count == 0:
        warnings.append(
            f"[{date_str}] No entry delimiters found (tried Tagebuch/Tagesregister formats). "
            "The per-entry delimiter format may have changed — no entries will be parsed."
        )
        critical = True

    return {
        "hr_end_found": m_end is not None,
        "tagebuch_count": tagebuch_count,
        "warnings": warnings,
        "critical": critical,
    }


def _find_entry_delimiters(
    text: str,
) -> list[tuple[str, str, re.Match]]:
    """Return (pub_number, ch_number, match) for each HR entry delimiter in text.

    Tries format 1/2 (newline + parenthesized block) first; falls back to
    format 3 (single-line slash separators) if the earlier format yields nothing.
    """
    matches = list(_FMT12_RE.finditer(text))
    if matches:
        return [(m.group(1), m.group(2), m) for m in matches]
    matches = list(_FMT3_RE.finditer(text))
    if matches:
        # Format 3: groups are (ch_number, pub_number) — swap to normalise
        return [(m.group(2), m.group(1), m) for m in matches]
    return []


def parse_bulk_hr_entries(pdf_text: str, pub_date: str) -> list[dict[str, Any]]:
    """Split a daily bulk SHAB PDF (pre-2012 format) into individual HR entry dicts.

    Returns one dict per HR entry found.  Each dict has keys:
        pub_number  — sequential entry number within the issue (6 or 8 digits)
        ch_number   — old Swiss cantonal register number (CH-XXX.X.XXX.XXX-X)
        pub_date    — ISO date string of the SHAB issue
        canton      — two-letter canton code (None if not detectable)
        sub_rubric  — "HR01"/"HR02"/"HR03"/"HR04" or "HR" if unknown
        title       — company name (first line of entry, may be truncated at line-break)
        city        — city of the registered seat, or None
        text        — normalized full entry text (German; mixed-language content preserved)

    Three delimiter formats across the archive eras are handled automatically
    (see _FMT12_RE / _FMT3_RE).  The delimiter marks the END of an entry.
    Canton and subsection-type headers appear in inter-entry whitespace and are
    tracked as running state.
    """
    # Truncate at the first non-HR section (Schuldenrufe / creditor calls)
    m_end = _HR_END_RE.search(pdf_text)
    hr_text = pdf_text[: m_end.start()] if m_end else pdf_text

    delimiters = _find_entry_delimiters(hr_text)
    if not delimiters:
        return []

    entries: list[dict[str, Any]] = []
    current_canton: str | None = None
    current_subtype: str = "HR"

    for i, (pub_number, ch_number, delim) in enumerate(delimiters):
        # Text that ends at this delimiter = body of the entry identified by pub_number
        if i == 0:
            segment = hr_text[: delim.start()]
        else:
            segment = hr_text[delimiters[i - 1][2].end() : delim.start()]

        # ── Canton tracking ───────────────────────────────────────────────────
        canton_m = _CANTON_HEADER_RE.search(segment)
        if canton_m:
            current_canton = canton_m.group(1)

        # ── Subsection type ───────────────────────────────────────────────────
        subtype_m = _SUBTYPE_RE.search(segment)
        if subtype_m:
            word = subtype_m.group(1).lower()
            if "neueintrag" in word:
                current_subtype = "HR01"
            elif "mutation" in word:
                current_subtype = "HR02"
            elif "löschung" in word or "auflös" in word or "lschung" in word:
                current_subtype = "HR03"
            elif "konkurs" in word:
                current_subtype = "HR04"
            # Zweigniederlassungen → keep current

        # ── Title / city ──────────────────────────────────────────────────────
        # Use the LAST match — the first segment includes TOC/cover text before
        # the actual entry, so earlier matches are page-header noise.
        title = ""
        city: str | None = None
        title_matches = _ENTRY_START_RE.findall(segment)
        if title_matches:
            title = title_matches[-1].strip()
            city_m = _CITY_RE.search(title)
            if city_m:
                city = city_m.group(1).strip().rstrip(".")

        entry_text = normalize_pdf_text(segment)

        entries.append({
            "pub_number": pub_number,
            "ch_number": ch_number,
            "pub_date": pub_date,
            "canton": current_canton,
            "sub_rubric": current_subtype,
            "title": title[:512],
            "city": city,
            "text": entry_text,
        })

    return entries


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
