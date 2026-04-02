from __future__ import annotations

import json
import uuid
from typing import Any

from app.schemas.billing import BillingAddress


_ADDRESS_KEYS = {
    "first_name",
    "last_name",
    "street",
    "number",
    "postal_code",
    "city",
    "country",
    "company_name",
}


def _normalize_address(data: dict[str, Any]) -> dict[str, str | None]:
    model = BillingAddress.model_validate(data)
    return model.model_dump()


def parse_billing_address_book(raw: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Parse user billing_address_json into (addresses, default_id).

    Supports both legacy single-address payloads and the new address-book shape.
    """
    if not raw:
        return [], None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return [], None

    if not isinstance(payload, dict):
        return [], None

    addresses: list[dict[str, Any]] = []
    default_id: str | None = None

    # New shape: {"addresses": [...], "default_id": "..."}
    if isinstance(payload.get("addresses"), list):
        for item in payload["addresses"]:
            if not isinstance(item, dict):
                continue
            try:
                normalized = _normalize_address(item)
            except Exception:
                continue
            address_id = str(item.get("id") or uuid.uuid4().hex)
            label = str(item.get("label") or "").strip() or None
            addresses.append({"id": address_id, "label": label, **normalized})
        raw_default = payload.get("default_id")
        if isinstance(raw_default, str) and raw_default:
            default_id = raw_default

    # Legacy shape: a single BillingAddress object
    elif _ADDRESS_KEYS.intersection(payload.keys()):
        try:
            normalized = _normalize_address(payload)
            legacy_id = "legacy"
            addresses.append({"id": legacy_id, "label": "Default", **normalized})
            default_id = legacy_id
        except Exception:
            return [], None

    if not addresses:
        return [], None

    known_ids = {str(addr["id"]) for addr in addresses}
    if default_id not in known_ids:
        default_id = str(addresses[0]["id"])

    return addresses, default_id


def serialize_billing_address_book(addresses: list[dict[str, Any]], default_id: str | None) -> str:
    return json.dumps(
        {
            "addresses": addresses,
            "default_id": default_id,
        }
    )


def get_default_billing_address(raw: str | None) -> dict[str, Any] | None:
    addresses, default_id = parse_billing_address_book(raw)
    if not addresses:
        return None
    selected = next((a for a in addresses if a["id"] == default_id), addresses[0])
    return {
        "first_name": selected.get("first_name"),
        "last_name": selected.get("last_name"),
        "street": selected.get("street"),
        "number": selected.get("number"),
        "postal_code": selected.get("postal_code"),
        "city": selected.get("city"),
        "country": selected.get("country"),
        "company_name": selected.get("company_name"),
    }
