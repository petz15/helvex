"""Subscription lifecycle and payment management routes."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_org, require_org_role
from app.database import get_db
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.services.billing import payment_transactions
from app.services.billing.tiers import TIER_RANK, normalize_tier

from app.api.routes.billing._shared import (
    ScheduleDowngradeRequest,
    UpgradeProrationResponse,
    WebhookResponse,
    _cancel_provider_transaction,
    _resolve_tier_amount_chf,
    logger,
)

router = APIRouter()


@router.post("/subscription/cancel", response_model=WebhookResponse)
def cancel_subscription(
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Schedule subscription cancellation at the end of the current billing period."""
    _user, org = user_org
    logger.info(
        "billing.subscription_cancel user_id=%s org_id=%s current_tier=%s period_end=%s",
        _user.id, org.id, org.tier,
        org.subscription_period_end.isoformat() if org.subscription_period_end else "None",
    )
    if getattr(org, "tier", "free") == "free":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Organization is already on the free tier")

    if not org.subscription_period_end:
        org.subscription_period_end = datetime.now(tz=timezone.utc)
        logger.info("billing.subscription_cancel_no_period_end org_id=%s — anchoring period_end to now", org.id)

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


@router.post("/subscription/schedule-downgrade", response_model=WebhookResponse)
def schedule_downgrade(
    body: ScheduleDowngradeRequest,
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Schedule a downgrade to a lower tier at the end of the current billing period."""
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


@router.post("/subscription/upgrade-proration", response_model=UpgradeProrationResponse)
def calculate_upgrade_proration(
    user_org: tuple[User, object] = Depends(require_org_role("admin", "owner")),
    db: Session = Depends(get_db),
) -> UpgradeProrationResponse:
    """Calculate proration credits for upgrading from the current plan."""
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
    tx = db.query(PaymentTransaction).filter(
        PaymentTransaction.id == payment_id, PaymentTransaction.org_id == org.id
    ).first()
    if tx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    payment_transactions.expire_stale_pending_transactions(
        db, org_id=org.id, max_age_minutes=15, on_expire=_cancel_provider_transaction,
    )
    db.refresh(tx)

    try:
        payment_transactions.cancel_pending_transaction(db, tx=tx, on_cancel=_cancel_provider_transaction)
    except payment_transactions.PaymentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WebhookResponse(ok=True)


@router.get("/payments/{payment_id}/invoice", response_class=HTMLResponse)
def get_payment_invoice(
    payment_id: int,
    user_org: tuple[User, object] = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Generate and return an HTML invoice/receipt for a captured payment."""
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

    vat_amount = float(tx.vat_amount_chf) if tx.vat_amount_chf is not None else None
    base_amount = float(tx.amount_chf)
    amount_str = f"CHF {base_amount:.2f}"
    refund_str = f"CHF {tx.refunded_amount_chf:.2f}" if tx.refunded_amount_chf else None
    total_amount = base_amount + (vat_amount or 0.0)
    net_amount = total_amount - (tx.refunded_amount_chf or 0)
    net_amount_str = f"CHF {net_amount:.2f}"

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
        <td class="amount-col" style="font-weight:600;">CHF {base_amount:.2f}</td>
      </tr>
    </tbody>
  </table>

  <div class="totals">
    <div class="total-row">
      <span>Subtotal (excl. VAT)</span>
      <span>{amount_str}</span>
    </div>
    {f'<div class="total-row"><span>VAT ({round((tx.vat_rate or 0) * 100, 2)} %)</span><span>CHF {vat_amount:.2f}</span></div>' if vat_amount is not None else '<div class="total-row"><span>VAT</span><span>—</span></div>'}
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
