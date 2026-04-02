"""Billing routes for subscription checkout, top-ups, and provider webhooks."""

from __future__ import annotations

import json
import logging
from typing import Literal
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _emit(level: str, message: str, *args: object) -> None:
    """Emit to logger and stdout so logs remain visible in container output."""
    log_fn = getattr(logger, level, logger.info)
    log_fn(message, *args)
    try:
        rendered = message % args if args else message
    except Exception:
        rendered = f"{message} | args={args!r}"
    print(rendered, flush=True)

from app.api.deps import get_current_org
from app.api.deps import require_org_role
from app.auth import get_current_user
from app.database import get_db
from app.models.org_credit_transaction import OrgCreditTransaction
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.schemas.billing import BillingAddress, BillingTierRead
from app.services.billing_addresses import get_default_billing_address
from app.services import payment_transactions, payments
from app.services.tiers import (
    get_billing_tier_by_slug,
    get_billing_tiers,
    get_tier_price_chf,
    get_tier_yearly_price_chf,
)

router = APIRouter(prefix="/billing", tags=["billing"])


class SubscriptionCheckoutRequest(BaseModel):
    tier: str
    billing_cycle: Literal["monthly", "yearly"] = "monthly"
    success_url: str
    cancel_url: str
    billing_address: BillingAddress | None = None
    save_payment_method: bool = False
    provider: Literal["worldline", "stripe"] | None = None


class TopupCheckoutRequest(BaseModel):
    credits: int = Field(..., gt=0)
    success_url: str
    cancel_url: str
    billing_address: BillingAddress | None = None
    save_payment_method: bool = False
    provider: Literal["worldline", "stripe"] | None = None


class CardRegistrationRequest(BaseModel):
    success_url: str
    cancel_url: str
    billing_address: BillingAddress | None = None


class DefaultPaymentMethodRequest(BaseModel):
    user_id: int


class CheckoutResponse(BaseModel):
    provider: str
    checkout_url: str
    external_id: str | None = None
    amount_chf: float


class PaymentMethodRegistrationResponse(BaseModel):
    provider: str
    checkout_url: str
    external_id: str | None = None


class WebhookResponse(BaseModel):
    ok: bool
    ignored: bool = False


def _resolve_worldline_payment_alias(db: Session, org: Organization) -> str | None:
    owner_id = getattr(org, "default_payment_user_id", None)
    if not owner_id:
        return None
    owner = db.get(User, int(owner_id))
    if owner is None:
        return None
    alias_id = str(owner.payment_customer_id or "").strip()
    return alias_id or None


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


def _resolve_billing_address(current_user: User, body_address: BillingAddress | None, db: Session) -> dict:
    if body_address is not None:
        return body_address.model_dump()

    managed_user = db.merge(current_user)
    default_address = get_default_billing_address(managed_user.billing_address_json)
    if default_address:
        return default_address
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Billing address required before checkout")


def _resolve_tier_amount_chf(
    db: Session,
    *,
    tier: str,
    billing_cycle: str,
    custom_features: dict | None,
    verified_business: bool,
) -> float:
    if tier == "custom":
        amount = payments.compute_subscription_price_chf(
            tier=tier,
            billing_cycle=billing_cycle,  # type: ignore[arg-type]
            custom_features=custom_features,
            verified_business=verified_business,
        )
        return amount
    base = get_tier_price_chf(db, tier)
    amount = base * (10.0 if billing_cycle == "yearly" else 1.0)
    if verified_business:
        amount *= 0.80
    return round(amount, 2)


