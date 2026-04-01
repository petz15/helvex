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

    # Entitlement: simple tier gets one free flex rescore job per billing month.
    if action == "flex_rescore" and normalize_tier(org.tier) == "simple" and not org.monthly_rescore_used:
        before = int(org.credits_balance or 0)
        org.monthly_rescore_used = True
        org.monthly_rescore_reset_at = datetime.now(tz=timezone.utc)
        _create_ledger_row(
            db,
            org_id=org_id,
            amount=0,
            tx_type="deduction",
            action_type=action,
            reference_id=reference_id,
            credits_before=before,
            credits_after=before,
        )
        db.commit()
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
    return True


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
    org = (
        db.query(Organization)
        .filter(Organization.id == org_id)
        .with_for_update()
        .first()
    )
    if org is None:
        raise ValueError("Organization not found")

    if normalize_tier(org.tier) == "simple":
        org.monthly_rescore_used = False
        org.monthly_rescore_reset_at = datetime.now(tz=timezone.utc)

    db.commit()
