"""Read-only billing info routes: tiers, summary, credits, payments, payment methods."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_org
from app.auth import get_current_user
from app.database import get_db
from app.models.org_credit_transaction import OrgCreditTransaction
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.services import payment_transactions, payments
from app.services.tiers import get_billing_tiers

from app.schemas.billing import BillingTierRead
from app.api.routes.billing._shared import (
    _cancel_provider_transaction,
    _resolve_worldline_payment_alias,
)

router = APIRouter()


@router.get("/providers")
def list_enabled_providers(_: User = Depends(get_current_user)) -> dict:
    return {"mode": payments.settings.payment_provider_mode, "enabled": payments.get_enabled_provider_order()}


@router.get("/payment-methods")
def list_payment_methods(
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    """Return org-scoped and caller's personal payment methods."""
    from app.models.org_payment_method import OrgPaymentMethod
    user, org = user_org
    items = []

    org_methods = (
        db.query(OrgPaymentMethod)
        .filter(OrgPaymentMethod.org_id == org.id)
        .order_by(OrgPaymentMethod.created_at.asc())
        .all()
    )
    for m in org_methods:
        card_info: dict = {}
        if m.card_info_json:
            try:
                card_info = json.loads(m.card_info_json)
            except (ValueError, TypeError):
                pass
        items.append({
            "id": f"org:{m.alias_id}",
            "scope": "org",
            "provider": "worldline",
            "alias_id": m.alias_id,
            "masked_number": card_info.get("masked_number") or None,
            "brand": card_info.get("brand") or None,
            "holder_name": card_info.get("holder_name") or None,
            "exp_year": card_info.get("exp_year"),
            "exp_month": card_info.get("exp_month"),
            "is_default": bool(m.is_default),
            "label": m.label,
        })

    if user.payment_customer_id:
        card_info = {}
        if user.payment_card_info_json:
            try:
                card_info = json.loads(user.payment_card_info_json)
            except (ValueError, TypeError):
                pass
        items.append({
            "id": f"personal:{user.payment_customer_id}",
            "scope": "personal",
            "provider": "worldline",
            "alias_id": user.payment_customer_id,
            "masked_number": card_info.get("masked_number") or None,
            "brand": card_info.get("brand") or None,
            "holder_name": card_info.get("holder_name") or None,
            "exp_year": card_info.get("exp_year"),
            "exp_month": card_info.get("exp_month"),
            "is_default": False,
            "label": None,
        })

    return {"items": items}


@router.delete("/payment-methods/{alias_id}")
def delete_payment_method(
    alias_id: str,
    scope: str = Query("org"),
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    from app.models.org_payment_method import OrgPaymentMethod
    from fastapi import HTTPException, status
    user, org = user_org

    if scope == "personal":
        if user.payment_customer_id != alias_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment method not found")
        user.payment_customer_id = None
        user.payment_card_info_json = None
        db.commit()
        return {"ok": True}

    if user.org_role not in ("admin", "owner"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    method = db.query(OrgPaymentMethod).filter(
        OrgPaymentMethod.org_id == org.id,
        OrgPaymentMethod.alias_id == alias_id,
    ).first()
    if not method:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment method not found")
    was_default = method.is_default
    db.delete(method)
    db.flush()
    if was_default:
        next_method = db.query(OrgPaymentMethod).filter(OrgPaymentMethod.org_id == org.id).order_by(OrgPaymentMethod.created_at.asc()).first()
        if next_method:
            next_method.is_default = True
    db.commit()
    return {"ok": True}


@router.put("/payment-methods/{alias_id}/set-default")
def set_payment_method_default(
    alias_id: str,
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    from app.models.org_payment_method import OrgPaymentMethod
    from fastapi import HTTPException, status
    user, org = user_org
    if user.org_role not in ("admin", "owner"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    db.query(OrgPaymentMethod).filter(OrgPaymentMethod.org_id == org.id).update({"is_default": False})
    method = db.query(OrgPaymentMethod).filter(
        OrgPaymentMethod.org_id == org.id,
        OrgPaymentMethod.alias_id == alias_id,
    ).first()
    if not method:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org payment method not found")
    method.is_default = True
    db.commit()
    return {"ok": True}


@router.post("/payment-methods/add-personal-to-org")
def add_personal_card_to_org(
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    """Copy the caller's personal saved card into the org payment methods."""
    from app.models.org_payment_method import OrgPaymentMethod
    from fastapi import HTTPException, status
    user, org = user_org
    if user.org_role not in ("admin", "owner"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    if not user.payment_customer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No personal payment method saved")
    existing = db.query(OrgPaymentMethod).filter(
        OrgPaymentMethod.org_id == org.id,
        OrgPaymentMethod.alias_id == user.payment_customer_id,
    ).first()
    if existing:
        return {"ok": True}
    is_default = db.query(OrgPaymentMethod).filter(OrgPaymentMethod.org_id == org.id).count() == 0
    method = OrgPaymentMethod(
        org_id=org.id,
        alias_id=user.payment_customer_id,
        card_info_json=user.payment_card_info_json,
        is_default=is_default,
        added_by_user_id=user.id,
    )
    db.add(method)
    db.commit()
    return {"ok": True}


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


@router.get("/summary")
def get_billing_summary(
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    """Return billing summary for the current org: tier, balance, subscription info."""
    user, org = user_org
    payment_transactions.expire_stale_pending_transactions(
        db, org_id=org.id, max_age_minutes=15, on_expire=_cancel_provider_transaction,
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
    """Return paginated credit ledger for the current org."""
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
                "type": tx.type,
                "amount": tx.amount,
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
    """Return per-action credit consumption for the org over the last N days."""
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
    """Return paginated payment transaction history for the current org."""
    _user, org = user_org
    payment_transactions.expire_stale_pending_transactions(
        db, org_id=org.id, max_age_minutes=15, on_expire=_cancel_provider_transaction,
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
                "vat_rate": tx.vat_rate,
                "vat_amount_chf": float(tx.vat_amount_chf) if tx.vat_amount_chf is not None else None,
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
