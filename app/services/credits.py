"""Organization credit ledger service.

Credits are integer units (1 credit = 0.0001 CHF).

Deductions always use the full base cost — there is no per-deduction discount.
Tier benefits are instead expressed as *bonus* credits granted on top of every
top-up purchase.  E.g. an Explorer org that buys 10,000 credits receives an
extra 1,500 bonus credits (15 %) at the time of purchase.

Superadmin orgs set ``credits_unlimited = True``, which causes ``check_and_deduct``
to always succeed without modifying the balance.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.org_credit_transaction import OrgCreditTransaction
from app.models.organization import Organization
from app.services.tiers import get_topup_bonus_rate, get_tier_rank, normalize_tier

CREDIT_COSTS: dict[str, int] = {
    "batch_llm": 8,               # per company
    "immediate_llm": 12,          # per company
    "web_search": 20,             # per company
    "flex_rescore": 1,            # per company
    "recluster": 100_000,         # flat
    "bulk_export_basic": 6_000,   # per 10k rows unit
    "bulk_export_detail": 13_000, # per 10k rows unit
}


def compute_cost(action: str, count: int) -> int:
    """Return the full base credit cost for an action (no discounts applied).

    Raises ValueError for unknown actions or invalid counts.
    """
    if action not in CREDIT_COSTS:
        raise ValueError(f"Unknown credit action: {action}")
    if count <= 0:
        raise ValueError("count must be > 0")
    return CREDIT_COSTS[action] * count


def _create_ledger_row(
    db: Session,
    *,
    org_id: int,
    amount: int,
    tx_type: str,
    action_type: str | None,
    reference_id: str | None,
    credits_before: int,
    credits_after: int,
    expires_at: datetime | None = None,
) -> OrgCreditTransaction:
    tx = OrgCreditTransaction(
        org_id=org_id,
        amount=amount,
        type=tx_type,
        action_type=action_type,
        reference_id=reference_id,
        credits_before=credits_before,
        credits_after=credits_after,
        expires_at=expires_at,
    )
    db.add(tx)
    return tx


def check_and_deduct(
    db: Session,
    org_id: int,
    action: str,
    count: int,
    *,
    reference_id: str | None = None,
) -> bool:
    """Atomically verify balance and deduct credits at full base cost.

    Returns False if funds are insufficient.
    Raises ValueError for invalid actions/count or missing org.

    Orgs with ``credits_unlimited = True`` always return True without any
    balance change or ledger entry (used for superadmin orgs).
    """
    org = (
        db.query(Organization)
        .filter(Organization.id == org_id)
        .with_for_update()
        .first()
    )
    if org is None:
        raise ValueError("Organization not found")

    # Superadmin orgs are never blocked by credits.
    if org.credits_unlimited:
        return True

    # Entitlement: explorer+ flex rescoring is free.
    if action == "flex_rescore" and get_tier_rank(org) >= 2:
        return True

    cost = compute_cost(action, count)
    before = int(org.credits_balance or 0)
    after = before - cost
    if after < 0:
        return False

    org.credits_balance = after
    _create_ledger_row(
        db,
        org_id=org_id,
        amount=-cost,
        tx_type="deduction",
        action_type=action,
        reference_id=reference_id,
        credits_before=before,
        credits_after=after,
    )
    db.commit()

    # Low-credit alert: fire when the balance crosses below 20% of the last
    # top-up amount, or below a fixed floor of 5,000 credits, whichever is
    # larger.  Best-effort — never raises.
    _maybe_low_credit_alert(db, org_id=org_id, balance=after, before=before)

    return True


# 1 credit = 0.0001 CHF → 5,000 credits = 0.50 CHF (absolute floor)
_LOW_CREDIT_FLOOR = 5_000


def _maybe_low_credit_alert(
    db: "Session",
    *,
    org_id: int,
    balance: int,
    before: int,
) -> None:
    """Emit a low-credit event when the balance drops into the warning zone.

    Only fires once per crossing (not on every subsequent deduction while
    already low) by checking whether `before` was above the threshold.
    Logged as a ``job_run_event`` on a synthetic sentinel job so it surfaces
    in the admin event log without requiring a separate notifications table.
    """
    if balance >= before:
        return  # balance went up or unchanged — no alert needed

    try:
        # Determine threshold: max(5000, 20% of last topup)
        from app.models.org_credit_transaction import OrgCreditTransaction
        from sqlalchemy import desc as _desc
        last_topup = (
            db.query(OrgCreditTransaction)
            .filter(
                OrgCreditTransaction.org_id == org_id,
                OrgCreditTransaction.type == "topup",
            )
            .order_by(_desc(OrgCreditTransaction.created_at))
            .first()
        )
        threshold = _LOW_CREDIT_FLOOR
        if last_topup is not None:
            threshold = max(_LOW_CREDIT_FLOOR, int(last_topup.amount * 0.20))

        # Only alert on the crossing (before was above, after is below)
        if before <= threshold or balance > threshold:
            return

        import logging as _logging
        _log = _logging.getLogger(__name__)
        _log.warning(
            "low_credit_alert org_id=%d balance=%d threshold=%d",
            org_id, balance, threshold,
        )

        # Store as an org-scoped app setting flag so the frontend can surface it.
        # Key: "low_credit_alert_at" — value: ISO timestamp.  Cleared when org tops up.
        from datetime import datetime, timezone
        from app.crud.app_setting import set_org_setting
        set_org_setting(db, org_id=org_id, key="low_credit_alert_at",
                        value=datetime.now(tz=timezone.utc).isoformat())

        _send_low_credit_email(db, org_id=org_id, balance=balance, threshold=threshold)

    except Exception:  # noqa: BLE001
        pass  # never break a deduction because of alert logic


def _send_low_credit_email(
    db: "Session",
    *,
    org_id: int,
    balance: int,
    threshold: int,
) -> None:
    """Send a low-credit alert email to the org admin if notifications are enabled."""
    try:
        from app.crud.app_setting import get_effective_setting
        if get_effective_setting(db, "email_notifications", org_id=org_id, default="1") != "1":
            return
        if get_effective_setting(db, "notif_low_credit", org_id=org_id, default="1") != "1":
            return
        from app.models.organization import Organization
        from app.models.user import User
        org = db.get(Organization, org_id)
        if not org:
            return
        # Email the org admin (earliest-created active admin/owner member)
        from app.models.org_member import OrgMember
        admin_member = (
            db.query(OrgMember)
            .filter(
                OrgMember.org_id == org_id,
                OrgMember.role.in_(["admin", "owner"]),
            )
            .order_by(OrgMember.created_at)
            .first()
        )
        if not admin_member:
            return
        user = db.get(User, admin_member.user_id)
        if not user or not user.is_active:
            return
        from app.services.email import send_low_credit_alert
        send_low_credit_alert(
            to=user.email,
            org_name=org.name,
            balance=balance,
            threshold=threshold,
        )
    except Exception:  # noqa: BLE001
        pass


def topup_credits(
    db: Session,
    *,
    org_id: int,
    amount_purchased: int,
    reference_id: str | None = None,
    expires_at: datetime | None = None,
) -> int:
    """Credit an org for a top-up purchase and automatically grant tier bonus credits.

    Writes two ledger rows when a bonus applies:
      1. type="topup"  — the purchased credits (never expire unless caller sets expires_at)
      2. type="bonus"  — the tier bonus credits (same expiry as the topup)

    Returns the total credits added (purchased + bonus).
    """
    if amount_purchased <= 0:
        raise ValueError("amount_purchased must be > 0")

    org = (
        db.query(Organization)
        .filter(Organization.id == org_id)
        .with_for_update()
        .first()
    )
    if org is None:
        raise ValueError("Organization not found")

    before = int(org.credits_balance or 0)

    # 1. Grant purchased credits
    after_topup = before + amount_purchased
    org.credits_balance = after_topup
    _create_ledger_row(
        db,
        org_id=org_id,
        amount=amount_purchased,
        tx_type="topup",
        action_type=None,
        reference_id=reference_id,
        credits_before=before,
        credits_after=after_topup,
        expires_at=expires_at,
    )

    # 2. Grant tier bonus on top
    bonus_rate = get_topup_bonus_rate(org)
    bonus_amount = round(amount_purchased * bonus_rate)
    total_granted = amount_purchased

    if bonus_amount > 0:
        after_bonus = after_topup + bonus_amount
        org.credits_balance = after_bonus
        _create_ledger_row(
            db,
            org_id=org_id,
            amount=bonus_amount,
            tx_type="bonus",
            action_type=None,
            reference_id=reference_id,
            credits_before=after_topup,
            credits_after=after_bonus,
            expires_at=expires_at,
        )
        total_granted += bonus_amount

    db.commit()

    # Clear the low-credit alert flag now that the org has topped up.
    try:
        from app.crud.app_setting import set_org_setting
        set_org_setting(db, org_id=org_id, key="low_credit_alert_at", value="")
    except Exception:  # noqa: BLE001
        pass

    return total_granted


def grant_credits(
    db: Session,
    *,
    org_id: int,
    amount: int,
    tx_type: str = "grant",
    action_type: str | None = None,
    reference_id: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Add credits to an org with an explicit ledger type (grant/refund/etc.).

    For customer top-up purchases use ``topup_credits`` instead — it
    automatically appends tier bonus credits.
    """
    if amount <= 0:
        raise ValueError("amount must be > 0")

    org = (
        db.query(Organization)
        .filter(Organization.id == org_id)
        .with_for_update()
        .first()
    )
    if org is None:
        raise ValueError("Organization not found")

    before = int(org.credits_balance or 0)
    after = before + int(amount)
    org.credits_balance = after

    _create_ledger_row(
        db,
        org_id=org_id,
        amount=int(amount),
        tx_type=tx_type,
        action_type=action_type,
        reference_id=reference_id,
        credits_before=before,
        credits_after=after,
        expires_at=expires_at,
    )
    db.commit()


def grant_monthly_entitlements(db: Session, org_id: int) -> None:
    """Reset monthly entitlement flags at billing cycle rollover."""
    # Simple tier monthly free rescore has been removed from the product.
    # This function is kept as a hook for future monthly entitlements.
    pass
