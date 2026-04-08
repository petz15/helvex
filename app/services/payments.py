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
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode
from typing import Any, Literal, Protocol

import httpx
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.models.organization import Organization
from app.services import credits, payment_transactions
from app.services.tiers import calculate_custom_tier_price

logger = logging.getLogger(__name__)

ProviderName = Literal["worldline", "stripe"]


class PaymentConfigurationError(RuntimeError):
    """Raised when the requested payment provider is not configured."""


@dataclass(slots=True)
class CheckoutSession:
    provider: ProviderName
    checkout_url: str
    external_id: str | None = None
    order_reference: str | None = None


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
        billing_address: dict[str, str] | None = None,
        save_payment_method: bool = False,
    ) -> CheckoutSession:
        ...

    def create_topup_checkout(
        self,
        *,
        org_id: int,
        credits: int,
        success_url: str,
        cancel_url: str,
        billing_address: dict[str, str] | None = None,
        save_payment_method: bool = False,
    ) -> CheckoutSession:
        ...


def _normalize_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    return mode if mode in {"worldline", "stripe", "dual"} else "worldline"


def _base_url() -> str:
    return settings.app_base_url.rstrip("/")


def _query_url(base_path: str, params: dict[str, str]) -> str:
    return f"{_base_url()}{base_path}?{urlencode(params)}"


def _worldline_api_username() -> str:
    return settings.worldline_api_username.strip()


def _worldline_customer_id() -> str:
    return settings.worldline_customer_id.strip()


def _worldline_terminal_id() -> str:
    return getattr(settings, "worldline_terminal_id", "").strip()


def _worldline_api_base_url() -> str:
    base = (settings.worldline_api_base_url or "").strip()
    if not base:
        raise PaymentConfigurationError("Worldline provider is not configured (WORLDLINE_API_BASE_URL missing)")
    if not (base.startswith("https://") or base.startswith("http://")):
        raise PaymentConfigurationError(
            "Worldline provider is not configured "
            "(WORLDLINE_API_BASE_URL must start with http:// or https://)"
        )
    return base.rstrip("/")


def _worldline_spec_version() -> str:
    return (getattr(settings, "worldline_spec_version", "") or "1.51").strip() or "1.51"


def _worldline_raw_api_logging_enabled() -> bool:
    return bool(getattr(settings, "worldline_raw_api_logging_enabled", False))


def _worldline_callback_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt="worldline-callback-v1")