@router.post("/checkout/subscription", response_model=CheckoutResponse)
def create_subscription_checkout(
    body: SubscriptionCheckoutRequest,
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> CheckoutResponse:
    _user, org = user_org
    logger.info(
        "billing.subscription_checkout_start user_id=%s org_id=%s tier=%s cycle=%s provider=%s",
        _user.id, org.id, body.tier, body.billing_cycle, body.provider,
    )

    billing_address = _resolve_billing_address(_user, body.billing_address, db)
    logger.debug(
        "billing.subscription_checkout address_resolved user_id=%s country=%s",
        _user.id, billing_address.get("country", "N/A"),
    )
    
    amount_chf = _resolve_tier_amount_chf(
        db,
        tier=body.tier,
        billing_cycle=body.billing_cycle,
        custom_features=(getattr(org, "custom_features", None) if body.tier == "custom" else None),
        verified_business=bool(getattr(org, "verified_business", False)),
    )
    logger.debug("billing.subscription_checkout amount_resolved amount_chf=%s", amount_chf)

    try:
        logger.info(
            "billing.subscription_checkout calling_provider provider=%s org_id=%s",
            body.provider or "default", org.id,
        )
        session = payments.create_subscription_checkout(
            org_id=org.id,
            user_id=_user.id,
            payment_alias_id=(_resolve_worldline_payment_alias(db, org) if body.provider in {None, "worldline"} else None),
            save_payment_method=body.save_payment_method,
            tier=body.tier,
            billing_cycle=body.billing_cycle,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            billing_address=billing_address,
            preferred_provider=body.provider,
            amount_chf=amount_chf,
        )
        logger.info(
            "billing.subscription_checkout_ok org_id=%s provider=%s checkout_url_prefix=%s",
            org.id, session.provider, session.checkout_url[:50] if session.checkout_url else "N/A",
        )
        if session.external_id:
            try:
                payment_transactions.log_payment_transaction(
                    db,
                    org_id=org.id,
                    provider=session.provider,  # type: ignore[arg-type]
                    external_id=session.external_id,
                    order_reference=session.order_reference or f"sub_{session.external_id[:24]}",
                    amount_chf=amount_chf,
                    kind="subscription",
                    status="pending",
                    subscription_tier=body.tier,
                    subscription_billing_cycle=body.billing_cycle,
                    billing_address=json.dumps(billing_address),
                )
            except payment_transactions.DuplicatePaymentError:
                logger.warning(
                    "billing.subscription_checkout_pending_duplicate org_id=%s token=%s",
                    org.id,
                    session.external_id[:20],
                )
            except payment_transactions.PaymentValidationError:
                logger.exception("billing.subscription_checkout_pending_log_failed org_id=%s", org.id)
    except payments.PaymentConfigurationError as exc:
        logger.exception("billing.subscription_checkout_config_error org_id=%s", org.id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("billing.subscription_checkout_runtime_error org_id=%s", org.id)
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
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> CheckoutResponse:
    _user, org = user_org
    logger.info(
        "billing.topup_checkout_start user_id=%s org_id=%s credits=%s provider=%s",
        _user.id, org.id, body.credits, body.provider,
    )

    billing_address = _resolve_billing_address(_user, body.billing_address, db)
    logger.debug(
        "billing.topup_checkout address_resolved user_id=%s country=%s",
        _user.id, billing_address.get("country", "N/A"),
    )
    
    amount_chf = payments.credits_to_chf(body.credits)
    logger.debug("billing.topup_checkout amount_resolved amount_chf=%s credits=%s", amount_chf, body.credits)

    try:
        logger.info(
            "billing.topup_checkout calling_provider provider=%s org_id=%s",
            body.provider or "default", org.id,
        )
        session = payments.create_topup_checkout(
            org_id=org.id,
            user_id=_user.id,
            payment_alias_id=(_resolve_worldline_payment_alias(db, org) if body.provider in {None, "worldline"} else None),
            save_payment_method=body.save_payment_method,
            credits=body.credits,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            billing_address=billing_address,
            preferred_provider=body.provider,
            amount_chf=amount_chf,
        )
        logger.info(
            "billing.topup_checkout_ok org_id=%s provider=%s checkout_url_prefix=%s",
            org.id, session.provider, session.checkout_url[:50] if session.checkout_url else "N/A",
        )
        if session.external_id:
            try:
                payment_transactions.log_payment_transaction(
                    db,
                    org_id=org.id,
                    provider=session.provider,  # type: ignore[arg-type]
                    external_id=session.external_id,
                    order_reference=session.order_reference or f"topup_{session.external_id[:24]}",
                    amount_chf=amount_chf,
                    kind="topup",
                    status="pending",
                    credits_purchased=body.credits,
                    billing_address=json.dumps(billing_address),
                )
            except payment_transactions.DuplicatePaymentError:
                logger.warning(
                    "billing.topup_checkout_pending_duplicate org_id=%s token=%s",
                    org.id,
                    session.external_id[:20],
                )
            except payment_transactions.PaymentValidationError:
                logger.exception("billing.topup_checkout_pending_log_failed org_id=%s", org.id)
    except payments.PaymentConfigurationError as exc:
        logger.exception("billing.topup_checkout_config_error org_id=%s", org.id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("billing.topup_checkout_runtime_error org_id=%s", org.id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    
    return CheckoutResponse(
        provider=session.provider,
        checkout_url=session.checkout_url,
        external_id=session.external_id,
        amount_chf=amount_chf,
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
    callback_ctx = payments.decode_worldline_callback_context(str(params.get("ctx") or "").strip())

    # Saferpay returns token as TOKEN query parameter, but accept variations
    token = str(token or params.get("TOKEN") or params.get("token") or params.get("Token") or "").strip()
    if token in {
        "{TOKEN}",
        "%7BTOKEN%7D",
        "{token}",
        "%7Btoken%7D",
        "{Token}",
        "%7BToken%7D",
        "{{{PAYMENTPAGETOKEN}}}",
        "%7B%7B%7BPAYMENTPAGETOKEN%7D%7D%7D",
    }:
        token = ""

    success_url = str(callback_ctx.get("success_url") or params.get("success_url") or "").strip()
    cancel_url = str(callback_ctx.get("cancel_url") or params.get("cancel_url") or "").strip()
    source = str(params.get("source") or "").strip().lower()
    order_reference = str(callback_ctx.get("order_reference") or params.get("order_reference") or "").strip()
    kind = str(callback_ctx.get("kind") or params.get("kind") or "").strip().lower()
    query_string = request.url.query or ""

    _emit(
        "info",
        "billing.worldline_return_called source=%s token=%s kind=%s order_ref=%s ctx_valid=%s query_string=%s",
        source,
        token[:20] if token else "NONE",
        kind,
        order_reference[:30] if order_reference else "NONE",
        "yes" if callback_ctx else "no",
        query_string[:1000],
    )

    if not callback_ctx and not token:
        _emit(
            "warning",
            "billing.worldline_return_invalid_context source=%s token=%s query=%s",
            source,
            "NONE",
            str(request.query_params)[:300],
        )
        target = _append_query_params(cancel_url, {"reason": "invalid_callback_context"})
        return RedirectResponse(_safe_redirect_target(target), status_code=status.HTTP_303_SEE_OTHER)

    pending_payment: PaymentTransaction | None = None
    if not token and callback_ctx and order_reference:
        pending_payment = payment_transactions.get_payment_transaction_by_order_reference(db, order_reference)
        if pending_payment is not None:
            token = str(pending_payment.external_id or "").strip()
            _emit(
                "info",
                "billing.worldline_token_from_pending source=%s order_ref=%s tx_id=%s token=%s",
                source,
                order_reference[:50],
                pending_payment.id,
                token[:20] if token else "NONE",
            )

    # If no token after recovery, we cannot verify with provider.
    if not token:
        _emit(
            "warning",
            "billing.worldline_return_no_token source=%s kind=%s order_ref=%s query=%s query_string=%s",
            source,
            kind,
            order_reference[:50] if order_reference else "NONE",
            str(request.query_params)[:300],
            query_string[:1000],
        )
        # Missing token means we cannot verify with provider, so treat as cancel/invalid.
        target = _append_query_params(cancel_url, {"reason": "missing_token"})
        return RedirectResponse(_safe_redirect_target(target), status_code=status.HTTP_303_SEE_OTHER)

    parsed_ref = payments.parse_worldline_merchant_reference(order_reference)
    _emit(
        "info",
        "billing.worldline_ref_parsed token=%s source=%s kind=%s org_id=%s user_id=%s tier=%s cycle=%s topup_credits=%s",
        token[:20],
        source,
        kind,
        parsed_ref.get("org_id"),
        parsed_ref.get("user_id"),
        parsed_ref.get("tier"),
        parsed_ref.get("billing_cycle"),
        parsed_ref.get("topup_credits"),
    )

    # BUG-GUARD: If we cannot parse org_id, the reference is invalid — decline immediately.
    # This prevents a KeyError crash and rejects forged/malformed references.
    if not parsed_ref.get("org_id"):
        _emit(
            "warning",
            "billing.worldline_invalid_reference token=%s order_ref=%s",
            token[:20],
            order_reference[:50] if order_reference else "NONE",
        )
        return RedirectResponse(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "invalid_reference"})),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # SECURITY: Check for existing payment to prevent double-processing.
    # Must happen before calling Saferpay to avoid paying twice on retries.
    existing_payment = payment_transactions.get_payment_transaction_by_external_id(db, token)
    if existing_payment:
        _emit(
            "info",
            "billing.worldline_existing_payment token=%s tx_id=%s status=%s kind=%s",
            token[:20],
            existing_payment.id,
            existing_payment.status,
            existing_payment.kind,
        )
        # Redirect based on the actual recorded outcome — never assume success.
        if existing_payment.status in {"authorized", "captured"}:
            return RedirectResponse(_safe_redirect_target(success_url), status_code=status.HTTP_303_SEE_OTHER)
        if existing_payment.status in {"declined", "error"}:
            return RedirectResponse(
                _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"})),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        pending_payment = existing_payment

    try:
        # Contact Saferpay to verify the token and get authoritative status.
        # This is the key security check — tokens not in Saferpay are rejected here.
        result = payments.WorldlineProvider().authorize_transaction(token=token)
        transaction = result.get("Transaction") if isinstance(result, dict) else {}
        transaction_status = str(transaction.get("Status") or "").upper() if isinstance(transaction, dict) else ""
        transaction_id = str(transaction.get("Id") or "") if isinstance(transaction, dict) else ""

        # Persist alias from successful checkout when Worldline returns it.
        payment_means = result.get("PaymentMeans") if isinstance(result, dict) else {}
        card_obj = payment_means.get("Card") if isinstance(payment_means, dict) else {}
        alias_obj = card_obj.get("Alias") if isinstance(card_obj, dict) else {}
        alias_id = str(alias_obj.get("Id") or "") if isinstance(alias_obj, dict) else ""
        if alias_id and parsed_ref.get("user_id"):
            alias_owner = db.get(User, int(parsed_ref["user_id"]))
            if alias_owner is not None:
                alias_owner.payment_customer_id = alias_id
                db.flush()

        # Normalize status from provider-specific values to our internal enum.
        normalized_status = payment_transactions.validate_transaction_status(transaction_status)
        _emit(
            "info",
            "billing.worldline_authorize_result token=%s provider_status=%s normalized_status=%s provider_tx_id=%s",
            token[:20],
            transaction_status or "NONE",
            normalized_status,
            transaction_id[:30] if transaction_id else "NONE",
        )

        # Extract payment method and cardholder identity (for chargeback evidence)
        payment_method = payment_transactions._extract_payment_method_worldline(transaction)
        cardholder_name = payment_transactions._extract_cardholder_name_worldline(result)

        # Log the transaction. The UNIQUE constraint on external_id is the DB-level
        # idempotency safeguard against races; the SELECT above handles the common case.
        # Retrieve billing address from the initiating user when available.
        org = db.query(Organization).filter(Organization.id == int(parsed_ref["org_id"])).first()
        # Determine CHF amount from the order reference, not user-supplied params.
        # Fall back to a non-zero placeholder so logging never rejects the transaction.
        amount_chf: float = 0.01
        if kind == "subscription" and parsed_ref.get("tier"):
            tier_name = str(parsed_ref["tier"])
            billing_cycle = str(parsed_ref.get("billing_cycle") or "monthly")
            if tier_name == "custom" and org is not None:
                amount_chf = payments.compute_subscription_price_chf(
                    tier=tier_name,
                    billing_cycle=billing_cycle,  # type: ignore[arg-type]
                    custom_features=getattr(org, "custom_features", None),
                    verified_business=bool(getattr(org, "verified_business", False)),
                )
            else:
                amount_chf = round(
                    get_tier_price_chf(db, tier_name) * (10.0 if billing_cycle == "yearly" else 1.0) * (0.8 if org and org.verified_business else 1.0),
                    2,
                )
        elif kind == "topup" and parsed_ref.get("topup_credits"):
            amount_chf = payments.credits_to_chf(int(parsed_ref["topup_credits"]))

        billing_address = None
        if parsed_ref.get("user_id"):
            user = db.get(User, int(parsed_ref["user_id"]))
            default_address = get_default_billing_address(user.billing_address_json) if user else None
            billing_address = json.dumps(default_address) if default_address else None
        if not billing_address and org is not None:
            billing_address = getattr(org, "billing_address_json", None)

        if pending_payment is not None:
            pending_payment.order_reference = order_reference or pending_payment.order_reference
            pending_payment.amount_chf = amount_chf
            pending_payment.kind = kind or pending_payment.kind
            pending_payment.status = normalized_status
            pending_payment.payment_method = payment_method
            pending_payment.provider_transaction_id = transaction_id or pending_payment.provider_transaction_id
            pending_payment.cardholder_name = cardholder_name
            pending_payment.billing_address = billing_address
            pending_payment.subscription_tier = str(parsed_ref["tier"]) if parsed_ref.get("tier") else pending_payment.subscription_tier
            pending_payment.subscription_billing_cycle = str(parsed_ref["billing_cycle"]) if parsed_ref.get("billing_cycle") else pending_payment.subscription_billing_cycle
            pending_payment.credits_purchased = int(parsed_ref["topup_credits"]) if parsed_ref.get("topup_credits") else pending_payment.credits_purchased
            db.commit()
            db.refresh(pending_payment)
            payment_tx = pending_payment
            _emit(
                "info",
                "billing.worldline_tx_updated token=%s tx_id=%s org_id=%s status=%s kind=%s amount_chf=%s",
                token[:20],
                payment_tx.id,
                payment_tx.org_id,
                payment_tx.status,
                payment_tx.kind,
                payment_tx.amount_chf,
            )
        else:
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
                cardholder_name=cardholder_name,
                billing_address=billing_address,
                subscription_tier=(str(parsed_ref["tier"]) if parsed_ref.get("tier") else None),
                subscription_billing_cycle=(str(parsed_ref["billing_cycle"]) if parsed_ref.get("billing_cycle") else None),
                credits_purchased=(int(parsed_ref["topup_credits"]) if parsed_ref.get("topup_credits") else None),
            )
        _emit(
            "info",
            "billing.worldline_tx_logged token=%s tx_id=%s org_id=%s status=%s kind=%s amount_chf=%s",
            token[:20],
            payment_tx.id,
            payment_tx.org_id,
            payment_tx.status,
            payment_tx.kind,
            payment_tx.amount_chf,
        )

        # Only apply business logic if payment succeeded.
        if normalized_status in {"authorized", "captured"}:
            _emit(
                "info",
                "billing.worldline_apply_start token=%s tx_id=%s kind=%s",
                token[:20],
                payment_tx.id,
                payment_tx.kind,
            )
            payment_transactions.apply_successful_payment(db, payment_tx)
            _emit(
                "info",
                "billing.worldline_apply_success token=%s tx_id=%s webhook_processed_at=%s credits_total_granted=%s",
                token[:20],
                payment_tx.id,
                payment_tx.webhook_processed_at,
                payment_tx.credits_total_granted,
            )
            return RedirectResponse(_safe_redirect_target(success_url), status_code=status.HTTP_303_SEE_OTHER)

        # Declined or unknown status — no credits granted.
        _emit(
            "warning",
            "billing.worldline_not_success token=%s tx_id=%s normalized_status=%s redirect_reason=payment_declined",
            token[:20],
            payment_tx.id,
            normalized_status,
        )
        return RedirectResponse(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"})),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    except payment_transactions.DuplicatePaymentError:
        _emit("warning", "billing.worldline_duplicate token=%s", token[:20])
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
        _emit("error", "billing.worldline_runtime_error token=%s message=%s", token[:20], message)
        logger.exception("billing.worldline_runtime_error token=%s message=%s", token[:20], message)
        # Saferpay-specific errors indicating the token was already consumed.
        if "TOKEN_INVALID" in message or "TRANSACTION_IN_WRONG_STATE" in message:
            return RedirectResponse(
                _safe_redirect_target(_append_query_params(success_url, {"already_processed": "true"})),
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc

    except payment_transactions.PaymentValidationError as exc:
        _emit("error", "billing.worldline_validation_error token=%s message=%s", token[:20], str(exc))
        logger.exception("billing.worldline_validation_error token=%s message=%s", token[:20], str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/payment-methods/worldline/register", response_model=PaymentMethodRegistrationResponse)
def create_worldline_card_registration(
    body: CardRegistrationRequest,
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> PaymentMethodRegistrationResponse:
    _user, org = user_org
    billing_address = _resolve_billing_address(_user, body.billing_address, db)
    try:
        session = payments.WorldlineProvider().create_card_alias_registration(
            org_id=org.id,
            user_id=_user.id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            billing_address=billing_address,
        )
    except payments.PaymentConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PaymentMethodRegistrationResponse(provider=session.provider, checkout_url=session.checkout_url, external_id=session.external_id)


@router.get("/webhooks/worldline/card/return")
@router.get("/webhooks/worldline/card/return/{token}")
async def worldline_card_return(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
) -> RedirectResponse:
    params = request.query_params
    callback_ctx = payments.decode_worldline_callback_context(str(params.get("ctx") or "").strip())
    token = str(token or params.get("TOKEN") or params.get("token") or params.get("Token") or "").strip()
    if token in {"{TOKEN}", "%7BTOKEN%7D", "{token}", "%7Btoken%7D", "{Token}", "%7BToken%7D", "{{{PAYMENTPAGETOKEN}}}", "%7B%7B%7BPAYMENTPAGETOKEN%7D%7D%7D"}:
        token = ""

    success_url = str(callback_ctx.get("success_url") or params.get("success_url") or "").strip()
    cancel_url = str(callback_ctx.get("cancel_url") or params.get("cancel_url") or "").strip()
    source = str(params.get("source") or "").strip().lower()
    query_string = request.url.query or ""

    _emit(
        "info",
        "billing.worldline_card_return_called source=%s token=%s ctx_valid=%s query_string=%s",
        source,
        token[:20] if token else "NONE",
        "yes" if callback_ctx else "no",
        query_string[:1000],
    )

    if not callback_ctx and not token:
        target = _append_query_params(cancel_url, {"reason": "invalid_callback_context"})
        if source == "notify":
            return RedirectResponse(_safe_redirect_target(target), status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(_safe_redirect_target(target), status_code=status.HTTP_303_SEE_OTHER)

    if not token:
        target = _append_query_params(cancel_url, {"reason": "missing_token"})
        return RedirectResponse(_safe_redirect_target(target), status_code=status.HTTP_303_SEE_OTHER)

    try:
        org_id = int(callback_ctx.get("org_id") or 0)
        user_id = int(callback_ctx.get("user_id") or 0)
        if org_id <= 0:
            raise RuntimeError("Worldline alias callback missing org_id")
        if user_id <= 0:
            raise RuntimeError("Worldline alias callback missing user_id")
        owner = db.get(User, user_id)
        if owner is None:
            raise RuntimeError("User not found")
        result = payments.WorldlineProvider().assert_alias_insert(token=token)
        alias = result.get("Alias") if isinstance(result, dict) else {}
        alias_id = str(alias.get("Id") or "") if isinstance(alias, dict) else ""
        payment_means = result.get("PaymentMeans") if isinstance(result, dict) else {}
        card = payment_means.get("Card") if isinstance(payment_means, dict) else {}
        display_text = str(payment_means.get("DisplayText") or "") if isinstance(payment_means, dict) else ""

        if not alias_id:
            raise RuntimeError("Worldline alias assertion did not include an alias id")

        owner.payment_customer_id = alias_id
        db.commit()

        _emit(
            "info",
            "billing.worldline_card_alias_saved org_id=%s alias_id=%s display_text=%s holder=%s",
            org_id,
            alias_id,
            display_text[:40] if display_text else "NONE",
            str(card.get("HolderName") or "")[:40] if isinstance(card, dict) else "NONE",
        )

        if source == "notify":
            return RedirectResponse(_safe_redirect_target(success_url or payments.settings.app_base_url.rstrip("/")), status_code=status.HTTP_303_SEE_OTHER)

        return RedirectResponse(
            _safe_redirect_target(_append_query_params(success_url, {"payment_method": "saved"})),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except payments.PaymentConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/providers")
def list_enabled_providers(_: User = Depends(get_current_user)) -> dict:
    return {"mode": payments.settings.payment_provider_mode, "enabled": payments.get_enabled_provider_order()}


@router.get("/tiers", response_model=list[BillingTierRead])
def list_billing_tiers(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[BillingTierRead]:
    tiers = get_billing_tiers(db)
    return [
        BillingTierRead(
            id=tier.id,
            slug=tier.slug,
            display_name=tier.display_name,
            description=tier.description,
            monthly_price_chf=float(tier.monthly_price_chf),
            yearly_multiplier=float(tier.yearly_multiplier),
            yearly_price_chf=float(tier.monthly_price_chf) * float(tier.yearly_multiplier),
            topup_bonus_rate=float(tier.topup_bonus_rate),
            sort_order=tier.sort_order,
            is_active=tier.is_active,
            is_public=tier.is_public,
        )
        for tier in tiers
    ]


# ── User-facing billing history (org-scoped) ───────────────────────────────────


@router.get("/summary")
def get_billing_summary(
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    """Return billing summary for the current org: tier, balance, subscription info."""
    user, org = user_org
    return {
        "org_id": org.id,
        "tier": org.tier,
        "billing_cycle": org.subscription_billing_cycle,
        "subscription_period_end": org.subscription_period_end.isoformat() if org.subscription_period_end else None,
        "credits_balance": org.credits_balance,
        "credits_balance_chf": round(org.credits_balance * 0.0001, 4),
        "has_saved_payment_method": bool(user.payment_customer_id),
    }


@router.get("/credits")
def list_credit_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    """Return paginated credit ledger for the current org (grants, topups, deductions, bonuses)."""
    _user, org = user_org
    query = db.query(OrgCreditTransaction).filter(OrgCreditTransaction.org_id == org.id)
    total = query.count()
    rows = (
        query.order_by(desc(OrgCreditTransaction.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": tx.id,
                "amount": tx.amount,
                "type": tx.type,
                "action_type": tx.action_type,
                "reference_id": tx.reference_id,
                "credits_before": tx.credits_before,
                "credits_after": tx.credits_after,
                "created_at": tx.created_at.isoformat(),
            }
            for tx in rows
        ],
    }


@router.get("/payments")
def list_payment_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    """Return paginated payment transaction history for the current org.

    Exposes only safe fields — no raw provider tokens or internal error codes.
    """
    _user, org = user_org
    query = db.query(PaymentTransaction).filter(PaymentTransaction.org_id == org.id)
    total = query.count()
    rows = (
        query.order_by(desc(PaymentTransaction.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": tx.id,
                "provider": tx.provider,
                "kind": tx.kind,
                "status": tx.status,
                "amount_chf": tx.amount_chf,
                "payment_method": tx.payment_method,
                "subscription_tier": tx.subscription_tier,
                "subscription_billing_cycle": tx.subscription_billing_cycle,
                "credits_purchased": tx.credits_purchased,
                "credits_bonus": tx.credits_bonus,
                "credits_total_granted": tx.credits_total_granted,
                "created_at": tx.created_at.isoformat(),
                "authorized_at": tx.authorized_at.isoformat() if tx.authorized_at else None,
                "refunded_at": tx.refunded_at.isoformat() if tx.refunded_at else None,
                "refunded_amount_chf": tx.refunded_amount_chf,
            }
            for tx in rows
        ],
    }
