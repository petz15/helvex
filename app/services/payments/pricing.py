"""Pricing helpers and provider-mode selection."""

from __future__ import annotations

from typing import Any, Literal

from app.config import settings
from app.services.payments._types import ProviderName
from app.services.tiers import calculate_custom_tier_price


_BASE_MONTHLY_CHF: dict[str, float] = {
    "free": 0.0,
    "simple": 6.0,
    "explorer": 12.0,
    "researcher": 17.0,
    "strategist": 37.0,
}


def _normalize_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    return mode if mode in {"worldline", "stripe", "dual"} else "worldline"


def get_enabled_provider_order() -> list[ProviderName]:
    """Return provider priority order based on PAYMENT_PROVIDER_MODE.

    dual mode keeps worldline first for backward-compatibility.
    """
    mode = _normalize_mode(settings.payment_provider_mode)
    if mode == "stripe":
        return ["stripe"]
    if mode == "dual":
        return ["worldline", "stripe"]
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
