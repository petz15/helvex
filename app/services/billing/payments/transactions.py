"""Subscription and credit transaction application."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.services.billing import credits


def apply_subscription_update(
    db: Session,
    *,
    org_id: int,
    tier: str | None,
    billing_cycle: Literal["monthly", "yearly"] | None,
    customer_id: str | None,
    period_end_ts: int | None,
) -> Organization:
    org = db.get(Organization, org_id)
    if org is None:
        raise ValueError("Organization not found")

    if tier:
        org.tier = tier
    if billing_cycle:
        org.subscription_billing_cycle = billing_cycle
    if customer_id:
        org.payment_customer_id = customer_id
    if period_end_ts:
        org.subscription_period_end = datetime.fromtimestamp(period_end_ts, tz=timezone.utc)

    db.commit()
    db.refresh(org)
    return org


def apply_credit_topup(db: Session, *, org_id: int, credits_amount: int, reference_id: str | None = None) -> None:
    if credits_amount <= 0:
        return
    credits.grant_credits(
        db,
        org_id=org_id,
        amount=credits_amount,
        tx_type="topup",
        reference_id=reference_id,
    )


def parse_json_payload(payload: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("Payload must be a JSON object")
    return obj
