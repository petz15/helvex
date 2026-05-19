"""Checkout routes: subscription, top-up, and card registration."""

from __future__ import annotations

import json
from urllib.parse import urlencode, parse_qs, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_org, require_org_role
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import payment_transactions, payments

from app.api.routes.billing._shared import (
    CardRegistrationRequest,
    CheckoutResponse,
    PaymentMethodRegistrationResponse,
    SubscriptionCheckoutRequest,
    TopupCheckoutRequest,
    _resolve_billing_address,
    _resolve_tier_amount_chf,
    _resolve_worldline_payment_alias,
    _start_worldline_alias_polling,
    logger,
)
from app.services.payments.pricing import apply_vat

router = APIRouter()


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

    # VAT
    sub_billing_country = billing_address.get("country", "") or ""
    sub_vat_rate, sub_vat_amount_chf, sub_total_chf = apply_vat(amount_chf, sub_billing_country, getattr(org, "vat_id", None))
    logger.debug("billing.subscription_checkout vat country=%s vat_rate=%s total=%s", sub_billing_country, sub_vat_rate, sub_total_chf)

    try:
        logger.info(
            "billing.subscription_checkout calling_provider provider=%s org_id=%s",
            body.provider or "default", org.id,
        )
        _sub_alias = _resolve_worldline_payment_alias(db, org, _user, body.selected_alias_id) if body.provider in {None, "worldline"} else None
        session = payments.create_subscription_checkout(
            org_id=org.id,
            user_id=_user.id,
            payment_alias_id=_sub_alias,
            save_payment_method=body.save_payment_method,
            tier=body.tier,
            billing_cycle=body.billing_cycle,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            billing_address=billing_address,
            preferred_provider=body.provider,
            amount_chf=sub_total_chf,
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
                    amount_chf=sub_total_chf,
                    kind="subscription",
                    status="pending",
                    subscription_tier=body.tier,
                    subscription_billing_cycle=body.billing_cycle,
                    upgrade_proration_credits=body.upgrade_proration_credits,
                    billing_address=json.dumps(billing_address),
                    vat_rate=sub_vat_rate,
                    vat_amount_chf=sub_vat_amount_chf,
                )
            except payment_transactions.DuplicatePaymentError:
                logger.warning(
                    "billing.subscription_checkout_pending_duplicate org_id=%s token=%s",
                    org.id, session.external_id[:20],
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
        amount_chf=sub_total_chf,
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

    # CHF 1,000 per-transaction limit
    if amount_chf > 1000.00:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum top-up is CHF 1,000.00 per transaction.")

    # CHF 1,000 max account balance guard (10,000,000 credits)
    if org.credits_balance + body.credits > 10_000_000:
        headroom = max(0, 10_000_000 - org.credits_balance)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Top-up would exceed the maximum account balance of CHF 1,000.00. You can add at most {headroom:,} credits.",
        )

    # VAT
    billing_country = billing_address.get("country", "") or ""
    vat_rate, vat_amount_chf, total_chf = apply_vat(amount_chf, billing_country, getattr(org, "vat_id", None))
    logger.debug("billing.topup_checkout vat country=%s vat_rate=%s vat_amount=%s total=%s", billing_country, vat_rate, vat_amount_chf, total_chf)

    try:
        logger.info(
            "billing.topup_checkout calling_provider provider=%s org_id=%s",
            body.provider or "default", org.id,
        )
        _topup_alias = None
        if body.provider in {None, "worldline"} and not body.use_new_card:
            _topup_alias = _resolve_worldline_payment_alias(db, org, _user, body.selected_alias_id)
        session = payments.create_topup_checkout(
            org_id=org.id,
            user_id=_user.id,
            payment_alias_id=_topup_alias,
            save_payment_method=body.save_payment_method,
            credits=body.credits,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            billing_address=billing_address,
            preferred_provider=body.provider,
            amount_chf=total_chf,
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
                    amount_chf=total_chf,
                    kind="topup",
                    status="pending",
                    credits_purchased=body.credits,
                    billing_address=json.dumps(billing_address),
                    vat_rate=vat_rate,
                    vat_amount_chf=vat_amount_chf,
                )
            except payment_transactions.DuplicatePaymentError:
                logger.warning(
                    "billing.topup_checkout_pending_duplicate org_id=%s token=%s",
                    org.id, session.external_id[:20],
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
        amount_chf=total_chf,
    )


@router.post("/payment-methods/worldline/register", response_model=PaymentMethodRegistrationResponse)
def create_worldline_card_registration(
    body: CardRegistrationRequest,
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> PaymentMethodRegistrationResponse:
    _user, org = user_org
    if body.scope == "org" and _user.org_role not in ("admin", "owner"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required to add org cards")
    billing_address = _resolve_billing_address(_user, body.billing_address, db)

    def _inject_scope(url: str) -> str:
        p = urlparse(url)
        qs = parse_qs(p.query, keep_blank_values=True)
        qs["card_scope"] = [body.scope]
        return urlunparse(p._replace(query=urlencode(qs, doseq=True)))

    success_url = _inject_scope(body.success_url)
    cancel_url = _inject_scope(body.cancel_url)

    try:
        session = payments.WorldlineProvider().create_card_alias_registration(
            org_id=org.id,
            user_id=_user.id,
            success_url=success_url,
            cancel_url=cancel_url,
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
            scope=body.scope,
        )

    return PaymentMethodRegistrationResponse(
        provider=session.provider,
        checkout_url=session.checkout_url,
        external_id=session.external_id,
    )
