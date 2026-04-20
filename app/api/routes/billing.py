"""Billing routes for subscription checkout, top-ups, and provider webhooks."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _emit(level: str, message: str, *args: object) -> None:
    """Emit to logger."""
    log_fn = getattr(logger, level, logger.info)
    log_fn(message, *args)

from app.api.deps import get_current_org
from app.api.deps import require_org_role
from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.models.org_credit_transaction import OrgCreditTransaction
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.schemas.billing import BillingAddress, BillingTierRead
from app.services.billing_addresses import get_default_billing_address
from app.services import payment_transactions, payments
from app.services.tiers import (
    TIER_RANK,
    get_billing_tier_by_slug,
    get_billing_tiers,
    get_tier_price_chf,
    get_tier_yearly_price_chf,
    normalize_tier,
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
    upgrade_proration_credits: int | None = None


class TopupCheckoutRequest(BaseModel):
    credits: int = Field(..., ge=100)
    success_url: str
    cancel_url: str
    billing_address: BillingAddress | None = None
    save_payment_method: bool = True
    use_new_card: bool = False
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


def _resolve_worldline_payment_alias(db: Session, org: Organization, current_user: User | None = None) -> str | None:
    if current_user is not None:
        current_user_alias = str(current_user.payment_customer_id or "").strip()
        if current_user_alias:
            return current_user_alias

    owner_id = getattr(org, "default_payment_user_id", None)
    if not owner_id:
        return None
    owner = db.get(User, int(owner_id))
    if owner is None:
        return None
    alias_id = str(owner.payment_customer_id or "").strip()
    return alias_id or None


def _cancel_provider_transaction(tx: PaymentTransaction) -> None:
    """Best-effort provider-side cancellation for pending transactions."""
    if tx.provider != "worldline":
        return
    provider_tx_id = str(tx.provider_transaction_id or "").strip()
    if not provider_tx_id:
        _emit(
            "info",
            "billing.provider_cancel_skip tx_id=%s provider=%s reason=missing_provider_transaction_id",
            tx.id,
            tx.provider,
        )
        return
    try:
        payments.WorldlineProvider().cancel_transaction(transaction_id=provider_tx_id)
        _emit(
            "info",
            "billing.provider_cancel_ok tx_id=%s provider=%s provider_tx_id=%s",
            tx.id,
            tx.provider,
            provider_tx_id[:30],
        )
    except (payments.PaymentConfigurationError, RuntimeError) as exc:
        _emit(
            "warning",
            "billing.provider_cancel_failed tx_id=%s provider=%s provider_tx_id=%s error=%s",
            tx.id,
            tx.provider,
            provider_tx_id[:30],
            str(exc),
        )


def _start_worldline_alias_polling(*, org_id: int, user_id: int, order_reference: str, token: str) -> None:
    worker = threading.Thread(
        target=_poll_worldline_alias_registration,
        kwargs={
            "org_id": org_id,
            "user_id": user_id,
            "order_reference": order_reference,
            "token": token,
        },
        daemon=True,
        name=f"worldline-alias-poll-{org_id}-{user_id}",
    )
    worker.start()


def _poll_worldline_alias_registration(*, org_id: int, user_id: int, order_reference: str, token: str) -> None:
    _emit(
        "info",
        "billing.worldline_alias_poll_start org_id=%s user_id=%s order_ref=%s token=%s",
        org_id,
        user_id,
        order_reference[:50],
        token[:20],
    )
    try:
        result = payments.WorldlineProvider().wait_for_alias_registration(
            token=token,
            max_attempts=15,
            poll_interval_seconds=60,
        )
        alias = result.get("Alias") if isinstance(result, dict) else {}
        alias_id = str(alias.get("Id") or "") if isinstance(alias, dict) else ""
        if not alias_id:
            raise RuntimeError("Worldline alias assertion did not include an alias id")

        db = SessionLocal()
        try:
            owner = db.get(User, user_id)
            if owner is None:
                raise RuntimeError("User not found")
            owner.payment_customer_id = alias_id
            db.commit()
        finally:
            db.close()

        _emit(
            "info",
            "billing.worldline_alias_poll_saved org_id=%s user_id=%s alias_id=%s",
            org_id,
            user_id,
            alias_id,
        )
    except (payments.PaymentConfigurationError, RuntimeError) as exc:
        _emit(
            "warning",
            "billing.worldline_alias_poll_failed org_id=%s user_id=%s order_ref=%s error=%s",
            org_id,
            user_id,
            order_reference[:50],
            str(exc),
        )
    finally:
        payments.clear_pending_card_alias_token(order_reference=order_reference)


def _safe_redirect_target(url: str | None) -> str:
    base = payments.settings.app_base_url.rstrip("/")
    if not url:
        return base
    parsed = urlparse(url)
    allowed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return base
    # Only allow redirects to the same host (and port) as the app itself.
    if parsed.netloc != allowed.netloc:
        logger.warning("billing.redirect_blocked_open_redirect url=%s allowed=%s", url[:80], allowed.netloc)
        return base
    return url


def _append_query_params(url: str, params: dict[str, str]) -> str:
    """Append query params to a URL that may or may not already have a query string."""
    if not params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _iframe_redirect(url: str) -> HTMLResponse:
    """Return an HTML page that navigates both iframe and top-level frames to url.

    When Saferpay is embedded in an iframe, RedirectResponse only redirects
    the iframe itself.  This page uses JavaScript to break out to the top frame,
    with a <noscript> meta-refresh fallback.
    """
    safe = url.replace('"', "%22").replace("'", "%27").replace("<", "%3C").replace(">", "%3E")
    content = (
        "<!DOCTYPE html><html><head>"
        f'<meta http-equiv="refresh" content="0;url={safe}">'
        "</head><body>"
        "<script>"
        f"var t={repr(url)};"
        "if(window.top!==window.self){window.top.location.href=t;}else{window.location.href=t;}"
        "</script>"
        "</body></html>"
    )
    return HTMLResponse(content=content, status_code=200)


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
            payment_alias_id=(_resolve_worldline_payment_alias(db, org, _user) if body.provider in {None, "worldline"} else None),
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
                    upgrade_proration_credits=body.upgrade_proration_credits,
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
            payment_alias_id=(
                _resolve_worldline_payment_alias(db, org, _user)
                if body.provider in {None, "worldline"} and not body.use_new_card
                else None
            ),
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


@router.get("/webhooks/worldline/return", response_model=None)
@router.get("/webhooks/worldline/return/{token}", response_model=None)
async def worldline_return(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
) -> HTMLResponse | RedirectResponse:
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
    save_payment_method = str(callback_ctx.get("save_payment_method") or "").strip().lower() in {"1", "true", "yes", "on"}
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
        return _iframe_redirect(_safe_redirect_target(target))

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
        return _iframe_redirect(_safe_redirect_target(target))

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
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "invalid_reference"}))
        )

    # SECURITY: Check for existing payment to prevent double-processing.
    # Must happen before calling Saferpay to avoid paying twice on retries.
    existing_payment = payment_transactions.get_payment_transaction_by_external_id(db, token)
    if existing_payment:
        if existing_payment.status == "pending" and existing_payment.created_at <= datetime.now(tz=timezone.utc) - timedelta(minutes=15):
            existing_payment.status = "declined"
            existing_payment.error_code = "PENDING_TIMEOUT"
            existing_payment.error_message = "Payment expired after 15 minutes"
            existing_payment.webhook_processed_at = datetime.now(tz=timezone.utc)
            db.commit()
            db.refresh(existing_payment)

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
            return _iframe_redirect(_safe_redirect_target(success_url))
        if existing_payment.status in {"declined", "error"}:
            return _iframe_redirect(
                _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"}))
            )
        pending_payment = existing_payment

    try:
        # Contact Saferpay to verify the token and get authoritative status.
        # This is the key security check — tokens not in Saferpay are rejected here.
        result = payments.WorldlineProvider().authorize_transaction(
            token=token,
            save_payment_method=save_payment_method,
        )
        transaction = result.get("Transaction") if isinstance(result, dict) else {}
        transaction_status = str(transaction.get("Status") or "").upper() if isinstance(transaction, dict) else ""
        transaction_id = str(transaction.get("Id") or "") if isinstance(transaction, dict) else ""

        # Persist alias from successful checkout when Worldline returns it.
        # Some responses place alias under PaymentMeans.Card.Alias while others
        # return it under RegistrationResult.Alias.
        payment_means = result.get("PaymentMeans") if isinstance(result, dict) else {}
        card_obj = payment_means.get("Card") if isinstance(payment_means, dict) else {}
        alias_obj = card_obj.get("Alias") if isinstance(card_obj, dict) else {}
        alias_id = str(alias_obj.get("Id") or "") if isinstance(alias_obj, dict) else ""
        if not alias_id and isinstance(result, dict):
            registration_result = result.get("RegistrationResult")
            registration_alias = registration_result.get("Alias") if isinstance(registration_result, dict) else {}
            alias_id = str(registration_alias.get("Id") or "") if isinstance(registration_alias, dict) else ""
        if not alias_id and isinstance(result, dict):
            top_level_alias = result.get("Alias")
            alias_id = str(top_level_alias.get("Id") or "") if isinstance(top_level_alias, dict) else ""
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

        # Capture the transaction if it is only authorized — Saferpay requires an
        # explicit Capture call to settle funds.  CAPTURED transactions are already
        # settled (e.g. via AuthorizeReferenced) and must not be captured again.
        if normalized_status == "authorized" and transaction_id:
            try:
                payments.WorldlineProvider().capture_transaction(transaction_id=transaction_id)
                _emit(
                    "info",
                    "billing.worldline_capture_ok token=%s tx_id=%s provider_tx=%s",
                    token[:20], payment_tx.id, transaction_id[:20],
                )
                payment_tx.status = "captured"
                normalized_status = "captured"
                db.commit()
            except (payments.PaymentConfigurationError, RuntimeError) as cap_exc:
                # Non-fatal: the payment is authorized so the user gets access now.
                # The transaction stays as "authorized" and can be captured manually.
                _emit(
                    "warning",
                    "billing.worldline_capture_failed token=%s tx_id=%s provider_tx=%s error=%s",
                    token[:20], payment_tx.id, transaction_id[:20], cap_exc,
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
            # Store recurring reference for subscription payments so the nightly
            # billing_renewal job can charge via AuthorizeReferenced.
            if kind == "subscription" and transaction_id and org is not None:
                org.recurring_transaction_id = transaction_id
                org.subscription_cancel_at_period_end = False
                db.commit()
                _emit(
                    "info",
                    "billing.recurring_tx_stored org_id=%s tx_id=%s",
                    org.id, transaction_id[:20],
                )
            _emit(
                "info",
                "billing.worldline_apply_success token=%s tx_id=%s webhook_processed_at=%s credits_total_granted=%s",
                token[:20],
                payment_tx.id,
                payment_tx.webhook_processed_at,
                payment_tx.credits_total_granted,
            )
            return _iframe_redirect(_safe_redirect_target(success_url))

        # Declined or unknown status — no credits granted.
        _emit(
            "warning",
            "billing.worldline_not_success token=%s tx_id=%s normalized_status=%s redirect_reason=payment_declined",
            token[:20],
            payment_tx.id,
            normalized_status,
        )
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"}))
        )

    except payment_transactions.DuplicatePaymentError:
        _emit("warning", "billing.worldline_duplicate token=%s", token[:20])
        # Race condition: two identical requests arrived simultaneously.
        # Re-read the actual stored outcome to decide where to send the user.
        existing = payment_transactions.get_payment_transaction_by_external_id(db, token)
        if existing and existing.status in {"authorized", "captured"}:
            return _iframe_redirect(_safe_redirect_target(success_url))
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"}))
        )

    except RuntimeError as exc:
        message = str(exc)
        _emit("error", "billing.worldline_runtime_error token=%s message=%s", token[:20], message)
        logger.exception("billing.worldline_runtime_error token=%s message=%s", token[:20], message)
        # Saferpay-specific errors indicating the token was already consumed.
        if "TOKEN_INVALID" in message or "TRANSACTION_IN_WRONG_STATE" in message:
            return _iframe_redirect(
                _safe_redirect_target(_append_query_params(success_url, {"already_processed": "true"}))
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc

    except payment_transactions.PaymentValidationError as exc:
        _emit("error", "billing.worldline_validation_error token=%s message=%s", token[:20], str(exc))
        logger.exception("billing.worldline_validation_error token=%s message=%s", token[:20], str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/payment-methods/worldline/register", response_model=PaymentMethodRegistrationResponse)
def create_worldline_card_registration(
    body: CardRegistrationRequest,
    user_org: tuple[User, object] = Depends(get_current_org),
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

    if session.external_id and session.order_reference:
        _start_worldline_alias_polling(
            org_id=org.id,
            user_id=_user.id,
            order_reference=session.order_reference,
            token=session.external_id,
        )

    return PaymentMethodRegistrationResponse(provider=session.provider, checkout_url=session.checkout_url, external_id=session.external_id)


@router.get("/webhooks/worldline/card/return", response_model=None)
@router.get("/webhooks/worldline/card/return/{token}", response_model=None)
async def worldline_card_return(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
) -> HTMLResponse | RedirectResponse:
    params = request.query_params
    callback_ctx = payments.decode_worldline_callback_context(str(params.get("ctx") or "").strip())
    token = str(token or params.get("TOKEN") or params.get("token") or params.get("Token") or "").strip()
    if token in {"{TOKEN}", "%7BTOKEN%7D", "{token}", "%7Btoken%7D", "{Token}", "%7BToken%7D", "{{{PAYMENTPAGETOKEN}}}", "%7B%7B%7BPAYMENTPAGETOKEN%7D%7D%7D"}:
        token = ""

    success_url = str(callback_ctx.get("success_url") or params.get("success_url") or "").strip()
    cancel_url = str(callback_ctx.get("cancel_url") or params.get("cancel_url") or "").strip()
    order_reference = str(callback_ctx.get("order_reference") or params.get("order_reference") or "").strip()
    org_id = int(callback_ctx.get("org_id") or 0)
    user_id = int(callback_ctx.get("user_id") or 0)
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
        return _iframe_redirect(_safe_redirect_target(target))

    if not token and order_reference:
        pending_token = payments.get_pending_card_alias_token(
            order_reference=order_reference,
            org_id=(org_id if org_id > 0 else None),
            user_id=(user_id if user_id > 0 else None),
        )
        if pending_token:
            token = pending_token
            _emit(
                "info",
                "billing.worldline_card_token_from_pending source=%s order_ref=%s token=%s",
                source,
                order_reference[:50],
                token[:20],
            )

    if not token and user_id > 0:
        owner = db.get(User, user_id)
        # Background polling may have already completed and persisted the alias.
        if owner is not None and str(owner.payment_customer_id or "").strip():
            return _iframe_redirect(
                _safe_redirect_target(_append_query_params(success_url, {"payment_method": "saved"}))
            )

    if not token:
        target = _append_query_params(cancel_url, {"reason": "missing_token"})
        return _iframe_redirect(_safe_redirect_target(target))

    try:
        if org_id <= 0:
            raise RuntimeError("Worldline alias callback missing org_id")
        if user_id <= 0:
            raise RuntimeError("Worldline alias callback missing user_id")
        owner = db.get(User, user_id)
        if owner is None:
            raise RuntimeError("User not found")
        result = payments.WorldlineProvider().wait_for_alias_registration(
            token=token,
            max_attempts=15,
            poll_interval_seconds=60,
        )
        alias = result.get("Alias") if isinstance(result, dict) else {}
        alias_id = str(alias.get("Id") or "") if isinstance(alias, dict) else ""
        payment_means = result.get("PaymentMeans") if isinstance(result, dict) else {}
        card = payment_means.get("Card") if isinstance(payment_means, dict) else {}
        display_text = str(payment_means.get("DisplayText") or "") if isinstance(payment_means, dict) else ""

        if not alias_id:
            raise RuntimeError("Worldline alias assertion did not include an alias id")

        owner.payment_customer_id = alias_id
        db.commit()
        if order_reference:
            payments.clear_pending_card_alias_token(order_reference=order_reference)

        _emit(
            "info",
            "billing.worldline_card_alias_saved org_id=%s alias_id=%s display_text=%s holder=%s",
            org_id,
            alias_id,
            display_text[:40] if display_text else "NONE",
            str(card.get("HolderName") or "")[:40] if isinstance(card, dict) else "NONE",
        )

        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(success_url, {"payment_method": "saved"}))
        )
    except payments.PaymentConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        _emit(
            "warning",
            "billing.worldline_card_alias_failed source=%s org_id=%s user_id=%s order_ref=%s error=%s",
            source,
            org_id,
            user_id,
            order_reference[:50] if order_reference else "NONE",
            str(exc),
        )
        target = _append_query_params(cancel_url, {"reason": "alias_registration_failed"})
        return _iframe_redirect(_safe_redirect_target(target))


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
    payment_transactions.expire_stale_pending_transactions(
        db,
        org_id=org.id,
        max_age_minutes=15,
        on_expire=_cancel_provider_transaction,
    )
    from app import crud as _crud
    alert_at = _crud.get_effective_setting(db, "low_credit_alert_at", org_id=org.id, default="")
    return {
        "org_id": org.id,
        "tier": org.tier,
        "billing_cycle": org.subscription_billing_cycle,
        "subscription_period_end": org.subscription_period_end.isoformat() if org.subscription_period_end else None,
        "subscription_cancel_at_period_end": bool(getattr(org, "subscription_cancel_at_period_end", False)),
        "pending_downgrade_tier": getattr(org, "pending_downgrade_tier", None),
        "credits_balance": org.credits_balance,
        "credits_balance_chf": round(org.credits_balance * 0.0001, 4),
        "has_saved_payment_method": bool(_resolve_worldline_payment_alias(db, org, user)),
        "low_credit_alert_at": alert_at or None,
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


@router.get("/credits/usage")
def get_credit_usage(
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    """Return per-action credit consumption for the org over the last N days.

    Groups deductions by action_type and returns totals + refunds.
    Useful for the org billing dashboard to show what's spending credits.
    """
    from datetime import timedelta, timezone
    from sqlalchemy import func

    _user, org = user_org
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)

    rows = (
        db.query(
            OrgCreditTransaction.action_type,
            OrgCreditTransaction.type,
            func.sum(OrgCreditTransaction.amount).label("total"),
            func.count(OrgCreditTransaction.id).label("count"),
        )
        .filter(
            OrgCreditTransaction.org_id == org.id,
            OrgCreditTransaction.created_at >= since,
            OrgCreditTransaction.type.in_(["deduction", "refund"]),
        )
        .group_by(OrgCreditTransaction.action_type, OrgCreditTransaction.type)
        .all()
    )

    # Build per-action summary: {action: {spent, refunded, net, transactions}}
    summary: dict[str, dict] = {}
    for action_type, tx_type, total, count in rows:
        key = action_type or "unknown"
        if key not in summary:
            summary[key] = {"spent": 0, "refunded": 0, "net": 0, "transactions": 0}
        if tx_type == "deduction":
            summary[key]["spent"] += abs(int(total))
        elif tx_type == "refund":
            summary[key]["refunded"] += int(total)
        summary[key]["transactions"] += int(count)

    for v in summary.values():
        v["net"] = v["spent"] - v["refunded"]

    total_spent = sum(v["spent"] for v in summary.values())
    total_refunded = sum(v["refunded"] for v in summary.values())

    return {
        "days": days,
        "total_spent": total_spent,
        "total_refunded": total_refunded,
        "net_spent": total_spent - total_refunded,
        "current_balance": int(org.credits_balance or 0),
        "by_action": summary,
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
    payment_transactions.expire_stale_pending_transactions(
        db,
        org_id=org.id,
        max_age_minutes=15,
        on_expire=_cancel_provider_transaction,
    )
    query = db.query(PaymentTransaction).filter(PaymentTransaction.org_id == org.id)
    total = query.count()
    rows = (
        query.order_by(desc(PaymentTransaction.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    def _decline_reason(tx: PaymentTransaction) -> str | None:
        if tx.error_code == "PENDING_TIMEOUT":
            return "Expired (15 min timeout)"
        if tx.error_code == "MANUAL_CANCELLED":
            return "Cancelled by user"
        if tx.status == "declined":
            return "Declined"
        return None

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
                "decline_reason": _decline_reason(tx),
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


@router.post("/subscription/cancel", response_model=WebhookResponse)
def cancel_subscription(
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Schedule subscription cancellation at the end of the current billing period.

    The org keeps its current tier and access until subscription_period_end.
    The nightly billing_renewal job will downgrade to free when the period ends.
    If no period_end is set, downgrade immediately.
    Credits in the balance are kept. No refund is issued.
    """
    _user, org = user_org
    logger.info(
        "billing.subscription_cancel user_id=%s org_id=%s current_tier=%s period_end=%s",
        _user.id, org.id, org.tier,
        org.subscription_period_end.isoformat() if org.subscription_period_end else "None",
    )
    if getattr(org, "tier", "free") == "free":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Organization is already on the free tier")

    if not org.subscription_period_end:
        # No period end on record — anchor it to now so the nightly billing_renewal
        # job picks this org up on its next run and downgrades it then.
        # We never hard-downgrade here: the user keeps access until the job runs.
        org.subscription_period_end = datetime.now(tz=timezone.utc)
        logger.info(
            "billing.subscription_cancel_no_period_end org_id=%s — anchoring period_end to now",
            org.id,
        )

    # Soft cancel: tier stays active until subscription_period_end.
    # billing_renewal handles the actual downgrade to free.
    org.subscription_cancel_at_period_end = True
    db.commit()
    logger.info(
        "billing.subscription_cancel_scheduled org_id=%s period_end=%s",
        org.id, org.subscription_period_end.isoformat(),
    )

    return WebhookResponse(ok=True)


