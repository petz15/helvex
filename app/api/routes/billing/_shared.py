"""Shared imports, schemas, and helper functions for the billing routes package."""

from __future__ import annotations

import json
import logging
import threading
from typing import Literal
from urllib.parse import urlencode, urlparse

from fastapi import HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.schemas.billing import BillingAddress
from app.services.billing.billing_addresses import get_default_billing_address
from app.services.billing import payments
from app.services.billing.tiers import (
    get_tier_price_chf,
)

logger = logging.getLogger(__name__)


# ── Request / Response schemas ─────────────────────────────────────────────────

class SubscriptionCheckoutRequest(BaseModel):
    tier: str
    billing_cycle: Literal["monthly", "yearly"] = "monthly"
    success_url: str
    cancel_url: str
    billing_address: BillingAddress | None = None
    save_payment_method: bool = False
    provider: Literal["worldline"] | None = None
    upgrade_proration_credits: int | None = None
    selected_alias_id: str | None = None


class TopupCheckoutRequest(BaseModel):
    credits: int = Field(..., ge=10_000, le=10_000_000)
    success_url: str
    cancel_url: str
    billing_address: BillingAddress | None = None
    save_payment_method: bool = True
    use_new_card: bool = False
    provider: Literal["worldline"] | None = None
    selected_alias_id: str | None = None


class CardRegistrationRequest(BaseModel):
    success_url: str
    cancel_url: str
    billing_address: BillingAddress | None = None
    scope: Literal["org", "personal"] = "personal"


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


class ScheduleDowngradeRequest(BaseModel):
    tier: str


class UpgradeProrationResponse(BaseModel):
    credits_granted: int
    credits_chf: float
    remaining_days: int
    plan_cost_chf: float


# ── Shared helper functions ────────────────────────────────────────────────────

def _emit(level: str, message: str, *args: object) -> None:
    log_fn = getattr(logger, level, logger.info)
    log_fn(message, *args)


def _extract_card_info_from_worldline(result: dict) -> dict:
    """Pull masked card details from a Worldline Authorize or AliasInsert response."""
    pm = result.get("PaymentMeans") if isinstance(result, dict) else {}
    card = pm.get("Card") if isinstance(pm, dict) else {}
    if not isinstance(card, dict):
        card = {}
    return {
        "masked_number": str(card.get("MaskedNumber") or ""),
        "brand": str(card.get("Brand") or pm.get("Brand") if isinstance(pm, dict) else ""),
        "holder_name": str(card.get("HolderName") or ""),
        "exp_year": card.get("ExpYear"),
        "exp_month": card.get("ExpMonth"),
    }


def _save_alias(
    db: Session, *, user: User, alias_id: str, card_info: dict, org: Organization | None, scope: str = "personal"
) -> None:
    """Persist alias + card info. scope='org' saves to org_payment_methods, 'personal' to user."""
    from app.models.org_payment_method import OrgPaymentMethod
    if scope == "org" and org is not None:
        existing = db.query(OrgPaymentMethod).filter(
            OrgPaymentMethod.org_id == org.id,
            OrgPaymentMethod.alias_id == alias_id,
        ).first()
        has_default = db.query(OrgPaymentMethod).filter(
            OrgPaymentMethod.org_id == org.id,
            OrgPaymentMethod.is_default == True,  # noqa: E712
        ).first() is not None
        if existing:
            existing.card_info_json = json.dumps(card_info)
        else:
            db.add(OrgPaymentMethod(
                org_id=org.id,
                alias_id=alias_id,
                card_info_json=json.dumps(card_info),
                is_default=not has_default,
                added_by_user_id=user.id,
            ))
    else:
        user.payment_customer_id = alias_id
        user.payment_card_info_json = json.dumps(card_info)
        if org is not None and not getattr(org, "default_payment_user_id", None):
            org.default_payment_user_id = user.id


