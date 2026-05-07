"""Superadmin-only routes for platform management."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.org_credit_transaction import OrgCreditTransaction
from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.schemas.billing import BillingTierRead
from app.services import credits as credits_service
from app.services import payments as payments_service
from app.services.tiers import TIER_ID_BY_NAME, get_billing_tier_names, get_billing_tiers
from .api_keys import router as api_keys_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(api_keys_router)

def _require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin access required")
    return current_user


# ── Schemas ────────────────────────────────────────────────────────────────────

class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    is_superadmin: bool | None = None


class AdminOrgUpdate(BaseModel):
    name: str | None = None
    tier: str | None = None


class AdminCreditAdjustment(BaseModel):
    amount: int = Field(..., ge=1, description="Number of credits (always positive; type determines direction)")
    adjustment_type: Literal["grant", "deduct"]
    reason: str = Field(..., min_length=1, max_length=500)


class AdminRefundRequest(BaseModel):
    amount_chf: float = Field(..., gt=0)
    reason: str = Field(..., min_length=1, max_length=500)


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/analytics", summary="Platform analytics dashboard (superadmin)")
def get_analytics(
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
) -> dict:
    """Aggregate metrics for the superadmin analytics dashboard.

    Returns org counts, MRR estimate, top credit consumers (30d),
    job volume by type (30d), total companies in DB, and active org count.
    All monetary values are in CHF.
    """
    from datetime import timedelta, timezone as _tz
    from app.models.job_run import JobRun
    from app.models.company import Company
    from app.models.org_credit_transaction import OrgCreditTransaction as OrgCreditTx
    from app.services.tiers import TIER_NAME_BY_ID

    cutoff = datetime.now(tz=_tz.utc) - timedelta(days=30)

    # Basic org counts
    total_orgs: int = db.query(func.count(Organization.id)).scalar() or 0
    new_orgs_30d: int = (
        db.query(func.count(Organization.id))
        .filter(Organization.created_at >= cutoff)
        .scalar() or 0
    )

    # Orgs by tier name
    tier_rows = db.query(Organization.tier_id, func.count(Organization.id)).group_by(Organization.tier_id).all()
    orgs_by_tier = {TIER_NAME_BY_ID.get(tid, str(tid)): int(cnt) for tid, cnt in tier_rows}

    # MRR estimate: sum(monthly_price_chf) for all active paid-tier orgs
    try:
        from app.models.billing_tier import BillingTier
        mrr_rows = (
            db.query(BillingTier.monthly_price_chf, func.count(Organization.id))
            .join(Organization, Organization.tier_id == BillingTier.id)
            .filter(BillingTier.monthly_price_chf > 0)
            .group_by(BillingTier.monthly_price_chf)
            .all()
        )
        mrr_estimate_chf = float(sum(float(p) * int(c) for p, c in mrr_rows))
    except Exception:  # noqa: BLE001
        mrr_estimate_chf = 0.0

    # Top 10 credit consumers (net spend = deductions - refunds, last 30d)
    top_rows = (
        db.query(
            OrgCreditTx.org_id,
            Organization.name,
            func.sum(-OrgCreditTx.amount).label("net_spend"),
        )
        .join(Organization, Organization.id == OrgCreditTx.org_id, isouter=True)
        .filter(OrgCreditTx.created_at >= cutoff, OrgCreditTx.amount < 0)
        .group_by(OrgCreditTx.org_id, Organization.name)
        .order_by(func.sum(-OrgCreditTx.amount).desc())
        .limit(10)
        .all()
    )
    top_credit_consumers = []
    for org_id, org_name, net_spend in top_rows:
        net = int(net_spend or 0)
        top_credit_consumers.append({
            "org_id": org_id,
            "org_name": org_name if org_name else f"org:{org_id}",
            "net_spend_credits": net,
            "net_spend_chf": round(net * 0.0001, 4),
        })

    # Job volume by type (last 30d)
    job_rows = (
        db.query(JobRun.job_type, func.count(JobRun.id))
        .filter(JobRun.queued_at >= cutoff)
        .group_by(JobRun.job_type)
        .all()
    )
    job_volume_by_type = {jt: int(cnt) for jt, cnt in job_rows}

    # Total companies in DB
    total_companies_db: int = db.query(func.count(Company.id)).scalar() or 0

    # Active orgs: had at least one job in last 30d
    active_orgs_30d: int = (
        db.query(func.count(func.distinct(JobRun.org_id)))
        .filter(JobRun.queued_at >= cutoff, JobRun.org_id.isnot(None))
        .scalar() or 0
    )

    return {
        "total_orgs": total_orgs,
        "new_orgs_30d": new_orgs_30d,
        "orgs_by_tier": orgs_by_tier,
        "mrr_estimate_chf": mrr_estimate_chf,
        "top_credit_consumers": top_credit_consumers,
        "job_volume_by_type": job_volume_by_type,
        "total_companies_db": total_companies_db,
        "active_orgs_30d": active_orgs_30d,
    }


@router.post("/jobs/saved-view-alerts", summary="Manually trigger saved-view alert check (superadmin)")
def trigger_saved_view_alerts(
    db: Session = Depends(get_db),
    actor: User = Depends(_require_superadmin),
) -> dict:
    """Enqueue a one-off saved_view_alerts job. The nightly cron calls this automatically."""
    from app.services.job_worker import enqueue_job
    job = enqueue_job(
        db,
        job_type="saved_view_alerts",
        label="Saved view alerts",
        params={},
        org_id=None,
        user_id=actor.id,
    )
    return {"job_id": job.id, "status": job.status}


# ── Stats ──────────────────────────────────────────────────────────────────────

@router.get("/stats", summary="Platform-wide stats (superadmin)")
def get_stats(
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0
    verified_users = db.query(func.count(User.id)).filter(User.email_verified.is_(True)).scalar() or 0
    total_orgs = db.query(func.count(Organization.id)).scalar() or 0
    users_in_org = db.query(func.count(User.id)).filter(User.org_id.isnot(None)).scalar() or 0
    return {
        "total_users": total_users,
        "active_users": active_users,
        "verified_users": verified_users,
        "total_orgs": total_orgs,
        "users_in_org": users_in_org,
    }


# ── Users ──────────────────────────────────────────────────────────────────────

def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "is_active": u.is_active,
        "email_verified": u.email_verified,
        "is_superadmin": u.is_superadmin,
        "org_id": u.org_id,
        "org_name": u.org.name if u.org else None,
        "org_role": u.org_role,
        "created_at": u.created_at.isoformat(),
    }


@router.get("/users", summary="List all users (superadmin)")
def list_users(
    q: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    query = db.query(User)
    if q:
        query = query.filter(User.email.ilike(f"%{q}%"))
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    total = query.count()
    users = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"items": [_user_dict(u) for u in users], "total": total, "page": page, "page_size": page_size}


@router.patch("/users/{user_id}", summary="Update user status (superadmin)")
def update_user(
    user_id: int,
    body: AdminUserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(_require_superadmin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_superadmin is not None:
        if user.id == actor.id and not body.is_superadmin:
            raise HTTPException(status_code=400, detail="Cannot revoke your own superadmin")
        user.is_superadmin = body.is_superadmin
    db.commit()
    db.refresh(user)
    return _user_dict(user)


# ── Orgs ──────────────────────────────────────────────────────────────────────

def _org_dict(org: Organization, member_count: int) -> dict:
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "tier": org.tier,
        "member_count": member_count,
        "credits_balance": int(org.credits_balance or 0),
        "credits_unlimited": bool(org.credits_unlimited),
        "created_at": org.created_at.isoformat(),
    }


@router.get("/orgs", summary="List all orgs (superadmin)")
def list_orgs(
    q: str | None = Query(None),
    tier: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    query = db.query(Organization)
    if q:
        query = query.filter(Organization.name.ilike(f"%{q}%"))
    if tier:
        tier_id = TIER_ID_BY_NAME.get(tier, -1)
        query = query.filter(Organization.tier_id == tier_id)
    total = query.count()
    orgs = (
        query.order_by(Organization.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    result = []
    for org in orgs:
        mc = db.query(func.count(User.id)).filter(User.org_id == org.id).scalar() or 0
        result.append(_org_dict(org, mc))
    return {"items": result, "total": total, "page": page, "page_size": page_size}


@router.patch("/orgs/{org_id}", summary="Update org name/tier (superadmin)")
def update_org(
    org_id: int,
    body: AdminOrgUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    valid_tiers = set(get_billing_tier_names(db))
    if body.name is not None:
        org.name = body.name.strip()
    if body.tier is not None:
        if body.tier not in valid_tiers:
            raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {sorted(valid_tiers)}")
        org.tier = body.tier
    db.commit()
    db.refresh(org)
    mc = db.query(func.count(User.id)).filter(User.org_id == org.id).scalar() or 0
    return _org_dict(org, mc)


@router.delete("/orgs/{org_id}", status_code=204, summary="Delete org and kick all members (superadmin)")
def delete_org_admin(
    org_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    db.query(User).filter(User.org_id == org_id).update(
        {"org_id": None, "org_role": "member"}, synchronize_session=False
    )
    db.delete(org)
    db.commit()


# ── Payment Transactions ────────────────────────────────────────────────────────


def _payment_tx_dict(tx: PaymentTransaction) -> dict:
    return {
        "id": tx.id,
        "org_id": tx.org_id,
        "provider": tx.provider,
        "external_id": tx.external_id,
        "order_reference": tx.order_reference,
        "amount_chf": tx.amount_chf,
        "kind": tx.kind,
        "status": tx.status,
        "payment_method": tx.payment_method,
        "provider_transaction_id": tx.provider_transaction_id,
        "subscription_tier": tx.subscription_tier,
        "subscription_billing_cycle": tx.subscription_billing_cycle,
        "credits_purchased": tx.credits_purchased,
        "credits_bonus": tx.credits_bonus,
        "credits_total_granted": tx.credits_total_granted,
        "error_code": tx.error_code,
        "error_message": tx.error_message,
        "refunded_amount_chf": tx.refunded_amount_chf,
        "webhook_processed_at": tx.webhook_processed_at.isoformat() if tx.webhook_processed_at else None,
        "created_at": tx.created_at.isoformat(),
        "authorized_at": tx.authorized_at.isoformat() if tx.authorized_at else None,
        "refunded_at": tx.refunded_at.isoformat() if tx.refunded_at else None,
    }


@router.get("/payment-transactions", summary="List payment transactions (superadmin)")
def list_payment_transactions(
    org_id: int | None = Query(None),
    provider: str | None = Query(None),
    status: str | None = Query(None),
    kind: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    query = db.query(PaymentTransaction)

    if org_id is not None:
        query = query.filter(PaymentTransaction.org_id == org_id)
    if provider:
        query = query.filter(PaymentTransaction.provider == provider.lower())
    if status:
        query = query.filter(PaymentTransaction.status == status.lower())
    if kind:
        query = query.filter(PaymentTransaction.kind == kind.lower())

    total = query.count()
    transactions = (
        query.order_by(PaymentTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [_payment_tx_dict(tx) for tx in transactions],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/payment-transactions/{tx_id}", summary="Get payment transaction details (superadmin)")
def get_payment_transaction(
    tx_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    tx = db.get(PaymentTransaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Payment transaction not found")
    return _payment_tx_dict(tx)


@router.get("/tiers", response_model=list[BillingTierRead], summary="List billing tiers (superadmin)")
def list_billing_tiers_admin(
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
) -> list[BillingTierRead]:
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


@router.post("/orgs/{org_id}/credits", summary="Grant or deduct credits for an org (superadmin)")
def adjust_org_credits(
    org_id: int,
    body: AdminCreditAdjustment,
    db: Session = Depends(get_db),
    actor: User = Depends(_require_superadmin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    if body.adjustment_type == "grant":
        credits_service.grant_credits(
            db,
            org_id=org_id,
            amount=body.amount,
            tx_type="grant",
            action_type="admin_grant",
            reference_id=f"admin:{actor.id}",
        )
        logger.info(
            "admin.credit_grant actor=%s org=%s amount=%s reason=%s",
            actor.id, org_id, body.amount, body.reason,
        )
    else:
        org_fresh = db.query(Organization).filter(Organization.id == org_id).with_for_update().first()
        if not org_fresh:
            raise HTTPException(status_code=404, detail="Org not found")
        if not org_fresh.credits_unlimited:
            balance = int(org_fresh.credits_balance or 0)
            if balance < body.amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient balance: org has {balance} credits, requested deduction is {body.amount}",
                )
        before = int(org_fresh.credits_balance or 0)
        after = before - body.amount if not org_fresh.credits_unlimited else before
        if not org_fresh.credits_unlimited:
            org_fresh.credits_balance = after
        tx = OrgCreditTransaction(
            org_id=org_id,
            amount=-body.amount,
            type="deduction",
            action_type="admin_deduct",
            reference_id=f"admin:{actor.id}",
            credits_before=before,
            credits_after=after,
        )
        db.add(tx)
        db.commit()
        logger.info(
            "admin.credit_deduct actor=%s org=%s amount=%s reason=%s before=%s after=%s",
            actor.id, org_id, body.amount, body.reason, before, after,
        )

    db.refresh(org)
    mc = db.query(func.count(User.id)).filter(User.org_id == org_id).scalar() or 0
    return {
        **_org_dict(org, mc),
        "credits_balance": int(org.credits_balance or 0),
    }


@router.post(
    "/payment-transactions/{tx_id}/refund",
    summary="Refund a captured payment transaction via Saferpay referenced refund (superadmin)",
)
def refund_payment_transaction(
    tx_id: int,
    body: AdminRefundRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(_require_superadmin),
):
    tx = db.get(PaymentTransaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Payment transaction not found")

    if tx.provider != "worldline":
        raise HTTPException(status_code=400, detail="Only Worldline transactions can be refunded via this endpoint")

    if tx.status not in {"captured", "authorized"}:
        raise HTTPException(
            status_code=400,
            detail=f"Only captured/authorized transactions can be refunded (current status: {tx.status})",
        )

    if tx.refunded_at is not None:
        raise HTTPException(status_code=400, detail="Transaction has already been refunded")

    if not tx.provider_transaction_id:
        raise HTTPException(status_code=400, detail="Transaction has no provider_transaction_id — cannot issue referenced refund")

    if body.amount_chf > tx.amount_chf:
        raise HTTPException(
            status_code=400,
            detail=f"Refund amount CHF {body.amount_chf:.2f} exceeds transaction amount CHF {tx.amount_chf:.2f}",
        )

    refund_order_id = f"refund_{tx.order_reference or tx.id}_{secrets.token_hex(4)}"

    try:
        result = payments_service.WorldlineProvider().refund_transaction(
            transaction_id=tx.provider_transaction_id,
            amount_chf=body.amount_chf,
            order_id=refund_order_id,
            description=f"Refund: {body.reason}",
        )
    except (payments_service.PaymentConfigurationError, RuntimeError) as exc:
        logger.error("admin.refund_failed tx_id=%s actor=%s error=%s", tx_id, actor.id, exc)
        raise HTTPException(status_code=502, detail=f"Saferpay refund failed: {exc}") from exc

    tx.refunded_amount_chf = body.amount_chf
    tx.refund_reason = body.reason
    tx.refunded_at = datetime.now(tz=timezone.utc)
    refund_tx = result.get("Transaction") or {}
    refund_tx_id = refund_tx.get("Id") or refund_tx.get("TransactionId")
    if refund_tx_id:
        existing_err = tx.error_message or ""
        tx.error_message = f"{existing_err}[refund_tx:{refund_tx_id}]".strip()

    db.commit()

    logger.info(
        "admin.refund_ok tx_id=%s actor=%s amount_chf=%s reason=%s",
        tx_id, actor.id, body.amount_chf, body.reason,
    )

    return {
        **_payment_tx_dict(tx),
        "refund_saferpay_response": {k: v for k, v in result.items() if k in {"Transaction", "ExecutionDateTime", "ResponseHeader"}},
    }


# ── Activity Log ───────────────────────────────────────────────────────────────

@router.get("/activity-logs", summary="List user activity logs (superadmin)")
def list_activity_logs(
    user_id: int | None = Query(None),
    org_id: int | None = Query(None),
    action: str | None = Query(None, description="Exact action slug, e.g. company_viewed"),
    resource_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
) -> dict:
    from app.models.activity_log import ActivityLog

    query = db.query(ActivityLog)
    if user_id is not None:
        query = query.filter(ActivityLog.user_id == user_id)
    if org_id is not None:
        query = query.filter(ActivityLog.org_id == org_id)
    if action:
        query = query.filter(ActivityLog.action == action)
    if resource_type:
        query = query.filter(ActivityLog.resource_type == resource_type)

    total = query.count()
    rows = (
        query.order_by(ActivityLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    def _row(r: ActivityLog) -> dict:
        return {
            "id": r.id,
            "action": r.action,
            "user_id": r.user_id,
            "user_email": r.user.email if r.user else None,
            "org_id": r.org_id,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "meta": r.meta,
            "ip": r.ip,
            "created_at": r.created_at.isoformat(),
        }

    return {
        "items": [_row(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/activity-logs/summary", summary="Activity summary counts by action (superadmin)")
def activity_log_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
) -> dict:
    from datetime import timedelta
    from app.models.activity_log import ActivityLog

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    action_rows = (
        db.query(ActivityLog.action, func.count(ActivityLog.id).label("cnt"))
        .filter(ActivityLog.created_at >= cutoff)
        .group_by(ActivityLog.action)
        .order_by(func.count(ActivityLog.id).desc())
        .all()
    )

    dau: int = (
        db.query(func.count(func.distinct(ActivityLog.user_id)))
        .filter(ActivityLog.created_at >= cutoff, ActivityLog.user_id.isnot(None))
        .scalar() or 0
    )

    total_events: int = (
        db.query(func.count(ActivityLog.id))
        .filter(ActivityLog.created_at >= cutoff)
        .scalar() or 0
    )

    return {
        "period_days": days,
        "total_events": total_events,
        "distinct_active_users": dau,
        "by_action": {row.action: int(row.cnt) for row in action_rows},
    }


# ── Credit Transactions ────────────────────────────────────────────────────────

@router.get("/credit-transactions", summary="List all credit transactions across all orgs (superadmin)")
def list_credit_transactions(
    org_id: int | None = Query(None, description="Filter by org"),
    user_id: int | None = Query(None, description="Filter by user who triggered the transaction (via reference_id)"),
    tx_type: str | None = Query(None, description="Filter by type: grant | topup | deduction | refund | expire"),
    action_type: str | None = Query(None, description="Filter by action: web_search | batch_llm | etc."),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
) -> dict:
    query = db.query(OrgCreditTransaction)
    if org_id is not None:
        query = query.filter(OrgCreditTransaction.org_id == org_id)
    if tx_type:
        query = query.filter(OrgCreditTransaction.type == tx_type)
    if action_type:
        query = query.filter(OrgCreditTransaction.action_type == action_type)

    total = query.count()
    rows = (
        query.order_by(OrgCreditTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    org_ids = {r.org_id for r in rows if r.org_id is not None}
    orgs_by_id: dict[int, Organization] = {}
    if org_ids:
        orgs_by_id = {o.id: o for o in db.query(Organization).filter(Organization.id.in_(org_ids)).all()}

    def _row(r: OrgCreditTransaction) -> dict:
        org = orgs_by_id.get(r.org_id) if r.org_id else None
        return {
            "id": r.id,
            "org_id": r.org_id,
            "org_name": org.name if org else None,
            "amount": r.amount,
            "type": r.type,
            "action_type": r.action_type,
            "reference_id": r.reference_id,
            "credits_before": r.credits_before,
            "credits_after": r.credits_after,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "created_at": r.created_at.isoformat(),
        }

    return {
        "items": [_row(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/orgs/{org_id}/payment-transactions", summary="List payment transactions for org (superadmin)")
def list_org_payment_transactions(
    org_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    query = db.query(PaymentTransaction).filter(PaymentTransaction.org_id == org_id)
    total = query.count()
    transactions = (
        query.order_by(PaymentTransaction.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [_payment_tx_dict(tx) for tx in transactions],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