@router.post("/subscription/reactivate", response_model=WebhookResponse)
def reactivate_subscription(
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Undo a scheduled cancellation — resume automatic renewal at period end."""
    _user, org = user_org
    if not getattr(org, "subscription_cancel_at_period_end", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Subscription is not scheduled for cancellation")
    if getattr(org, "tier", "free") == "free":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Organization is on the free tier")
    org.subscription_cancel_at_period_end = False
    org.pending_downgrade_tier = None
    db.commit()
    logger.info("billing.subscription_reactivated org_id=%s tier=%s", org.id, org.tier)
    return WebhookResponse(ok=True)


class ScheduleDowngradeRequest(BaseModel):
    tier: str


@router.post("/subscription/schedule-downgrade", response_model=WebhookResponse)
def schedule_downgrade(
    body: ScheduleDowngradeRequest,
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Schedule a downgrade to a lower tier at the end of the current billing period.

    The org keeps its current tier and access until subscription_period_end.
    The nightly billing_renewal job will then switch to the requested tier (charging
    for its first period) instead of dropping to free.
    """
    _user, org = user_org
    current_tier = normalize_tier(getattr(org, "tier", "free"))
    target_tier = normalize_tier(body.tier)

    if current_tier == "free":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active paid subscription")
    if TIER_RANK.get(target_tier, 0) >= TIER_RANK.get(current_tier, 0):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target tier must be lower than current tier")
    if getattr(org, "subscription_cancel_at_period_end", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Subscription is already scheduled for cancellation")

    if not org.subscription_period_end:
        org.subscription_period_end = datetime.now(tz=timezone.utc)

    org.subscription_cancel_at_period_end = True
    org.pending_downgrade_tier = target_tier
    db.commit()
    logger.info(
        "billing.downgrade_scheduled org_id=%s current_tier=%s target_tier=%s period_end=%s",
        org.id, current_tier, target_tier,
        org.subscription_period_end.isoformat() if org.subscription_period_end else "None",
    )
    return WebhookResponse(ok=True)


class UpgradeProrationResponse(BaseModel):
    credits_granted: int
    credits_chf: float
    remaining_days: int
    plan_cost_chf: float


@router.post("/subscription/upgrade-proration", response_model=UpgradeProrationResponse)
def calculate_upgrade_proration(
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> UpgradeProrationResponse:
    """Calculate proration credits for upgrading from the current plan.

    Returns the amount that will be granted once the upgrade payment is captured.
    Credits are NOT granted here — they are granted in apply_successful_payment
    after a confirmed subscription payment with upgrade_proration_credits set.

    Formula: int((plan_cost_chf / 50) * remaining_days) * 10_000 credits.
    Only valid when the org has an active paid subscription that is not
    scheduled for cancellation.
    """
    _user, org = user_org
    current_tier = normalize_tier(getattr(org, "tier", "free"))
    if current_tier == "free":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active paid subscription to prorate")
    if getattr(org, "subscription_cancel_at_period_end", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Subscription is already scheduled for cancellation")

    now = datetime.now(tz=timezone.utc)
    period_end = getattr(org, "subscription_period_end", None)
    if period_end is None or period_end <= now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active subscription period found")

    remaining_days = max(0, (period_end - now).days)
    billing_cycle = getattr(org, "subscription_billing_cycle", "monthly") or "monthly"
    plan_cost_chf = _resolve_tier_amount_chf(
        db,
        tier=current_tier,
        billing_cycle=billing_cycle,
        custom_features=getattr(org, "custom_features", None),
        verified_business=bool(getattr(org, "verified_business", False)),
    )

    credits_granted = int((plan_cost_chf / 50) * remaining_days * 10_000)
    logger.info(
        "billing.upgrade_proration_calculated org_id=%s tier=%s remaining_days=%s credits=%s",
        org.id, current_tier, remaining_days, credits_granted,
    )

    return UpgradeProrationResponse(
        credits_granted=credits_granted,
        credits_chf=round(credits_granted * 0.0001, 4),
        remaining_days=remaining_days,
        plan_cost_chf=plan_cost_chf,
    )


@router.post("/payments/{payment_id}/cancel", response_model=WebhookResponse)
def cancel_pending_payment(
    payment_id: int,
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    _user, org = user_org
    tx = db.query(PaymentTransaction).filter(PaymentTransaction.id == payment_id, PaymentTransaction.org_id == org.id).first()
    if tx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    # Ensure stale pending payments are closed before manual action check.
    payment_transactions.expire_stale_pending_transactions(
        db,
        org_id=org.id,
        max_age_minutes=15,
        on_expire=_cancel_provider_transaction,
    )
    db.refresh(tx)

    try:
        payment_transactions.cancel_pending_transaction(
            db,
            tx=tx,
            on_cancel=_cancel_provider_transaction,
        )
    except payment_transactions.PaymentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WebhookResponse(ok=True)


@router.get("/payments/{payment_id}/invoice", response_class=HTMLResponse)
def get_payment_invoice(
    payment_id: int,
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Generate and return an HTML invoice/receipt for a captured payment.

    The invoice can be printed or saved as PDF from the browser.
    Only accessible to members of the org that made the payment.
    """
    _user, org = user_org
    tx = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.id == payment_id, PaymentTransaction.org_id == org.id)
        .first()
    )
    if tx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if tx.status not in {"captured", "authorized"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice is only available for completed payments",
        )

    # Parse billing address
    billing_addr_str = ""
    if tx.billing_address:
        try:
            addr = json.loads(tx.billing_address)
            parts = []
            if addr.get("company_name"):
                parts.append(addr["company_name"])
            name_parts = []
            if addr.get("first_name"):
                name_parts.append(addr["first_name"])
            if addr.get("last_name"):
                name_parts.append(addr["last_name"])
            if name_parts:
                parts.append(" ".join(name_parts))
            street = f"{addr.get('street', '')} {addr.get('number', '')}".strip()
            if street:
                parts.append(street)
            city_line = f"{addr.get('postal_code', '')} {addr.get('city', '')}".strip()
            if city_line:
                parts.append(city_line)
            if addr.get("country"):
                parts.append(addr["country"].upper())
            billing_addr_str = "<br>".join(p for p in parts if p)
        except (ValueError, TypeError):
            billing_addr_str = tx.billing_address or ""

    # Format amounts
    amount_str = f"CHF {tx.amount_chf:.2f}"
    refund_str = f"CHF {tx.refunded_amount_chf:.2f}" if tx.refunded_amount_chf else None
    net_amount = tx.amount_chf - (tx.refunded_amount_chf or 0)
    net_amount_str = f"CHF {net_amount:.2f}"

    # Description line
    if tx.kind == "subscription":
        description = f"{(tx.subscription_tier or '').capitalize()} plan — {tx.subscription_billing_cycle or 'monthly'} billing"
    else:
        credits_total = tx.credits_total_granted or tx.credits_purchased or 0
        description = f"Credit top-up — {credits_total:,} credits"
        if tx.credits_bonus:
            description += f" (incl. {tx.credits_bonus:,} bonus)"

    invoice_number = f"INV-{tx.id:06d}"
    issued_date = tx.authorized_at or tx.created_at
    issued_str = issued_date.strftime("%d %B %Y") if issued_date else "—"

    # Minimal inline-styled invoice HTML suitable for print/PDF
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice {invoice_number}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; color: #1e293b; background: #fff; padding: 40px; max-width: 720px; margin: 0 auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 40px; padding-bottom: 24px; border-bottom: 2px solid #e2e8f0; }}
  .brand {{ font-size: 22px; font-weight: 700; color: #0f172a; letter-spacing: -0.5px; }}
  .brand-sub {{ font-size: 12px; color: #64748b; margin-top: 2px; }}
  .invoice-meta {{ text-align: right; }}
  .invoice-meta .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; }}
  .invoice-meta .value {{ font-size: 18px; font-weight: 700; color: #0f172a; }}
  .invoice-meta .date {{ font-size: 13px; color: #475569; margin-top: 4px; }}
  .section {{ margin-bottom: 28px; }}
  .section-title {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; font-weight: 600; margin-bottom: 8px; }}
  .addr {{ line-height: 1.7; color: #334155; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
  thead tr {{ background: #f8fafc; }}
  th {{ text-align: left; padding: 10px 14px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; font-weight: 600; border-bottom: 1px solid #e2e8f0; }}
  td {{ padding: 12px 14px; border-bottom: 1px solid #f1f5f9; color: #334155; }}
  .amount-col {{ text-align: right; }}
  .totals {{ margin-left: auto; width: 280px; }}
  .total-row {{ display: flex; justify-content: space-between; padding: 6px 0; font-size: 13px; color: #475569; }}
  .total-row.main {{ font-size: 15px; font-weight: 700; color: #0f172a; border-top: 2px solid #e2e8f0; padding-top: 10px; margin-top: 4px; }}
  .refund-note {{ color: #d97706; font-size: 12px; }}
  .footer {{ margin-top: 48px; padding-top: 20px; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8; line-height: 1.6; }}
  .status-badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
  .status-captured {{ background: #d1fae5; color: #065f46; }}
  .status-refunded {{ background: #fef3c7; color: #92400e; }}
  @media print {{
    body {{ padding: 20px; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <div class="brand">HELVEX by Balogh Consulting</div>
      <div class="brand-sub">helvex.balogh-consulting.ch</div>
      <div class="label">MWST: CHE-457.771.278</div>
      <div class="label">Address: Balogh Consulting, Dorfstrasse 43, 3073 Gümligen</div>
    </div>
    <div class="invoice-meta">
      <div class="label">Invoice</div>
      <div class="value">{invoice_number}</div>
      <div class="date">Issued {issued_str}</div>
    </div>
  </div>

  <div style="display:flex; gap:60px; margin-bottom:36px;">
    <div class="section" style="flex:1;">
      <div class="section-title">Billed to</div>
      <div class="addr">{billing_addr_str if billing_addr_str else f'<span style="color:#94a3b8">Organization: {org.name}</span>'}</div>
    </div>
    <div class="section" style="flex:1;">
      <div class="section-title">Payment details</div>
      <div style="line-height:1.8; color:#334155;">
        <div><strong>Method:</strong> {(tx.payment_method or '—').replace('_', ' ').title()}</div>
        {"<div><strong>Cardholder:</strong> " + tx.cardholder_name + "</div>" if tx.cardholder_name else ""}
        <div><strong>Provider ref:</strong> <span style="font-family:monospace;font-size:12px;">{tx.provider_transaction_id or tx.external_id or '—'}</span></div>
        <div><strong>Status:</strong> <span class="status-badge {'status-refunded' if tx.refunded_at else 'status-captured'}">{('Refunded' if tx.refunded_at else 'Paid')}</span></div>
      </div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Description</th>
        <th>Type</th>
        <th class="amount-col">Amount</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>{description}</td>
        <td style="text-transform:capitalize;">{tx.kind}</td>
        <td class="amount-col" style="font-weight:600;">{amount_str}</td>
      </tr>
    </tbody>
  </table>

  <div class="totals">
    <div class="total-row">
      <span>Subtotal</span>
      <span>{amount_str}</span>
    </div>
    {f'<div class="total-row refund-note"><span>Refund issued</span><span>−{refund_str}</span></div>' if refund_str else ''}
    <div class="total-row main">
      <span>Total paid</span>
      <span>{net_amount_str}</span>
    </div>
  </div>

  <div class="footer">
    <p>Transaction ID: {tx.id} &nbsp;·&nbsp; Order ref: {tx.order_reference or '—'} &nbsp;·&nbsp; Provider: {tx.provider.title()}</p>
    <p style="margin-top:6px;">This document serves as your receipt. For support, contact support@firmiq.io.</p>
    {f'<p class="refund-note" style="margin-top:6px;">Refund of {refund_str} issued {tx.refunded_at.strftime("%d %B %Y") if tx.refunded_at else "—"}. Reason: {tx.refund_reason or "—"}</p>' if tx.refunded_at else ''}
  </div>
</body>
</html>"""

    return HTMLResponse(content=html, status_code=200)
