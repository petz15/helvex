"""Client for the Swiss UID (Unternehmens-Identifikationsnummer) SOAP web service V5.0.

Endpoint (PublicServices): https://www.uid-wse.admin.ch/V5.0/PublicServices.svc
WSDL:                       https://www.uid-wse.admin.ch/V5.0/PublicServices.svc?wsdl

PublicServices key constraints (BFS spec 5.0 + empirical testing 2026-06):
  - Operation: Search (not SearchByCriteria from old V3.0)
  - Max 30 results per query — NO PAGINATION TOKEN
  - `organisationName` matching is EXACT WORD-TOKEN (tokenize by spaces, match each
    token exactly, case-insensitive, accent-folded) — NOT a SQL CONTAINS/LIKE.
    Multiple tokens are AND-ed (every result contains all words). Single ≤1-char
    tokens return 0 (min length ~2).
  - Filters that work server-side (before truncation), all confirmed empirically:
      * address.swissZipCode (PLZ)      → partition by domicile postcode
      * commercialRegisterInformation.commercialRegisterStatus  "3"=not in HR,
        "2"=in HR  → "3" isolates the non-Zefix gap (mwst/uid_only)
      * vatRegisterInformation.vatStatus "2" = registered for MWST/VAT
      * legalForm (list of eCH-0097 codes, e.g. "0101"=Einzelunternehmen)
    `personName` is silently IGNORED by the service.
  - None of these filters lift the 30-cap on their own (the gap is dense: >30
    non-HR entities per PLZ even in villages). The sweep therefore STACKS filters:
    PLZ × HR=3, then AND-refines a capped bucket with name tokens. See
    `app/services/uid_import.py:sweep_uid_gap` for orchestration.
  - GetByUID returns full address + legal form (Search omits zip/canton reliably).

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
from typing import Any, Callable

logger = logging.getLogger(__name__)

_WSDL = "https://www.uid-wse.admin.ch/V5.0/PublicServices.svc?wsdl"

# PublicServices hard limit — if we receive this many, there are more (no pagination)
_MAX_RECORDS_PER_CALL = 30

# Rate limiting (applies to ALL operations: Search and GetByUID)
_RATE_LIMIT_BASE_DELAY = 60.0   # seconds after first rate-limit error (doubles each retry)
_INTER_CALL_DELAY = 3.5         # min sleep between API calls; ~17/min leaves headroom below the API cap


class UIDRateLimitError(Exception):
    """Raised when the UID API returns Request_limit_exceeded after all retries."""


def _chunked_sleep(seconds: float, abort_cb=None, chunk: float = 5.0) -> None:
    """Sleep for `seconds` total, calling abort_cb every `chunk` seconds so
    job cancellation is detected promptly even during long rate-limit waits."""
    import time
    remaining = seconds
    while remaining > 0:
        time.sleep(min(chunk, remaining))
        remaining -= chunk
        if abort_cb:
            abort_cb()

# Search filter constants (eCH register-status codes)
_HR_REGISTERED = "2"            # commercialRegisterStatus: in Handelsregister
_HR_NOT_REGISTERED = "3"        # commercialRegisterStatus: NOT in HR → the non-Zefix gap
_VAT_REGISTERED = "2"           # vatStatus: registered for MWST/VAT

_client_cache: Any = None
_WSDL_INIT_TIMEOUT = 120  # seconds; zeep fetches WSDL + all imported XSD schemas


def _get_client() -> Any:
    global _client_cache
    if _client_cache is None:
        import concurrent.futures
        from zeep import Client, Settings
        from zeep.transports import Transport

        transport = Transport(timeout=30, operation_timeout=90)
        settings = Settings(strict=False, xml_huge_tree=True)

        # Transport(timeout=) doesn't always cover recursive XSD schema downloads —
        # wrap in a thread so the whole init has a hard ceiling.
        def _init() -> Any:
            return Client(_WSDL, transport=transport, settings=settings)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
            _future = _pool.submit(_init)
            try:
                _client_cache = _future.result(timeout=_WSDL_INIT_TIMEOUT)
            except concurrent.futures.TimeoutError:
                raise RuntimeError(
                    f"UID SOAP client init timed out after {_WSDL_INIT_TIMEOUT}s "
                    f"(WSDL or schema download hung)"
                )

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

    has_hr = comm_status == _HR_REGISTERED
    has_mwst = vat_status == _VAT_REGISTERED

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
    None if not found or request fails. Sleeps _INTER_CALL_DELAY after each call
    and retries on rate-limit errors with exponential backoff.
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


# ── Search (filtered) ─────────────────────────────────────────────────────────

def build_search_params(
    *,
    name: str | None = None,
    plz: int | str | None = None,
    canton: str | None = None,
    not_in_hr: bool = False,
    in_hr: bool = False,
    mwst_only: bool = False,
    legal_form: str | list[str] | None = None,
    active_only: bool = False,
) -> dict[str, Any]:
    """Build a `uidEntitySearchParameters` dict from high-level filters.

    swissZipCode (xsd:unsignedInt) and cantonAbbreviation live inside a repeating
    CHOICE group in addressSearchType → they go through zeep's `_value_1` list, not
    a top-level kwarg. PLZ is finer than canton; if both are given, PLZ wins.
    commercialRegisterStatus "3"=not in HR isolates the non-Zefix gap.
    """
    params: dict[str, Any] = {}
    if name:
        params["organisationName"] = name
    if plz is not None and str(plz).strip():
        params["address"] = {"_value_1": [{"swissZipCode": int(plz)}]}
    elif canton and str(canton).strip():
        params["address"] = {"_value_1": [{"cantonAbbreviation": str(canton).strip().upper()}]}
    if not_in_hr:
        params["commercialRegisterInformation"] = {"commercialRegisterStatus": _HR_NOT_REGISTERED}
    elif in_hr:
        params["commercialRegisterInformation"] = {"commercialRegisterStatus": _HR_REGISTERED}
    if mwst_only:
        params["vatRegisterInformation"] = {"vatStatus": _VAT_REGISTERED}
    if legal_form:
        params["legalForm"] = [legal_form] if isinstance(legal_form, str) else list(legal_form)
    if active_only:
        params["uidregInformation"] = {"uidregStatusEnterpriseDetail": "3"}
    return params


def _run_search(
    search_params: dict[str, Any],
    *,
    abort_cb: Callable[[], None] | None = None,
    _retry: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """One Search call. Returns (parsed_entities, capped).

    capped is True when we received the 30-result cap (there may be more — the
    caller must narrow with an additional filter/token). Retries up to 3 times on
    Request_limit_exceeded with exponential backoff. abort_cb runs after each
    successful call so the job worker can heartbeat / check cancellation.
    """
    import time
    from zeep.helpers import serialize_object

    client = _get_client()
    logger.info("uid_soap.search params=%s retry=%d", search_params, _retry)
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
        if "Request_limit_exceeded" in str(exc):
            if _retry < 3:
                delay = _RATE_LIMIT_BASE_DELAY * (2 ** _retry)
                logger.warning("UID rate limited (params=%s), waiting %.0fs (retry %d)", search_params, delay, _retry + 1)
                _chunked_sleep(delay, abort_cb=abort_cb)
                return _run_search(search_params, abort_cb=abort_cb, _retry=_retry + 1)
            raise UIDRateLimitError(f"Request_limit_exceeded after {_retry} retries (params={search_params})") from exc
        raise

    time.sleep(_INTER_CALL_DELAY)
    if abort_cb:
        abort_cb()

    result: dict = serialize_object(raw_result, target_cls=dict) or {}
    items = result.get("uidEntitySearchResultItem") or []
    if not isinstance(items, list):
        items = [items] if items else []

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
            logger.exception("Failed to parse V5.0 entity in _run_search")

    capped = len(parsed) >= _MAX_RECORDS_PER_CALL
    logger.info("uid_soap.result params=%s n=%d capped=%s", search_params, len(parsed), capped)
    return parsed, capped


def search_entities(
    *,
    name: str | None = None,
    plz: int | str | None = None,
    canton: str | None = None,
    not_in_hr: bool = False,
    in_hr: bool = False,
    mwst_only: bool = False,
    legal_form: str | list[str] | None = None,
    active_only: bool = False,
    abort_cb: Callable[[], None] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """High-level filtered Search. Returns (entities, capped). See build_search_params."""
    params = build_search_params(
        name=name, plz=plz, canton=canton, not_in_hr=not_in_hr, in_hr=in_hr,
        mwst_only=mwst_only, legal_form=legal_form, active_only=active_only,
    )
    return _run_search(params, abort_cb=abort_cb)