def create_worldline_callback_context(
    *,
    org_id: int | None,
    user_id: int | None,
    kind: str,
    order_reference: str,
    success_url: str,
    cancel_url: str,
) -> str:
    payload = {
        "org_id": str(org_id) if org_id is not None else "",
        "user_id": str(user_id) if user_id is not None else "",
        "kind": kind,
        "order_reference": order_reference,
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    return str(_worldline_callback_serializer().dumps(payload))


def decode_worldline_callback_context(ctx: str | None) -> dict[str, str]:
    if not ctx:
        return {}
    try:
        data = _worldline_callback_serializer().loads(ctx)
    except BadSignature:
        return {}
    if not isinstance(data, dict):
        return {}
    kind = str(data.get("kind") or "").strip().lower()
    org_id = str(data.get("org_id") or "").strip()
    user_id = str(data.get("user_id") or "").strip()
    order_reference = str(data.get("order_reference") or "").strip()
    success_url = str(data.get("success_url") or "").strip()
    cancel_url = str(data.get("cancel_url") or "").strip()
    if kind not in {"subscription", "topup", "alias", "card_alias"}:
        return {}
    if not order_reference or not success_url or not cancel_url:
        return {}
    return {
        "org_id": org_id,
        "user_id": user_id,
        "kind": kind,
        "order_reference": order_reference,
        "success_url": success_url,
        "cancel_url": cancel_url,
    }


def _worldline_callback_url(*, org_id: int, user_id: int | None, kind: str, order_reference: str, success_url: str, cancel_url: str, source: str) -> str:
    # Alias/card-registration callbacks must go to the card-specific endpoint.
    # Payment (subscription/topup) callbacks go to the generic return endpoint.
    if kind in {"alias", "card_alias"}:
        base_path = "/api/v1/billing/webhooks/worldline/card/return"
    else:
        base_path = "/api/v1/billing/webhooks/worldline/return"
    ctx = create_worldline_callback_context(
        org_id=org_id,
        user_id=user_id,
        kind=kind,
        order_reference=order_reference,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    fixed = urlencode(
        {
            "ctx": ctx,
            "source": source,
        }
    )
    return f"{_base_url()}{base_path}?{fixed}"


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


def parse_worldline_merchant_reference(reference: str | None) -> dict[str, Any]:
    """Parse order reference token created for Worldline transactions.

    Supported formats:
    - wl_sub_<org_id>_<user_id>_<tier>_<billing_cycle>_<nonce>
    - wl_topup_<org_id>_<user_id>_<credits>_<nonce>

    Backward-compatible legacy formats are also accepted:
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
            # wl_sub_<org_id>_<tier>_<billing_cycle>_<nonce>
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
            # wl_topup_<org_id>_<credits>_<nonce>
            try:
                out["topup_credits"] = int(parts[3])
            except ValueError:
                pass
        return out

    return {}


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
        customer_id = _worldline_customer_id()
        terminal_id = _worldline_terminal_id()
        api_username = _worldline_api_username()
        if not customer_id or not terminal_id or not api_username or not settings.worldline_api_password.strip():
            raise PaymentConfigurationError(
                "Worldline provider is not configured "
                "(WORLDLINE_CUSTOMER_ID/WORLDLINE_TERMINAL_ID/WORLDLINE_API_USERNAME/WORLDLINE_API_PASSWORD missing)"
            )

    def _post_worldline_json(
        self,
        *,
        endpoint_path: str,
        payload: dict[str, Any],
        operation: str,
        timeout: float = 100.0,
    ) -> dict[str, Any]:
        base = _worldline_api_base_url()
        url = f"{base}{endpoint_path}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        auth = (_worldline_api_username(), settings.worldline_api_password)

        if _worldline_raw_api_logging_enabled():
            logger.info(
                "worldline.raw_request operation=%s url=%s payload=%s",
                operation,
                url,
                json.dumps(payload),
            )

        with httpx.Client(timeout=timeout) as client:
            try:
                resp = client.post(url, headers=headers, json=payload, auth=auth)
            except httpx.HTTPError as exc:
                logger.exception("worldline.http_error operation=%s url=%s", operation, url)
                raise RuntimeError(f"{operation} request failed: {exc}") from exc

        if _worldline_raw_api_logging_enabled():
            logger.info(
                "worldline.raw_response operation=%s url=%s status=%s body=%s",
                operation,
                url,
                resp.status_code,
                resp.text,
            )

        if resp.status_code >= 400:
            raise RuntimeError(f"{operation} failed: {resp.status_code} {resp.text}")

        try:
            return resp.json()
        except ValueError as exc:
            raise RuntimeError(f"{operation} returned non-JSON response") from exc

    def _create_transaction_initialize(self, payload: dict[str, Any]) -> CheckoutSession:
        base = _worldline_api_base_url()
        url = f"{base}/Payment/v1/Transaction/Initialize"
        api_username = _worldline_api_username()

        logger.info(
            "worldline.initialize_request url=%s username=%s amount_chf=%s order_ref=%s",
            url, api_username, payload.get("Payment", {}).get("Amount", {}).get("Value"), 
            payload.get("Payment", {}).get("OrderId")
        )
        logger.debug("worldline.initialize_payload payload_keys=%s", list(payload.keys()))

        data = self._post_worldline_json(
            endpoint_path="/Payment/v1/Transaction/Initialize",
            payload=payload,
            operation="Worldline checkout creation",
            timeout=100.0,
        )

        logger.info(
            "worldline.initialize_response has_token=%s has_redirect=%s",
            "Token" in data,
            bool(data.get("RedirectUrl") or ((data.get("Redirect") or {}).get("RedirectUrl") if isinstance(data.get("Redirect"), dict) else None)),
        )

        token = str(data.get("Token") or data.get("token") or "")
        redirect = data.get("RedirectUrl")
        if not redirect:
            redirect = ((data.get("Redirect") or {}).get("RedirectUrl") if isinstance(data.get("Redirect"), dict) else None)
        if not redirect:
            logger.error("worldline.initialize_no_redirect response=%s", json.dumps(data)[:200])
            raise RuntimeError("Worldline response did not include a redirect URL")
        if not token:
            logger.error("worldline.initialize_no_token response=%s", json.dumps(data)[:200])
            raise RuntimeError("Worldline response did not include a transaction token")
        
        logger.info(
            "worldline.initialize_success token=%s redirect_prefix=%s",
            token[:20], redirect[:50] if redirect else "N/A"
        )
        order_reference = str(payload.get("Payment", {}).get("OrderId") or "")
        return CheckoutSession(
            provider=self.name,
            checkout_url=str(redirect),
            external_id=token,
            order_reference=order_reference or None,
        )

    def _initialize_request(
        self,
        *,
        org_id: int,
        payment_alias_id: str | None,
        save_payment_method: bool,
        amount_chf: float,
        order_reference: str,
        description: str,
        return_url: str,
        notify_url: str,
        success_url: str,
        cancel_url: str,
        billing_address: dict[str, str] | None = None,
        is_initial_recurring: bool = False,
    ) -> CheckoutSession:
        customer_id = _worldline_customer_id()
        terminal_id = _worldline_terminal_id()
        payment_block: dict[str, Any] = {
            "Amount": {
                "Value": str(int(round(amount_chf * 100))),
                "CurrencyCode": "CHF",
            },
            "OrderId": order_reference,
            "Description": description,
        }
        if is_initial_recurring:
            payment_block["Recurring"] = {"Initial": True}
        payload: dict[str, Any] = {
            "RequestHeader": {
                "SpecVersion": _worldline_spec_version(),
                "CustomerId": customer_id,
                "RequestId": f"wl_{org_id}_{secrets.token_hex(8)}",
                "RetryIndicator": 0,
            },
            "TerminalId": terminal_id,
            "Payment": payment_block,
            "Payer": {
                "Language": "en",
            },
            "ReturnUrl": {
                "Url": return_url,
            },
            "RedirectNotifyUrls": {
                "Success": notify_url,
                "Fail": notify_url,
            },
        }
        # Embed Saferpay form in an iframe — merchant can surround it with own CSS.
        css_url = (getattr(settings, "worldline_css_url", "") or "").strip()
        if css_url:
            payload["Styling"] = {"CssUrl": css_url}
        if payment_alias_id:
            payload["PaymentMeans"] = {"Card": {"Alias": {"Id": payment_alias_id}}}
        elif save_payment_method:
            payload["RegisterAlias"] = {"IdGenerator": "RANDOM", "Type": "CARD"}
        # Add billing address if provided (required by Worldline for chargeback evidence)
        if billing_address:
            payload["Payer"]["FirstName"] = billing_address.get("first_name", "")
            payload["Payer"]["LastName"] = billing_address.get("last_name", "")
            payload["Payer"]["Address"] = {
                "Street": billing_address.get("street", ""),
                "HouseNumber": billing_address.get("number", ""),
                "PostCode": billing_address.get("postal_code", ""),
                "City": billing_address.get("city", ""),
                "CountryCode": billing_address.get("country", ""),
            }
            if billing_address.get("company_name"):
                payload["Payer"]["CompanyName"] = billing_address["company_name"]
        session = self._create_transaction_initialize(payload)
        return session

    def authorize_transaction(self, *, token: str) -> dict[str, Any]:
        customer_id = _worldline_customer_id()
        payload = {
            "RequestHeader": {
                "SpecVersion": _worldline_spec_version(),
                "CustomerId": customer_id,
                "RequestId": f"wl_auth_{secrets.token_hex(8)}",
                "RetryIndicator": 0,
            },
            "Token": token,
        }
        return self._post_worldline_json(
            endpoint_path="/Payment/v1/Transaction/Authorize",
            payload=payload,
            operation="Worldline authorization",
            timeout=100.0,
        )

    def cancel_transaction(self, *, transaction_id: str) -> dict[str, Any]:
        """Cancel a previously authorized Worldline/Saferpay transaction."""
        tx_id = str(transaction_id or "").strip()
        if not tx_id:
            raise RuntimeError("Worldline transaction cancellation failed: missing transaction_id")

        payload = {
            "RequestHeader": {
                "SpecVersion": _worldline_spec_version(),
                "CustomerId": _worldline_customer_id(),
                "RequestId": f"wl_cancel_{secrets.token_hex(8)}",
                "RetryIndicator": 0,
            },
            "TransactionReference": {
                "TransactionId": tx_id,
            },
        }
        return self._post_worldline_json(
            endpoint_path="/Payment/v1/Transaction/Cancel",
            payload=payload,
            operation="Worldline transaction cancellation",
            timeout=100.0,
        )

    def capture_transaction(self, *, transaction_id: str) -> dict[str, Any]:
        """Capture a previously authorized Saferpay transaction."""
        tx_id = str(transaction_id or "").strip()
        if not tx_id:
            raise RuntimeError("Saferpay capture failed: missing transaction_id")
        payload = {
            "RequestHeader": {
                "SpecVersion": _worldline_spec_version(),
                "CustomerId": _worldline_customer_id(),
                "RequestId": f"wl_cap_{secrets.token_hex(8)}",
                "RetryIndicator": 0,
            },
            "TransactionReference": {
                "TransactionId": tx_id,
            },
        }
        return self._post_worldline_json(
            endpoint_path="/Payment/v1/Transaction/Capture",
            payload=payload,
            operation="Saferpay capture",
            timeout=100.0,
        )

    def authorize_referenced_transaction(
        self,
        *,
        org_id: int,
        transaction_id: str,
        amount_chf: float,
        order_reference: str,
        description: str,
    ) -> dict[str, Any]:
        """Charge a recurring subscription using a previous transaction as reference.

        Uses Transaction/AuthorizeReferenced — no customer interaction required.
        The referenced transaction must have been initialized with Payment.Recurring.Initial.
        Automatically captures the transaction on success.
        """
        tx_id = str(transaction_id or "").strip()
        if not tx_id:
            raise RuntimeError("AuthorizeReferenced failed: missing transaction_id reference")
        payload = {
            "RequestHeader": {
                "SpecVersion": _worldline_spec_version(),
                "CustomerId": _worldline_customer_id(),
                "RequestId": f"wl_recur_{org_id}_{secrets.token_hex(8)}",
                "RetryIndicator": 0,
            },
            "TerminalId": _worldline_terminal_id(),
            "Payment": {
                "Amount": {
                    "Value": str(int(round(amount_chf * 100))),
                    "CurrencyCode": "CHF",
                },
                "OrderId": order_reference,
                "Description": description,
            },
            "TransactionReference": {
                "TransactionId": tx_id,
            },
        }
        logger.info(
            "saferpay.authorize_referenced org_id=%s ref_tx=%s amount_chf=%s order_ref=%s",
            org_id, tx_id[:20], amount_chf, order_reference,
        )
        data = self._post_worldline_json(
            endpoint_path="/Payment/v1/Transaction/AuthorizeReferenced",
            payload=payload,
            operation="AuthorizeReferenced",
            timeout=100.0,
        )

        new_tx_id = str((data.get("Transaction") or {}).get("Id") or "")
        tx_status = str((data.get("Transaction") or {}).get("Status") or "").upper()
        logger.info(
            "saferpay.authorize_referenced_result org_id=%s new_tx_id=%s status=%s",
            org_id, new_tx_id[:20] if new_tx_id else "NONE", tx_status,
        )

        if tx_status == "AUTHORIZED" and new_tx_id:
            try:
                self.capture_transaction(transaction_id=new_tx_id)
                logger.info("saferpay.capture_ok org_id=%s new_tx_id=%s", org_id, new_tx_id[:20])
            except RuntimeError as exc:
                logger.warning("saferpay.capture_failed org_id=%s new_tx_id=%s error=%s", org_id, new_tx_id[:20], exc)

        return data

    def create_card_alias_registration(
        self,
        *,
        org_id: int,
        user_id: int | None = None,
        success_url: str,
        cancel_url: str,
        billing_address: dict[str, str] | None = None,
    ) -> CheckoutSession:
        self._assert_configured()
        order_reference = f"wl_alias_{org_id}_{user_id or 0}_{secrets.token_hex(6)}"
        return_url = _worldline_callback_url(
            org_id=org_id,
            user_id=user_id,
            kind="alias",
            order_reference=order_reference,
            success_url=success_url,
            cancel_url=cancel_url,
            source="return",
        )
        notify_url = _worldline_callback_url(
            org_id=org_id,
            user_id=user_id,
            kind="alias",
            order_reference=order_reference,
            success_url=success_url,
            cancel_url=cancel_url,
            source="notify",
        )
        payload: dict[str, Any] = {
            "RequestHeader": {
                "SpecVersion": _worldline_spec_version(),
                "CustomerId": _worldline_customer_id(),
                "RequestId": f"wl_alias_{org_id}_{secrets.token_hex(8)}",
                "RetryIndicator": 0,
            },
            "RegisterAlias": {
                "IdGenerator": "RANDOM",
            },
            "Type": "CARD",
            "ReturnUrl": {
                "Url": return_url,
            },
            "Notification": {
                "Url": notify_url,
            },
            "LanguageCode": "en",
        }
        # Do not prefill PaymentMeans.Card fields for hosted alias registration.
        # Sending a partial Card object makes Worldline validate Number/Exp* as required.
        data = self._post_worldline_json(
            endpoint_path="/Payment/v1/Alias/Insert",
            payload=payload,
            operation="Worldline alias registration",
            timeout=100.0,
        )

        token = str(data.get("Token") or data.get("token") or "")
        redirect = data.get("RedirectUrl")
        if not redirect:
            redirect = ((data.get("Redirect") or {}).get("RedirectUrl") if isinstance(data.get("Redirect"), dict) else None)
        if not token:
            raise RuntimeError("Worldline alias registration response did not include a token")
        if not redirect:
            raise RuntimeError("Worldline alias registration response did not include a redirect URL")

        return CheckoutSession(
            provider=self.name,
            checkout_url=str(redirect),
            external_id=token,
            order_reference=order_reference,
        )

    def assert_alias_insert(self, *, token: str) -> dict[str, Any]:
        payload = {
            "RequestHeader": {
                "SpecVersion": _worldline_spec_version(),
                "CustomerId": _worldline_customer_id(),
                "RequestId": f"wl_alias_assert_{secrets.token_hex(8)}",
                "RetryIndicator": 0,
            },
            "Token": token,
        }
        return self._post_worldline_json(
            endpoint_path="/Payment/v1/Alias/AssertInsert",
            payload=payload,
            operation="Worldline alias assertion",
            timeout=100.0,
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
        self._assert_configured()
        price = amount_chf if amount_chf is not None else compute_subscription_price_chf(tier=tier, billing_cycle=billing_cycle)
        if user_id is not None:
            order_reference = f"wl_sub_{org_id}_{user_id}_{tier}_{billing_cycle}_{secrets.token_hex(6)}"
        else:
            order_reference = f"wl_sub_{org_id}_{tier}_{billing_cycle}_{secrets.token_hex(6)}"
        return_url = _worldline_callback_url(
            org_id=org_id,
            user_id=user_id,
            kind="subscription",
            order_reference=order_reference,
            success_url=success_url,
            cancel_url=cancel_url,
            source="return",
        )
        notify_url = _worldline_callback_url(
            org_id=org_id,
            user_id=user_id,
            kind="subscription",
            order_reference=order_reference,
            success_url=success_url,
            cancel_url=cancel_url,
            source="notify",
        )
        return self._initialize_request(
            org_id=org_id,
            payment_alias_id=payment_alias_id,
            save_payment_method=save_payment_method,
            amount_chf=price,
            order_reference=order_reference,
            description=f"Helvex {tier.title()} ({billing_cycle})",
            return_url=return_url,
            notify_url=notify_url,
            success_url=success_url,
            cancel_url=cancel_url,
            billing_address=billing_address,
            is_initial_recurring=True,
        )

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
        self._assert_configured()
        amount_chf = amount_chf if amount_chf is not None else credits_to_chf(credits)
        if user_id is not None:
            order_reference = f"wl_topup_{org_id}_{user_id}_{credits}_{secrets.token_hex(6)}"
        else:
            order_reference = f"wl_topup_{org_id}_{credits}_{secrets.token_hex(6)}"
        return_url = _worldline_callback_url(
            org_id=org_id,
            user_id=user_id,
            kind="topup",
            order_reference=order_reference,
            success_url=success_url,
            cancel_url=cancel_url,
            source="return",
        )
        notify_url = _worldline_callback_url(
            org_id=org_id,
            user_id=user_id,
            kind="topup",
            order_reference=order_reference,
            success_url=success_url,
            cancel_url=cancel_url,
            source="notify",
        )
        return self._initialize_request(
            org_id=org_id,
            payment_alias_id=payment_alias_id,
            save_payment_method=save_payment_method,
            amount_chf=amount_chf,
            order_reference=order_reference,
            description=f"Helvex top-up {credits} credits",
            return_url=return_url,
            notify_url=notify_url,
            success_url=success_url,
            cancel_url=cancel_url,
            billing_address=billing_address,
        )


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
