"""Payment provider abstraction for subscription and top-up billing.

Phase 6 scaffold:
- Supports provider selection modes: worldline | stripe | dual
- Keeps billing orchestration provider-agnostic so both providers can coexist
  without rewriting business logic.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.organization import Organization
from app.services import credits
from app.services.tiers import calculate_custom_tier_price

ProviderName = Literal["worldline", "stripe"]


class PaymentConfigurationError(RuntimeError):
    """Raised when the requested payment provider is not configured."""


@dataclass(slots=True)
class CheckoutSession:
    provider: ProviderName
    checkout_url: str
    external_id: str | None = None


_BASE_MONTHLY_CHF: dict[str, float] = {
    "free": 0.0,
    "simple": 6.0,
    "explorer": 12.0,
    "researcher": 17.0,
    "strategist": 37.0,
}


class PaymentProvider(Protocol):
    name: ProviderName

    def create_subscription_checkout(
        self,
        *,
        org_id: int,
        tier: str,
        billing_cycle: Literal["monthly", "yearly"],
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        ...

    def create_topup_checkout(
        self,
        *,
        org_id: int,
        credits: int,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        ...


def _normalize_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    return mode if mode in {"worldline", "stripe", "dual"} else "worldline"


def _base_url() -> str:
    return settings.app_base_url.rstrip("/")


def compute_subscription_price_chf(
    *,
    tier: str,
    billing_cycle: Literal["monthly", "yearly"],
    custom_features: dict | None = None,
    verified_business: bool = False,
) -> float:
    """Compute CHF price for a tier and billing cycle.

    Yearly billing follows the plan rule: monthly * 10.
    Verified business gets an additional 20% discount.
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


def get_enabled_provider_order() -> list[ProviderName]:
    """Return provider priority order based on PAYMENT_PROVIDER_MODE.

    dual mode keeps worldline first for backward-compatibility in this codebase.
    """
    mode = _normalize_mode(settings.payment_provider_mode)
    if mode == "stripe":
        return ["stripe"]
    if mode == "dual":
        return ["worldline", "stripe"]
    return ["worldline"]