def _resolve_worldline_payment_alias(
    db: Session, org: Organization, current_user: User | None = None, selected_alias_id: str | None = None
) -> str | None:
    from app.models.org_payment_method import OrgPaymentMethod
    if selected_alias_id:
        return selected_alias_id.strip() or None
    default_org = db.query(OrgPaymentMethod).filter(
        OrgPaymentMethod.org_id == org.id,
        OrgPaymentMethod.is_default == True,  # noqa: E712
    ).first()
    if default_org:
        return default_org.alias_id
    if current_user is not None:
        alias = str(current_user.payment_customer_id or "").strip()
        if alias:
            return alias
    owner_id = getattr(org, "default_payment_user_id", None)
    if not owner_id:
        return None
    owner = db.get(User, int(owner_id))
    if owner is None:
        return None
    return str(owner.payment_customer_id or "").strip() or None


def _cancel_provider_transaction(tx: PaymentTransaction) -> None:
    """Best-effort provider-side cancellation for pending transactions."""
    if tx.provider != "worldline":
        return
    provider_tx_id = str(tx.provider_transaction_id or "").strip()
    if not provider_tx_id:
        _emit(
            "info",
            "billing.provider_cancel_skip tx_id=%s provider=%s reason=missing_provider_transaction_id",
            tx.id, tx.provider,
        )
        return
    try:
        payments.WorldlineProvider().cancel_transaction(transaction_id=provider_tx_id)
        _emit(
            "info",
            "billing.provider_cancel_ok tx_id=%s provider=%s provider_tx_id=%s",
            tx.id, tx.provider, provider_tx_id[:30],
        )
    except (payments.PaymentConfigurationError, RuntimeError) as exc:
        _emit(
            "warning",
            "billing.provider_cancel_failed tx_id=%s provider=%s provider_tx_id=%s error=%s",
            tx.id, tx.provider, provider_tx_id[:30], str(exc),
        )


def _start_worldline_alias_polling(
    *, org_id: int, user_id: int, order_reference: str, token: str, scope: str = "personal"
) -> None:
    worker = threading.Thread(
        target=_poll_worldline_alias_registration,
        kwargs={"org_id": org_id, "user_id": user_id, "order_reference": order_reference, "token": token, "scope": scope},
        daemon=True,
        name=f"worldline-alias-poll-{org_id}-{user_id}",
    )
    worker.start()


def _poll_worldline_alias_registration(
    *, org_id: int, user_id: int, order_reference: str, token: str, scope: str = "personal"
) -> None:
    import time as _time
    _emit(
        "info",
        "billing.worldline_alias_poll_start org_id=%s user_id=%s order_ref=%s token=%s",
        org_id, user_id, order_reference[:50], token[:20],
    )
    _time.sleep(30)
    try:
        result = payments.WorldlineProvider().wait_for_alias_registration(
            token=token, max_attempts=15, poll_interval_seconds=60,
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
            poll_org = db.get(Organization, org_id)
            _save_alias(db, user=owner, alias_id=alias_id, card_info=_extract_card_info_from_worldline(result), org=poll_org, scope=scope)
            db.commit()
        finally:
            db.close()

        payments.clear_pending_card_alias_token(order_reference=order_reference)
        _emit(
            "info",
            "billing.worldline_alias_poll_saved org_id=%s user_id=%s alias_id=%s",
            org_id, user_id, alias_id,
        )
    except (payments.PaymentConfigurationError, RuntimeError) as exc:
        _emit(
            "warning",
            "billing.worldline_alias_poll_failed org_id=%s user_id=%s order_ref=%s error=%s",
            org_id, user_id, order_reference[:50], str(exc),
        )


def _safe_redirect_target(url: str | None) -> str:
    base = payments.settings.app_base_url.rstrip("/")
    if not url:
        return base
    parsed = urlparse(url)
    allowed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return base
    if parsed.netloc != allowed.netloc:
        logger.warning("billing.redirect_blocked_open_redirect url=%s allowed=%s", url[:80], allowed.netloc)
        return base
    return url


def _append_query_params(url: str, params: dict[str, str]) -> str:
    if not params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _iframe_redirect(url: str) -> HTMLResponse:
    """Return an HTML page that navigates both iframe and top-level frames to url."""
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
        return payments.compute_subscription_price_chf(
            tier=tier,
            billing_cycle=billing_cycle,  # type: ignore[arg-type]
            custom_features=custom_features,
            verified_business=verified_business,
        )
    base = get_tier_price_chf(db, tier)
    amount = base * (10.0 if billing_cycle == "yearly" else 1.0)
    if verified_business:
        amount *= 0.80
    return round(amount, 2)
