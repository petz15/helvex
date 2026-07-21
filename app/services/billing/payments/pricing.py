"""Pricing helpers and provider-mode selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from app.services.billing.payments._types import ProviderName
from app.services.billing.tiers import calculate_custom_tier_price

# EU VAT standard rates keyed by ISO 3166-1 alpha-2 country code.
_EU_VAT_RATES: dict[str, dict] = json.loads(
    (Path(__file__).parent.parent.parent.parent / "data" / "eu_vat_rates.json").read_text(encoding="utf-8")
)["rates"]


def vat_rate_for(country: str, vat_id: str | None) -> float:
    """Return applicable VAT rate for a billing country.

    CH (Switzerland): 8.1 % (current standard rate).
    EU country: local standard rate.
    All other / unclear origin: 8.1 % (CH fallback — conservative default).
    """
    c = (country or "").upper().strip()
    if c == "CH":
        return 0.081
    eu = _EU_VAT_RATES.get(c)
    if eu:
        return eu["standard_rate"] / 100.0
    return 0.081


def apply_vat(base_chf: float, country: str, vat_id: str | None) -> tuple[float, float, float]:
    """Return (vat_rate, vat_amount_chf, total_chf) for a base CHF amount."""
    rate = vat_rate_for(country, vat_id)
    vat = round(base_chf * rate, 4)
    return rate, vat, round(base_chf + vat, 4)


_BASE_MONTHLY_CHF: dict[str, float] = {
    "free": 0.0,
    "simple": 6.0,
    "explorer": 12.0,
    "researcher": 17.0,
    "strategist": 37.0,
}


def _normalize_mode(raw: str) -> str:
    # Worldline is the only supported provider.
    return "worldline"


def get_enabled_provider_order() -> list[ProviderName]:
    """Return provider priority order. Worldline is the only supported provider."""
    return ["worldline"]


def compute_subscription_price_chf(
    *,
    tier: str,
    billing_cycle: Literal["monthly", "yearly"],
    custom_features: dict | None = None,
    verified_business: bool = False,
) -> float:
    """Compute CHF price for a tier and billing cycle.

    Yearly billing: monthly * 10. Verified business: additional 20% discount.
    """
    t = (tier or "free").strip().lower()
    if t == "custom":
        monthly = float(calculate_custom_tier_price(custom_features or {}))
    else:
        monthly = float(_BASE_MONTHLY_CHF.get(t, 0.0))

    total = monthly * (10.0 if billing_cycle == "yearly" else 1.0)
    if verified_business:
        total *= 0.80
    return round(total, 2)


def credits_to_chf(credits_amount: int) -> float:
    return round(float(credits_amount) * 0.0001, 4)


def parse_worldline_merchant_reference(reference: str | None) -> dict[str, Any]:
    """Parse order reference token created for Worldline transactions.

    Supported formats:
    - wl_sub_<org_id>_<user_id>_<tier>_<billing_cycle>_<nonce>
    - wl_topup_<org_id>_<user_id>_<credits>_<nonce>

    Backward-compatible legacy formats:
    - wl_sub_<org_id>_<nonce>
    - wl_topup_<org_id>_<nonce>
    """
    ref = (reference or "").strip()
    if not ref.startswith("wl_"):
        return {}

    parts = ref.split("_")
    if len(parts) < 4:
        return {}

    kind = parts[1]
    try:
        org_id = int(parts[2])
    except ValueError:
        return {}

    out: dict[str, Any] = {"kind": kind, "org_id": org_id}

    if kind == "sub":
        if len(parts) >= 7 and parts[3].isdigit():
            out["user_id"] = int(parts[3])
            out["tier"] = parts[4]
            out["billing_cycle"] = parts[5]
        elif len(parts) >= 6:
            out["tier"] = parts[3]
            out["billing_cycle"] = parts[4]
        return out

    if kind == "topup":
        if len(parts) >= 6 and parts[3].isdigit():
            out["user_id"] = int(parts[3])
            try:
                out["topup_credits"] = int(parts[4])
            except ValueError:
                pass
        elif len(parts) >= 5:
            try:
                out["topup_credits"] = int(parts[3])
            except ValueError:
                pass
        return out

    return {}
