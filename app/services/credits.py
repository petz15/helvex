"""Organization credit ledger service.

Credits are integer units (1 credit = 0.0001 CHF).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.org_credit_transaction import OrgCreditTransaction
from app.models.organization import Organization
from app.services.tiers import get_consumption_discount, get_tier_rank, normalize_tier

CREDIT_COSTS: dict[str, int] = {
    "batch_llm": 8,             # per company
    "immediate_llm": 12,        # per company
    "web_search": 20,           # per company
    "flex_rescore": 1,          # per company
    "recluster": 100_000,       # flat
    "bulk_export_basic": 6_000,   # per 10k rows unit
    "bulk_export_detail": 13_000, # per 10k rows unit
}


def compute_cost(action: str, count: int, org: Organization) -> int:
    """Return discounted credit cost for an action.

    Raises ValueError for unknown actions or invalid counts.
    """
    if action not in CREDIT_COSTS:
        raise ValueError(f"Unknown credit action: {action}")
    if count <= 0:
        raise ValueError("count must be > 0")

    base = CREDIT_COSTS[action] * count
    discount = get_consumption_discount(org)
    discounted = round(base * (1 - discount))
    return max(0, int(discounted))


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
    """Atomically verify balance and deduct credits.

    Returns False if funds are insufficient. Raises ValueError for invalid
    actions/count or missing org.
    """
    org = (
        db.query(Organization)
        .filter(Organization.id == org_id)
        .with_for_update()
        .first()
    )
    if org is None:
        raise ValueError("Organization not found")

    # Entitlements: explorer+ flex rescoring is free.
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

    cost = compute_cost(action, count, org)
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


def grant_credits(
    db: Session,
    *,
    org_id: int,
    amount: int,
    tx_type: str = "topup",
    action_type: str | None = None,
    reference_id: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Increase org credits and write a ledger row.

    amount must be a positive integer number of credits.
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
