"""Client for the Swiss UID (Unternehmens-Identifikationsnummer) SOAP web service V5.0.

Endpoint (PublicServices): https://www.uid-wse.admin.ch/V5.0/PublicServices.svc
WSDL:                       https://www.uid-wse.admin.ch/V5.0/PublicServices.svc?wsdl

PublicServices key constraints (from BFS specification 5.0, Oktober 2018):
  - Operation: Search (not SearchByCriteria from old V3.0)
  - Max 30 results per query — NO PAGINATION TOKEN
  - searchMode=Normal does a SQL CONTAINS search (not prefix/starts-with)
  - Single-character organisationName returns 0 results (min length ~2)
  - GetByUID returns full address and legal form data (absent from Search results)

Sweep strategy (see uid_import.py for orchestration):
  - Iterate all 2-char pairs from _SWEEP_CHARS as the base search term
  - If a pair returns exactly 30 results (the cap), recursively add a 3rd char
  - "Contains" semantics guarantee every company is reached by at least one 2-char pair
    (every company name has at least one 2-char consecutive substring in our alphabet)
  - Deduplication via seen_uids set handles overlap between prefix buckets

Register type codes (CH.* in OtherOrganisationId):
  CH.HR    — Handelsregister (commercial register)
  CH.MWST  — Mehrwertsteuer / VAT
  CH.AHV   — AHV/IV/EO employer
  CH.SUVA  — Accident insurance
  CH.STAT  — Statistical register (BFS)

legalForm code examples (eCH-0097):
  0101 = Einzelunternehmen, 0106 = GmbH, 0109 = AG, 0111 = Verein, 0113 = Stiftung

uidregStatusEnterpriseDetail values:
  1 = provisional, 3 = active, 4 = cancelled, 5 = liquidated
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_WSDL = "https://www.uid-wse.admin.ch/V5.0/PublicServices.svc?wsdl"

# PublicServices hard limit — if we receive exactly this many, there are more
_MAX_RECORDS_PER_CALL = 30

_client_cache: Any = None


def _get_client() -> Any:
    global _client_cache
    if _client_cache is None:
        from zeep import Client, Settings
        from zeep.transports import Transport

        transport = Transport(timeout=30, operation_timeout=90)
        settings = Settings(strict=False, xml_huge_tree=True)
        _client_cache = Client(_WSDL, transport=transport, settings=settings)
        logger.info("UID SOAP V5.0 PublicServices client initialised from WSDL")
    return _client_cache


# ── UID number formatting ─────────────────────────────────────────────────────

def format_uid(uid_id: int | str | None) -> str | None:
    """Return CHE-xxx.xxx.xxx from a raw 9-digit UID number or existing CHE string."""
    if uid_id is None:
        return None
    s = str(uid_id).replace(".", "").replace("-", "").upper()
    if s.startswith("CHE"):
        s = s[3:]
    if len(s) == 9 and s.isdigit():
        return f"CHE-{s[:3]}.{s[3:6]}.{s[6:]}"
    return f"CHE-{s}" if s else None


# ── Response parsing helpers ──────────────────────────────────────────────────

def _str_or_none(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _first(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _format_address(addr: dict | None) -> str | None:
    if not addr:
        return None
    parts: list[str] = []
    street = _str_or_none(addr.get("street"))
    house = _str_or_none(addr.get("houseNumber"))
    if street and house:
        parts.append(f"{street} {house}")
    elif street:
        parts.append(street)
    zip_code = _str_or_none(addr.get("swissZipCode"))
    town = _str_or_none(addr.get("town"))
    if zip_code and town:
        parts.append(f"{zip_code} {town}")
    elif town:
        parts.append(town)
    country = _str_or_none(addr.get("countryIdISO2"))
    if country and country.upper() != "CH":
        parts.append(country)
    return ", ".join(parts) if parts else None


_STATUS_MAP = {
    "1": "PROVISIONAL",
    "3": "ACTIVE",
    "4": "CANCELLED",
    "5": "CANCELLED",
}


def entity_to_dict(org_outer: dict) -> dict[str, Any]:
    """Convert a zeep-serialised V5.0 uidEntitySearchResultItem.organisation dict.

    The V5.0 response nests organisation inside organisation:
      org_outer  = eCH-0108:organisationType
      org_inner  = org_outer['organisation']  — actual company data
      oid        = org_inner['organisationIdentification']
    """
    org_inner: dict = org_outer.get("organisation") or {}
    oid: dict = org_inner.get("organisationIdentification") or {}

    # UID
    uid_obj: dict = oid.get("uid") or {}
    uid_str = format_uid(uid_obj.get("uidOrganisationId"))

    name = _str_or_none(oid.get("organisationName"))

    # Legal form — V5.0 returns a numeric code (e.g. "0106" for GmbH)
    legal_form_code = _str_or_none(oid.get("legalForm"))

    # Address — prefer category=LEGAL, otherwise first address
    addresses = org_inner.get("address") or []
    if not isinstance(addresses, list):
        addresses = [addresses]
    legal_addr = next(
        (a for a in addresses if isinstance(a, dict) and a.get("addressCategory") == "LEGAL"),
        None,
    )
    if legal_addr is None and addresses:
        legal_addr = addresses[0] if isinstance(addresses[0], dict) else None

    address_str = _format_address(legal_addr)
    zip_code = _str_or_none((legal_addr or {}).get("swissZipCode"))
    town = _str_or_none((legal_addr or {}).get("town"))
    canton = _str_or_none((legal_addr or {}).get("cantonAbbreviation"))
    if canton:
        canton = canton.upper()

    # Status from uidregStatusEnterpriseDetail
    uid_info: dict = org_outer.get("uidregInformation") or {}
    status_code = _str_or_none(uid_info.get("uidregStatusEnterpriseDetail"))
    status = _STATUS_MAP.get(status_code or "", "UNKNOWN")

    # Registration type from commercialRegisterStatus + vatRegisterInformation
    comm: dict = org_outer.get("commercialRegisterInformation") or {}
    comm_status = _str_or_none(comm.get("commercialRegisterStatus"))
    vat: dict | None = org_outer.get("vatRegisterInformation") or None
    vat_status = _str_or_none((vat or {}).get("vatStatus")) if vat else None

    has_hr = comm_status == "2"    # 2 = im HR eingetragen
    has_mwst = vat_status == "2"   # 2 = im MWST-Register eingetragen

    if has_hr and has_mwst:
        reg_type = "both"
    elif has_hr:
        reg_type = "hr"
    elif has_mwst:
        reg_type = "mwst"
    else:
        reg_type = "uid_only"

    return {
        "uid": uid_str,
        "name": name,
        "status": status,
        "legal_form": legal_form_code,
        "municipality": town,
        "canton": canton,
        "address": address_str,
        "address_city": town,
        "address_zip": zip_code,
        "registration_type": reg_type,
        "source": "uid",
    }


def detail_to_update(org_outer: dict) -> dict[str, Any]:
    """Extract the fields that GetByUID adds on top of Search results.

    Returns a dict of Company column names → values (only non-None).
    """
    org_inner: dict = org_outer.get("organisation") or {}
    oid: dict = org_inner.get("organisationIdentification") or {}

    legal_form_code = _str_or_none(oid.get("legalForm"))

    addresses = org_inner.get("address") or []
    if not isinstance(addresses, list):
        addresses = [addresses]
    legal_addr = next(
        (a for a in addresses if isinstance(a, dict) and a.get("addressCategory") == "LEGAL"),
        None,
    )
    if legal_addr is None and addresses:
        legal_addr = addresses[0] if isinstance(addresses[0], dict) else None

    address_str = _format_address(legal_addr)
    zip_code = _str_or_none((legal_addr or {}).get("swissZipCode"))
    town = _str_or_none((legal_addr or {}).get("town"))
    canton = _str_or_none((legal_addr or {}).get("cantonAbbreviation"))
    if canton:
        canton = canton.upper()

    update: dict[str, Any] = {}
    if legal_form_code:
        update["legal_form"] = legal_form_code
    if address_str:
        update["address"] = address_str
    if zip_code:
        update["address_zip"] = zip_code
    if town:
        update["municipality"] = town
        update["address_city"] = town
    if canton:
        update["canton"] = canton
    return update


# ── GetByUID detail call ──────────────────────────────────────────────────────

def get_by_uid(uid_str: str, *, _retry: int = 0) -> dict[str, Any] | None:
    """Fetch the full detail record for a single entity via GetByUID.

    Returns the V5.0 organisation dict (same shape as entity_to_dict input) or
    None if not found or request fails.

    GetByUID provides address details that Search does not reliably return
    (it returns the full eCH-0108 record including legal address, contact, etc.).
    Sleeps _INTER_CALL_DELAY after each call and retries on rate-limit errors
    with the same exponential backoff as _search_page.
    """
    import time
    from zeep.helpers import serialize_object

    s = uid_str.upper().replace("CHE-", "").replace(".", "")
    try:
        uid_id = int(s)
    except ValueError:
        logger.warning("Cannot parse UID string for GetByUID: %r", uid_str)
        return None

    client = _get_client()
    try:
        raw = client.service.GetByUID(
            uid={
                "uidOrganisationIdCategorie": "CHE",
                "uidOrganisationId": uid_id,
            }
        )
    except Exception as exc:
        if "Request_limit_exceeded" in str(exc) and _retry < 3:
            delay = _RATE_LIMIT_BASE_DELAY * (2 ** _retry)
            logger.warning("UID rate limited (GetByUID uid=%s), waiting %.0fs (retry %d)", uid_str, delay, _retry + 1)
            time.sleep(delay)
            return get_by_uid(uid_str, _retry=_retry + 1)
        logger.debug("GetByUID failed for %s", uid_str, exc_info=True)
        return None

    time.sleep(_INTER_CALL_DELAY)

    serialized = serialize_object(raw, target_cls=dict)
    # GetByUID returns a list wrapping a single organisationType in V5.0
    if isinstance(serialized, list):
        serialized = serialized[0] if serialized else None
    entity: dict = serialized or {}
    return entity if entity else None


# ── Prefix sweep characters ───────────────────────────────────────────────────

# Letters-only alphabet for the 2-char pair sweep (39 chars → 39²=1521 pairs).
# Digits are intentionally excluded: numeric substrings like "00" or "20" appear
# in too many company names (years, codes) and trigger recursive expansion that
# can generate tens of thousands of API calls for a single pair. Every Swiss
# company name contains at least one consecutive alpha pair, so completeness is
# preserved. Digits are still included in _EXPANSION_CHARS so sub-prefix
# expansion can narrow down alpha buckets that hit the 30-result cap.
_PAIR_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜÉÈÀÂÊÎÔÛÇÑ"

# Kept for back-compat; not used for pair generation anymore.
_SWEEP_CHARS = "0123456789" + _PAIR_CHARS

# Additional expansion characters for deeper sub-prefix sweeps
_EXPANSION_CHARS = _SWEEP_CHARS + "-. &/()"

# If a 2-char prefix hits exactly MAX results, sub-prefix to get a complete set.
# 29 means "if we got 30 back (the cap), definitely expand".
_EXPAND_THRESHOLD = _MAX_RECORDS_PER_CALL - 1


# ── Low-level single Search call ──────────────────────────────────────────────

_RATE_LIMIT_BASE_DELAY = 60.0   # seconds to wait after first rate-limit error (doubles each retry)
_INTER_CALL_DELAY = 3.0          # minimum sleep between API calls (~20 calls/min, safely under throttle)


def _search_page(
    name_prefix: str,
    *,
    active_only: bool,
    abort_cb: "Callable[[], None] | None" = None,
    _retry: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """One Search call returning (entities, effective_total).

    effective_total:
      - len(entities) when < _MAX_RECORDS_PER_CALL → we received all results
      - _MAX_RECORDS_PER_CALL + 1 when == MAX → there may be more; caller must expand

    Retries up to 3 times on Request_limit_exceeded with exponential backoff.
    abort_cb is called after each sleep so the job worker can update its heartbeat
    and check for cancellation without blocking the SOAP call itself.
    """
    import time
    from zeep.helpers import serialize_object

    client = _get_client()

    search_params: dict[str, Any] = {"organisationName": name_prefix}
    if active_only:
        search_params["uidregInformation"] = {"uidregStatusEnterpriseDetail": "3"}

    try:
        raw_result = client.service.Search(
            searchParameters={"uidEntitySearchParameters": search_params},
            config={
                "searchMode": "Normal",
                "maxNumberOfRecords": _MAX_RECORDS_PER_CALL,
                "searchNameAndAddressHistory": False,
            },
        )
    except Exception as exc:
        if "Request_limit_exceeded" in str(exc) and _retry < 3:
            delay = _RATE_LIMIT_BASE_DELAY * (2 ** _retry)
            logger.warning("UID rate limited (prefix=%r), waiting %.0fs (retry %d)", name_prefix, delay, _retry + 1)
            time.sleep(delay)
            return _search_page(name_prefix, active_only=active_only, abort_cb=abort_cb, _retry=_retry + 1)
        raise

    time.sleep(_INTER_CALL_DELAY)
    if abort_cb:
        abort_cb()

    result: dict = serialize_object(raw_result, target_cls=dict) or {}
    items = result.get("uidEntitySearchResultItem") or []
    if not isinstance(items, list):
        items = [items] if items else []

    effective_total = len(items) if len(items) < _MAX_RECORDS_PER_CALL else _MAX_RECORDS_PER_CALL + 1

    parsed: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        org_outer: dict = item.get("organisation") or {}
        try:
            d = entity_to_dict(org_outer)
            if not d.get("uid"):
                continue
            d["_raw"] = org_outer
            parsed.append(d)
        except Exception:
            logger.exception("Failed to parse V5.0 entity in _search_page")

    return parsed, effective_total


# ── Recursive prefix sweep ────────────────────────────────────────────────────

def iter_entities_by_prefix(
    prefix: str,
    *,
    active_only: bool,
    seen_uids: set[str],
    abort_cb: "Callable[[], None] | None" = None,
    _depth: int = 0,
) -> list[dict[str, Any]]:
    """Return all entities whose name contains *prefix* as a substring.

    V5.0 Search has no pagination — it returns at most 30 results. When the
    cap is hit, expand by appending one more character from _EXPANSION_CHARS,
    and recurse. Stop recursing at depth 3 (4-char prefixes) to bound API calls.

    Deduplication is done via seen_uids so entities matched by multiple prefixes
    (contains semantics) are inserted only once.
    abort_cb is forwarded to every _search_page call so the heartbeat stays alive
    during deep recursive expansion (which can make hundreds of SOAP calls).
    """
    _MAX_DEPTH = 3

    try:
        entities, effective_total = _search_page(prefix, active_only=active_only, abort_cb=abort_cb)
    except Exception:
        logger.warning("UID prefix search failed: prefix=%r", prefix, exc_info=True)
        return []

    if effective_total == 0:
        return []

    if effective_total > _EXPAND_THRESHOLD and _depth < _MAX_DEPTH:
        results: list[dict] = []
        for ch in _EXPANSION_CHARS:
            sub = iter_entities_by_prefix(
                prefix + ch,
                active_only=active_only,
                seen_uids=seen_uids,
                abort_cb=abort_cb,
                _depth=_depth + 1,
            )
            results.extend(sub)
        return results

    # Under cap or at max depth — deduplicate and return
    result: list[dict] = []
    for e in entities:
        uid = e.get("uid")
        if uid and uid not in seen_uids:
            seen_uids.add(uid)
            result.append(e)
    return result
