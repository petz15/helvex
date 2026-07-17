"""Stripe payment provider implementation and webhook verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Literal

import httpx

from app.config import settings
from app.services.billing.payments._types import CheckoutSession, PaymentConfigurationError


class StripeProvider:
    name: str = "stripe"

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
        ext_id = str(data.get("id") or "")
        return CheckoutSession(
            provider=self.name,
            checkout_url=str(checkout_url),
            external_id=ext_id,
            order_reference=(ext_id or None),
        )

    def create_subscription_checkout(
        self,
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
    ) -> CheckoutSession:
        from app.services.billing.payments.pricing import compute_subscription_price_chf
        self._assert_configured()
        price = amount_chf if amount_chf is not None else compute_subscription_price_chf(tier=tier, billing_cycle=billing_cycle)
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
            "metadata[user_id]": str(user_id) if user_id is not None else "",
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
        user_id: int | None = None,
        payment_alias_id: str | None = None,
        save_payment_method: bool = False,
        credits: int,
        success_url: str,
        cancel_url: str,
        billing_address: dict[str, str] | None = None,
        amount_chf: float | None = None,
    ) -> CheckoutSession:
        from app.services.billing.payments.pricing import credits_to_chf
        self._assert_configured()
        topup_amount = amount_chf if amount_chf is not None else credits_to_chf(credits)
        amount_cents = int(round(topup_amount * 100))
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
            "metadata[user_id]": str(user_id) if user_id is not None else "",
            "metadata[topup_credits]": str(credits),
            "metadata[kind]": "topup",
            "client_reference_id": ext_id,
        }
        return self._post_checkout_session(data)


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
