"""Phase-0b experiment: can PLZ and HR-status filters narrow a capped UID Search?

Two questions, both decided empirically (the client runs strict=False, so a
wrong field PATH is silently dropped and looks like "filter ignored" — that's
why we introspect the schema first):

  Q1  Does `commercialRegisterInformation.commercialRegisterStatus` filter work
      server-side? (status 3 = NOT in HR → isolates the non-Zefix gap population;
      status 2 = in HR → control.) Verifiable from results: entity_to_dict derives
      `registration_type` ∈ {hr, both, mwst, uid_only}.

  Q2  Does an address PLZ (`swissZipCode`) filter, COMBINED with a name token,
      narrow below the 30-cap? (The old "PLZ caps everywhere" note was PLZ-alone.)

Stage 1 prints the real `uidEntitySearchParameters` signature + referenced
sub-types so we use correct field paths. Stage 2 runs the probes.

Run from project root:
    python scripts/uid_phase0_filters.py
    python scripts/uid_phase0_filters.py --name TREUHAND --plz 8001
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


# ── Stage 1: schema introspection ────────────────────────────────────────────

def introspect() -> None:
    """Print the search-parameter type and key sub-types so we know real paths."""
    from app.clients.uid_client import _get_client

    client = _get_client()
    schema = client.wsdl.types

    wanted = (
        "uidEntitySearchParameters",
        "commercialRegisterInformation",
        "addressType",            # eCH-0010 address
        "uidStructure",
    )
    print("── Stage 1: search-parameter schema " + "-" * 35)
    seen = 0
    for t in schema.types:
        name = getattr(t, "name", None) or getattr(getattr(t, "qname", None), "localname", "")
        if not name:
            continue
        if any(w.lower() in name.lower() for w in wanted):
            try:
                sig = t.signature(schema=schema)
            except Exception as exc:
                sig = f"<no signature: {exc}>"
            print(f"\n  TYPE {name}:\n    {sig}")
            seen += 1
    if not seen:
        print("  (no matching types found — dumping all type names for reference)")
        for t in schema.types:
            name = getattr(t, "name", None) or getattr(getattr(t, "qname", None), "localname", "")
            if name:
                print(f"    {name}")
    print()


# ── Flexible Search call (mirrors _search_page, custom params) ────────────────

def raw_search(search_params: dict, *, _retry: int = 0) -> tuple[list[dict], bool]:
    """Run one Search with arbitrary uidEntitySearchParameters.

    Returns (parsed_entities, capped). Retries on rate-limit like production.
    """
    from app.clients.uid_client import (
        _MAX_RECORDS_PER_CALL,
        _RATE_LIMIT_BASE_DELAY,
        _INTER_CALL_DELAY,
        _get_client,
        entity_to_dict,
    )
    from zeep.helpers import serialize_object

    client = _get_client()
    try:
        raw = client.service.Search(
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
            print(f"    rate limited, waiting {delay:.0f}s (retry {_retry + 1})")
            time.sleep(delay)
            return raw_search(search_params, _retry=_retry + 1)
        print(f"    !! Search failed for {search_params}: {exc}")
        return [], False

    time.sleep(_INTER_CALL_DELAY)
    result = serialize_object(raw, target_cls=dict) or {}
    items = result.get("uidEntitySearchResultItem") or []
    if not isinstance(items, list):
        items = [items] if items else []
    parsed = []
    for item in items:
        if not isinstance(item, dict):
            continue
        d = entity_to_dict(item.get("organisation") or {})
        if d.get("uid"):
            parsed.append(d)
    capped = len(parsed) >= _MAX_RECORDS_PER_CALL
    return parsed, capped


def _regtype_breakdown(entities: list[dict]) -> Counter:
    return Counter(e.get("registration_type") for e in entities)


def _zip_breakdown(entities: list[dict]) -> Counter:
    return Counter(e.get("address_zip") or "—" for e in entities)


def _uidset(entities: list[dict]) -> set[str]:
    return {e["uid"] for e in entities if e.get("uid")}


# ── Stage 2: filter probes ───────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="TREUHAND", help="Name token that caps at 30 alone.")
    ap.add_argument("--plz", default="8001", help="A busy postal code to test PLZ filtering.")
    ap.add_argument("--no-introspect", action="store_true", help="Skip schema dump.")
    args = ap.parse_args()
    name, plz = args.name, args.plz

    print("=" * 72)
    print("UID Phase-0b: PLZ + HR-status filters")
    print("=" * 72)

    if not args.no_introspect:
        introspect()

    # Baseline: name token alone
    print("── Stage 2: filter probes " + "-" * 45)
    base, base_capped = raw_search({"organisationName": name})
    base_uids = _uidset(base)
    print(f"\n  BASELINE  {name!r}: {len(base)} results"
          + (" [CAPPED@30]" if base_capped else ""))
    print(f"    registration_type: {dict(_regtype_breakdown(base))}")

    # Q1a: HR status = 3 (NOT in commercial register) → should isolate the gap
    hr3, hr3_capped = raw_search({
        "organisationName": name,
        "commercialRegisterInformation": {"commercialRegisterStatus": "3"},
    })
    print(f"\n  +HR=3 (not in HR)  {name!r}: {len(hr3)} results"
          + (" [CAPPED@30]" if hr3_capped else ""))
    print(f"    registration_type: {dict(_regtype_breakdown(hr3))}")

    # Q1b: HR status = 2 (in commercial register) → control
    hr2, hr2_capped = raw_search({
        "organisationName": name,
        "commercialRegisterInformation": {"commercialRegisterStatus": "2"},
    })
    print(f"\n  +HR=2 (in HR)  {name!r}: {len(hr2)} results"
          + (" [CAPPED@30]" if hr2_capped else ""))
    print(f"    registration_type: {dict(_regtype_breakdown(hr2))}")

    # Q2: name + PLZ. swissZipCode is xsd:unsignedInt inside a repeating choice
    # group in addressSearchType, so it goes through zeep's _value_1 list, not a
    # top-level kwarg (see Stage-1 signature).
    pz, pz_capped = raw_search({
        "organisationName": name,
        "address": {"_value_1": [{"swissZipCode": int(plz)}]},
    })
    print(f"\n  +PLZ={plz}  {name!r}: {len(pz)} results"
          + (" [CAPPED@30]" if pz_capped else ""))
    print(f"    zips in results: {dict(_zip_breakdown(pz))}")

    # ── Interpretation ───────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("READINGS")
    print("=" * 72)

    # HR filter verdict
    hr3_types = set(_regtype_breakdown(hr3))
    hr2_types = set(_regtype_breakdown(hr2))
    base_types = set(_regtype_breakdown(base))
    hr_changed = (_uidset(hr3) != base_uids) or (_uidset(hr2) != base_uids)
    hr3_pure = hr3 and hr3_types <= {"mwst", "uid_only"}
    hr2_pure = hr2 and hr2_types <= {"hr", "both"}
    if not hr_changed and (base_types - {"hr", "both"}):
        hr_verdict = ("IGNORED — HR filter had no effect (same set as baseline). "
                      "Wrong field path (check Stage-1 signature) or unsupported.")
    elif hr3_pure and hr2_pure:
        hr_verdict = ("WORKS server-side — HR=3 returns only non-HR (mwst/uid_only), "
                      "HR=2 only HR. ⇒ Sweep non-HR directly to isolate the gap.")
    elif hr3 and hr3_types <= {"mwst", "uid_only"}:
        hr_verdict = "HR=3 looks honored (non-HR only); HR=2 control unclear — inspect counts."
    else:
        hr_verdict = ("PARTIAL/UNCLEAR — results mix registration types; "
                      f"HR=3 types={hr3_types}, HR=2 types={hr2_types}. Inspect manually.")
    print(f"  HR filter : {hr_verdict}")

    # PLZ filter verdict
    pz_uids = _uidset(pz)
    pz_subset = pz_uids <= base_uids if base_capped is False else None
    if pz and not pz_capped and len(pz) < len(base):
        pz_zips = {e.get("address_zip") for e in pz if e.get("address_zip")}
        if pz_zips and pz_zips <= {plz}:
            pz_verdict = (f"WORKS — narrowed to {len(pz)} and all returned zips == {plz}. "
                          "PLZ is a usable second filter when combined with a name token.")
        elif not pz_zips:
            pz_verdict = (f"NARROWED to {len(pz)} (< baseline {len(base)}) but Search omits "
                          "zips so can't confirm server-side; likely working — verify via GetByUID.")
        else:
            pz_verdict = (f"SUSPECT — narrowed but zips don't all match {plz}: {pz_zips}. "
                          "Filter may be ignored and the drop is coincidental.")
    elif _uidset(pz) == base_uids:
        pz_verdict = "IGNORED — identical to baseline (wrong path or unsupported)."
    elif pz_capped:
        pz_verdict = f"still CAPPED@30 with PLZ — try a smaller-town PLZ to see if it ever drops."
    else:
        pz_verdict = f"UNCLEAR — {len(pz)} results; inspect counts/zips above."
    print(f"  PLZ filter: {pz_verdict}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
