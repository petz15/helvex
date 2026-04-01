"""Billing routes for subscription checkout, top-ups, and provider webhooks."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_org
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import payments

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


@router.post("/checkout/subscription", response_model=CheckoutResponse)
def create_subscription_checkout(
    body: SubscriptionCheckoutRequest,
    user_org: tuple[User, object] = Depends(get_current_org),
) -> CheckoutResponse:
    _user, org = user_org

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
    session = payments.create_topup_checkout(
        org_id=org.id,
        credits=body.credits,
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        preferred_provider=body.provider,
    )
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
    token = str(token or params.get("token") or params.get("Token") or params.get("transaction_token") or "").strip()
    success_url = str(params.get("success_url") or "").strip()
    cancel_url = str(params.get("cancel_url") or "").strip()
    source = str(params.get("source") or "").strip().lower()
    order_reference = str(params.get("order_reference") or "").strip()
    kind = str(params.get("kind") or "").strip().lower()

    if not token:
        target = success_url if source == "return" else cancel_url
        return RedirectResponse(_safe_redirect_target(target), status_code=status.HTTP_303_SEE_OTHER)

    parsed_ref = payments.parse_worldline_merchant_reference(order_reference)
    try:
        result = payments.WorldlineProvider().authorize_transaction(token=token)
        transaction = result.get("Transaction") if isinstance(result, dict) else {}
        transaction_status = str(transaction.get("Status") or "").upper() if isinstance(transaction, dict) else ""

        if kind == "subscription" and parsed_ref.get("org_id") and parsed_ref.get("tier"):
            payments.apply_subscription_update(
                db,
                org_id=int(parsed_ref["org_id"]),
                tier=str(parsed_ref["tier"]),
                billing_cycle=(str(parsed_ref.get("billing_cycle")) if parsed_ref.get("billing_cycle") else None),
                customer_id=None,
                period_end_ts=None,
            )
        elif kind == "topup" and parsed_ref.get("org_id") and parsed_ref.get("topup_credits"):
            payments.apply_credit_topup(
                db,
                org_id=int(parsed_ref["org_id"]),
                credits_amount=int(parsed_ref["topup_credits"]),
                reference_id=(str(transaction.get("Id")) if isinstance(transaction, dict) and transaction.get("Id") else token),
            )

        if transaction_status in {"AUTHORIZED", "CAPTURED"}:
            return RedirectResponse(_safe_redirect_target(success_url), status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(_safe_redirect_target(cancel_url), status_code=status.HTTP_303_SEE_OTHER)
    except RuntimeError as exc:
        message = str(exc)
        if "TOKEN_INVALID" in message or "TRANSACTION_IN_WRONG_STATE" in message:
            return RedirectResponse(_safe_redirect_target(success_url), status_code=status.HTTP_303_SEE_OTHER)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc


@router.get("/providers")
def list_enabled_providers(_: User = Depends(get_current_user)) -> dict:
    return {"mode": payments.settings.payment_provider_mode, "enabled": payments.get_enabled_provider_order()}
