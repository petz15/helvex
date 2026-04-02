"""Billing routes for subscription checkout, top-ups, and provider webhooks."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_org
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import payment_transactions, payments

router = APIRouter(prefix="/billing", tags=["billing"])


class SubscriptionCheckoutRequest(BaseModel):
    tier: str
    billing_cycle: Literal["monthly", "yearly"] = "monthly"
    success_url: str
    cancel_url: str
    provider: Literal["worldline", "stripe"] | None = None


class TopupCheckoutRequest(BaseModel):
    credits: int = Field(..., gt=0)
    success_url: str
    cancel_url: str
    provider: Literal["worldline", "stripe"] | None = None


class CheckoutResponse(BaseModel):
    provider: str
    checkout_url: str
    external_id: str | None = None
    amount_chf: float


class WebhookResponse(BaseModel):
    ok: bool
    ignored: bool = False


def _safe_redirect_target(url: str | None) -> str:
    if not url:
        return payments.settings.app_base_url.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return payments.settings.app_base_url.rstrip("/")
    return url


def _append_query_params(url: str, params: dict[str, str]) -> str:
    """Append query params to a URL that may or may not already have a query string."""
    if not params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


@router.post("/checkout/subscription", response_model=CheckoutResponse)
def create_subscription_checkout(
    body: SubscriptionCheckoutRequest,
    user_org: tuple[User, object] = Depends(get_current_org),
) -> CheckoutResponse:
    _user, org = user_org
    try:
        amount_chf = payments.compute_subscription_price_chf(
            tier=body.tier,
            billing_cycle=body.billing_cycle,
            custom_features=(getattr(org, "custom_features", None) if body.tier == "custom" else None),
            verified_business=bool(getattr(org, "verified_business", False)),
        )
        session = payments.create_subscription_checkout(
            org_id=org.id,
            tier=body.tier,
            billing_cycle=body.billing_cycle,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            preferred_provider=body.provider,
        )
    except payments.PaymentConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return CheckoutResponse(
        provider=session.provider,
        checkout_url=session.checkout_url,
        external_id=session.external_id,
        amount_chf=amount_chf,
    )


@router.post("/checkout/topup", response_model=CheckoutResponse)
def create_topup_checkout(
    body: TopupCheckoutRequest,
    user_org: tuple[User, object] = Depends(get_current_org),
) -> CheckoutResponse:
    _user, org = user_org
    try:
        session = payments.create_topup_checkout(
            org_id=org.id,
            credits=body.credits,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            preferred_provider=body.provider,
        )
    except payments.PaymentConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return CheckoutResponse(
        provider=session.provider,
        checkout_url=session.checkout_url,
        external_id=session.external_id,
        amount_chf=payments.credits_to_chf(body.credits),
    )


@router.post("/webhooks/stripe", response_model=WebhookResponse)
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> WebhookResponse:
    payload = await request.body()
    if not payments.verify_stripe_signature(
        payload=payload,
        signature_header=stripe_signature,
        secret=payments.settings.stripe_webhook_secret,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Stripe signature")

    event = payments.parse_json_payload(payload)
    event_type = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}
    metadata = obj.get("metadata") if isinstance(obj, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}

    if event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        org_id = int(metadata.get("org_id") or 0)
        if org_id <= 0:
            return WebhookResponse(ok=True, ignored=True)
        payments.apply_subscription_update(
            db,
            org_id=org_id,
            tier=metadata.get("tier"),
            billing_cycle=(metadata.get("billing_cycle") or None),
            customer_id=(obj.get("customer") if isinstance(obj, dict) else None),
            period_end_ts=(int(obj.get("current_period_end")) if isinstance(obj, dict) and obj.get("current_period_end") else None),
        )
        return WebhookResponse(ok=True)

    if event_type == "checkout.session.completed":
        org_id = int(metadata.get("org_id") or 0)
        topup_credits = int(metadata.get("topup_credits") or 0)
        if org_id > 0 and topup_credits > 0:
            payments.apply_credit_topup(
                db,
                org_id=org_id,
                credits_amount=topup_credits,
                reference_id=(obj.get("id") if isinstance(obj, dict) else None),
            )
            return WebhookResponse(ok=True)

    return WebhookResponse(ok=True, ignored=True)


@router.get("/webhooks/worldline/return")
@router.get("/webhooks/worldline/return/{token}")
async def worldline_return(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
) -> RedirectResponse:
    params = request.query_params
    # Saferpay returns token as TOKEN query parameter, but accept variations
    token = str(token or params.get("TOKEN") or params.get("token") or params.get("Token") or "").strip()
    success_url = str(params.get("success_url") or "").strip()
    cancel_url = str(params.get("cancel_url") or "").strip()
    source = str(params.get("source") or "").strip().lower()
    order_reference = str(params.get("order_reference") or "").strip()
    kind = str(params.get("kind") or "").strip().lower()

    # If no token, redirect based on source (return=user clicked back, notify=webhook)
    if not token:
        target = success_url if source == "return" else cancel_url
        return RedirectResponse(_safe_redirect_target(target), status_code=status.HTTP_303_SEE_OTHER)

    parsed_ref = payments.parse_worldline_merchant_reference(order_reference)

    # BUG-GUARD: If we cannot parse org_id, the reference is invalid — decline immediately.
    # This prevents a KeyError crash and rejects forged/malformed references.
    if not parsed_ref.get("org_id"):
        return RedirectResponse(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "invalid_reference"})),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # SECURITY: Check for existing payment to prevent double-processing.
    # Must happen before calling Saferpay to avoid paying twice on retries.
    existing_payment = payment_transactions.get_payment_transaction_by_external_id(db, token)
    if existing_payment:
        # Redirect based on the actual recorded outcome — never assume success.
        if existing_payment.status in {"authorized", "captured"}:
            return RedirectResponse(_safe_redirect_target(success_url), status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"})),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        # Contact Saferpay to verify the token and get authoritative status.
        # This is the key security check — tokens not in Saferpay are rejected here.
        result = payments.WorldlineProvider().authorize_transaction(token=token)
        transaction = result.get("Transaction") if isinstance(result, dict) else {}
        transaction_status = str(transaction.get("Status") or "").upper() if isinstance(transaction, dict) else ""
        transaction_id = str(transaction.get("Id") or "") if isinstance(transaction, dict) else ""

        # Normalize status from provider-specific values to our internal enum.
        normalized_status = payment_transactions.validate_transaction_status(transaction_status)

        # Extract payment method (card, twint, bank_transfer, etc.)
        payment_method = payment_transactions._extract_payment_method_worldline(transaction)

        # Determine CHF amount from the order reference, not user-supplied params.
        # Fall back to a non-zero placeholder so logging never rejects the transaction.
        amount_chf: float = 0.01
        if kind == "subscription" and parsed_ref.get("tier"):
            amount_chf = payments.compute_subscription_price_chf(
                tier=parsed_ref["tier"],
                billing_cycle=str(parsed_ref.get("billing_cycle") or "monthly"),
            )
        elif kind == "topup" and parsed_ref.get("topup_credits"):
            amount_chf = payments.credits_to_chf(int(parsed_ref["topup_credits"]))

        # Log the transaction. The UNIQUE constraint on external_id is the DB-level
        # idempotency safeguard against races; the SELECT above handles the common case.
        payment_tx = payment_transactions.log_payment_transaction(
            db,
            org_id=int(parsed_ref["org_id"]),
            provider="worldline",
            external_id=token,
            order_reference=order_reference,
            amount_chf=amount_chf,
            kind=kind,
            status=normalized_status,
            payment_method=payment_method,
            provider_transaction_id=transaction_id or None,
            subscription_tier=(str(parsed_ref["tier"]) if parsed_ref.get("tier") else None),
            subscription_billing_cycle=(str(parsed_ref["billing_cycle"]) if parsed_ref.get("billing_cycle") else None),
            credits_purchased=(int(parsed_ref["topup_credits"]) if parsed_ref.get("topup_credits") else None),
        )

        # Only apply business logic if payment succeeded.
        if normalized_status in {"authorized", "captured"}:
            payment_transactions.apply_successful_payment(db, payment_tx)
            return RedirectResponse(_safe_redirect_target(success_url), status_code=status.HTTP_303_SEE_OTHER)

        # Declined or unknown status — no credits granted.
        return RedirectResponse(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"})),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    except payment_transactions.DuplicatePaymentError:
        # Race condition: two identical requests arrived simultaneously.
        # Re-read the actual stored outcome to decide where to send the user.
        existing = payment_transactions.get_payment_transaction_by_external_id(db, token)
        if existing and existing.status in {"authorized", "captured"}:
            return RedirectResponse(_safe_redirect_target(success_url), status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"})),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    except RuntimeError as exc:
        message = str(exc)
        # Saferpay-specific errors indicating the token was already consumed.
        if "TOKEN_INVALID" in message or "TRANSACTION_IN_WRONG_STATE" in message:
            return RedirectResponse(
                _safe_redirect_target(_append_query_params(success_url, {"already_processed": "true"})),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc

    except payment_transactions.PaymentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/providers")
def list_enabled_providers(_: User = Depends(get_current_user)) -> dict:
    return {"mode": payments.settings.payment_provider_mode, "enabled": payments.get_enabled_provider_order()}