class WorldlineProvider:
    name: ProviderName = "worldline"

    def _assert_configured(self) -> None:
        if (
            not settings.worldline_api_key.strip()
            or not settings.worldline_api_password.strip()
            or not settings.worldline_merchant_id.strip()
        ):
            raise PaymentConfigurationError(
                "Worldline provider is not configured "
                "(WORLDLINE_API_KEY/WORLDLINE_API_PASSWORD/WORLDLINE_MERCHANT_ID missing)"
            )

    def _create_hosted_checkout(self, payload: dict[str, Any]) -> CheckoutSession:
        base = settings.worldline_api_base_url.rstrip("/")
        url = f"{base}/v1/{settings.worldline_merchant_id}/hostedcheckouts"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                headers=headers,
                json=payload,
                auth=(settings.worldline_api_key, settings.worldline_api_password),
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Worldline checkout creation failed: {resp.status_code} {resp.text}")
            data = resp.json()

        hosted_id = data.get("hostedCheckoutId") or data.get("id")
        redirect = data.get("redirectUrl") or data.get("partialRedirectUrl")
        if not redirect and hosted_id:
            redirect = f"{base}/hostedcheckout/PaymentMethodsSelection/{hosted_id}"
        if not redirect:
            raise RuntimeError("Worldline response did not include a redirect URL")
        return CheckoutSession(provider=self.name, checkout_url=str(redirect), external_id=str(hosted_id or ""))

    def create_subscription_checkout(
        self,
        *,
        org_id: int,
        tier: str,
        billing_cycle: Literal["monthly", "yearly"],
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        self._assert_configured()
        price = compute_subscription_price_chf(tier=tier, billing_cycle=billing_cycle)
        amount_cents = int(round(price * 100))
        ext_id = f"wl_sub_{org_id}_{secrets.token_hex(6)}"
        payload = {
            "order": {
                "amountOfMoney": {"currencyCode": "CHF", "amount": amount_cents},
                "customer": {"merchantCustomerId": str(org_id)},
                "references": {"merchantReference": ext_id},
            },
            "hostedCheckoutSpecificInput": {
                "returnUrl": success_url,
                "locale": "en_GB",
                "showResultPage": False,
            },
            "metadata": {
                "org_id": str(org_id),
                "tier": tier,
                "billing_cycle": billing_cycle,
                "kind": "subscription",
            },
        }
        return self._create_hosted_checkout(payload)

    def create_topup_checkout(
        self,
        *,
        org_id: int,
        credits: int,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        self._assert_configured()
        amount_cents = int(round(credits_to_chf(credits) * 100))
        ext_id = f"wl_topup_{org_id}_{secrets.token_hex(6)}"
        payload = {
            "order": {
                "amountOfMoney": {"currencyCode": "CHF", "amount": amount_cents},
                "customer": {"merchantCustomerId": str(org_id)},
                "references": {"merchantReference": ext_id},
            },
            "hostedCheckoutSpecificInput": {
                "returnUrl": success_url,
                "locale": "en_GB",
                "showResultPage": False,
            },
            "metadata": {
                "org_id": str(org_id),
                "topup_credits": str(credits),
                "kind": "topup",
            },
        }
        return self._create_hosted_checkout(payload)


class StripeProvider:
    name: ProviderName = "stripe"

    def _assert_configured(self) -> None:
        if not settings.stripe_secret_key.strip():
            raise PaymentConfigurationError("Stripe provider is not configured (STRIPE_SECRET_KEY missing)")

    def _post_checkout_session(self, form_data: dict[str, str]) -> CheckoutSession:
        base = settings.stripe_api_base_url.rstrip("/")
        url = f"{base}/v1/checkout/sessions"
        headers = {
            "Authorization": f"Bearer {settings.stripe_secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, data=form_data, headers=headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"Stripe checkout creation failed: {resp.status_code} {resp.text}")
            data = resp.json()

        checkout_url = data.get("url")
        if not checkout_url:
            raise RuntimeError("Stripe response did not include checkout URL")
        return CheckoutSession(provider=self.name, checkout_url=str(checkout_url), external_id=str(data.get("id") or ""))

    def create_subscription_checkout(
        self,
        *,
        org_id: int,
        tier: str,
        billing_cycle: Literal["monthly", "yearly"],
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        self._assert_configured()
        price = compute_subscription_price_chf(tier=tier, billing_cycle=billing_cycle)
        amount_cents = int(round(price * 100))
        interval = "year" if billing_cycle == "yearly" else "month"
        ext_id = f"st_sub_{org_id}_{secrets.token_hex(6)}"
        data = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "chf",
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][recurring][interval]": interval,
            "line_items[0][price_data][product_data][name]": f"Helvex {tier.title()} ({billing_cycle})",
            "metadata[org_id]": str(org_id),
            "metadata[tier]": tier,
            "metadata[billing_cycle]": billing_cycle,
            "metadata[kind]": "subscription",
            "client_reference_id": ext_id,
        }
        return self._post_checkout_session(data)

    def create_topup_checkout(
        self,
        *,
        org_id: int,
        credits: int,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        self._assert_configured()
        amount_cents = int(round(credits_to_chf(credits) * 100))
        ext_id = f"st_topup_{org_id}_{secrets.token_hex(6)}"
        data = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "chf",
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][product_data][name]": f"Helvex top-up {credits} credits",
            "metadata[org_id]": str(org_id),
            "metadata[topup_credits]": str(credits),
            "metadata[kind]": "topup",
            "client_reference_id": ext_id,
        }
        return self._post_checkout_session(data)


def _provider_instance(name: ProviderName) -> PaymentProvider:
    if name == "stripe":
        return StripeProvider()
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


def create_subscription_checkout(
    *,
    org_id: int,
    tier: str,
    billing_cycle: Literal["monthly", "yearly"],
    success_url: str,
    cancel_url: str,
    preferred_provider: ProviderName | None = None,
) -> CheckoutSession:
    provider = _pick_provider(preferred_provider)
    return provider.create_subscription_checkout(
        org_id=org_id,
        tier=tier,
        billing_cycle=billing_cycle,
        success_url=success_url,
        cancel_url=cancel_url,
    )


def create_topup_checkout(
    *,
    org_id: int,
    credits: int,
    success_url: str,
    cancel_url: str,
    preferred_provider: ProviderName | None = None,
) -> CheckoutSession:
    provider = _pick_provider(preferred_provider)
    return provider.create_topup_checkout(
        org_id=org_id,
        credits=credits,
        success_url=success_url,
        cancel_url=cancel_url,
    )


def verify_stripe_signature(*, payload: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify Stripe webhook signature using the standard v1 HMAC format."""
    if not signature_header or not secret:
        return False
    parts = [p.strip() for p in signature_header.split(",") if p.strip()]
    values: dict[str, str] = {}
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            values[k] = v
    ts = values.get("t")
    v1 = values.get("v1")
    if not ts or not v1:
        return False
    signed = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, v1):
        return False

    # Reject stale payloads (>5 min) to limit replay window.
    try:
        age = abs(int(time.time()) - int(ts))
    except ValueError:
        return False
    return age <= 300


def verify_worldline_signature(*, payload: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify Worldline webhook HMAC signature (sha256 hex body digest)."""
    if not signature_header or not secret:
        return False
    sig = signature_header.strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256="):]
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, sig)


def apply_subscription_update(
    db: Session,
    *,
    org_id: int,
    tier: str | None,
    billing_cycle: Literal["monthly", "yearly"] | None,
    customer_id: str | None,
    period_end_ts: int | None,
) -> Organization:
    org = db.get(Organization, org_id)
    if org is None:
        raise ValueError("Organization not found")

    if tier:
        org.tier = tier
    if billing_cycle:
        org.subscription_billing_cycle = billing_cycle
    if customer_id:
        org.payment_customer_id = customer_id
    if period_end_ts:
        org.subscription_period_end = datetime.fromtimestamp(period_end_ts, tz=timezone.utc)

    db.commit()
    db.refresh(org)
    return org


def apply_credit_topup(db: Session, *, org_id: int, credits_amount: int, reference_id: str | None = None) -> None:
    if credits_amount <= 0:
        return
    credits.grant_credits(
        db,
        org_id=org_id,
        amount=credits_amount,
        tx_type="topup",
        reference_id=reference_id,
    )


def parse_json_payload(payload: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("Payload must be a JSON object")
    return obj
