"""Payment provider abstraction — thin facade re-exporting all sub-modules.

Implementation is split into focused modules:
  _types.py              — Shared types: ProviderName, CheckoutSession, PaymentConfigurationError
  pricing.py             — Price calculations, provider mode selection
  worldline_provider.py  — WorldlineProvider class and Worldline-specific helpers
  transactions.py        — Subscription lifecycle and credit grant application
"""

from __future__ import annotations

from typing import Literal

# ── Re-exports (backward compatibility) ───────────────────────────────────────

from app.services.billing.payments._types import (  # noqa: F401
    CheckoutSession,
    PaymentConfigurationError,
    PaymentProvider,
    ProviderName,
)

from app.services.billing.payments.pricing import (  # noqa: F401
    _BASE_MONTHLY_CHF,
    _normalize_mode,
    compute_subscription_price_chf,
    credits_to_chf,
    get_enabled_provider_order,
    parse_worldline_merchant_reference,
)

from app.services.billing.payments.worldline_provider import (  # noqa: F401
    WorldlineProvider,
    clear_pending_card_alias_token,
    create_worldline_callback_context,
    decode_worldline_callback_context,
    get_pending_card_alias_token,
    register_pending_card_alias_token,
)

from app.services.billing.payments.transactions import (  # noqa: F401
    apply_credit_topup,
    apply_subscription_update,
    parse_json_payload,
)

# billing.py accesses payments.settings.X — keep this re-export
from app.config import settings  # noqa: F401

import httpx  # noqa: F401 — re-exported so tests can patch payments.httpx.Client


# ── Provider factory ───────────────────────────────────────────────────────────

def _provider_instance(name: ProviderName) -> PaymentProvider:
    return WorldlineProvider()


def _pick_provider(preferred: ProviderName | None = None) -> PaymentProvider:
    enabled = get_enabled_provider_order()
    if preferred is not None:
        if preferred not in enabled:
            raise PaymentConfigurationError(
                f"Requested provider '{preferred}' is not enabled (enabled={enabled})"
            )
        return _provider_instance(preferred)
    return _provider_instance(enabled[0])


# ── Top-level checkout helpers ─────────────────────────────────────────────────

def create_subscription_checkout(
    *,
    org_id: int,
    user_id: int | None = None,
    payment_alias_id: str | None = None,
    save_payment_method: bool = False,
    tier: str,
    billing_cycle: Literal["monthly", "yearly"],
    success_url: str,
    cancel_url: str,
    billing_address: dict[str, str] | None = None,
    amount_chf: float | None = None,
    preferred_provider: ProviderName | None = None,
) -> CheckoutSession:
    provider = _pick_provider(preferred_provider)
    return provider.create_subscription_checkout(
        org_id=org_id,
        user_id=user_id,
        payment_alias_id=payment_alias_id,
        save_payment_method=save_payment_method,
        tier=tier,
        billing_cycle=billing_cycle,
        success_url=success_url,
        cancel_url=cancel_url,
        billing_address=billing_address,
        amount_chf=amount_chf,
    )


def create_topup_checkout(
    *,
    org_id: int,
    user_id: int | None = None,
    payment_alias_id: str | None = None,
    save_payment_method: bool = False,
    credits: int,
    success_url: str,
    cancel_url: str,
    billing_address: dict[str, str] | None = None,
    amount_chf: float | None = None,
    preferred_provider: ProviderName | None = None,
) -> CheckoutSession:
    provider = _pick_provider(preferred_provider)
    return provider.create_topup_checkout(
        org_id=org_id,
        user_id=user_id,
        payment_alias_id=payment_alias_id,
        save_payment_method=save_payment_method,
        credits=credits,
        success_url=success_url,
        cancel_url=cancel_url,
        billing_address=billing_address,
        amount_chf=amount_chf,
    )
