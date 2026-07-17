"""Nightly subscription billing renewal service.

Runs every night (scheduled via main.py) to:
1. Retry any Worldline transactions stuck in 'authorized' (capture previously failed).
2. Downgrade orgs that requested cancellation and have reached their period end.
3. Charge recurring subscriptions that are due using Transaction/AuthorizeReferenced.
4. Renew subscription_period_end on success, or downgrade to free on failure.
"""
from __future__ import annotations

import calendar
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.services.billing import payments, payment_transactions
from app.services.billing.billing_addresses import get_default_billing_address
from app.services.billing.payments.pricing import apply_vat

logger = logging.getLogger(__name__)


def _billing_country_for_org(db: Session, org: Organization) -> str:
    """Return billing country for an org, falling back to the default payment user's address."""
    addr = get_default_billing_address(getattr(org, "billing_address_json", None))
    if addr and addr.get("country"):
        return str(addr["country"]).upper().strip()
    user_id = getattr(org, "default_payment_user_id", None)
    if user_id:
        user = db.get(User, user_id)
        if user:
            user_addr = get_default_billing_address(user.billing_address_json)
            if user_addr and user_addr.get("country"):
                return str(user_addr["country"]).upper().strip()
    return ""


# Alert recipient for payment infrastructure failures
_ALERT_EMAIL = "peter@balogh-consulting.ch"


def _is_non_initial_recurring_reference_error(message: str) -> bool:
    upper = str(message or "").upper()
    return (
        "ACTION_NOT_SUPPORTED" in upper
        and "INITIAL RECURRING PAYMENT" in upper
    )


def _find_latest_initial_subscription_reference(
    db: Session,
    *,
    org_id: int,
    exclude_tx_id: str | None = None,
) -> str | None:
    """Find the most recent checkout subscription tx usable as recurring anchor."""
    excluded = str(exclude_tx_id or "").strip()
    candidates = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.org_id == org_id,
            PaymentTransaction.provider == "worldline",
            PaymentTransaction.kind == "subscription",
            PaymentTransaction.order_reference.like("wl_sub_%"),
            PaymentTransaction.status.in_(["authorized", "captured"]),
            PaymentTransaction.provider_transaction_id.isnot(None),
        )
        .order_by(PaymentTransaction.id.desc())
        .all()
    )

    for tx in candidates:
        tx_id = str(tx.provider_transaction_id or "").strip()
        if tx_id and tx_id != excluded:
            return tx_id
    return None


def retry_failed_captures(db: Session) -> dict[str, Any]:
    """Retry Worldline captures for transactions stuck in 'authorized' status.

    These are payments where Authorization succeeded but the subsequent Capture
    call failed (network timeout, config error, etc.).  Saferpay authorizations
    expire after ~7 days, so we only attempt transactions created in that window.

    Returns stats: {attempted, captured, failed, failed_tx_ids}
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=6)

    pending = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.status == "authorized",
            PaymentTransaction.provider == "worldline",
            PaymentTransaction.provider_transaction_id.isnot(None),
            PaymentTransaction.created_at >= cutoff,
        )
        .all()
    )

    stats: dict[str, Any] = {
        "attempted": len(pending),
        "captured": 0,
        "failed": 0,
        "failed_tx_ids": [],
    }

    if not pending:
        logger.info("capture_retry.none_pending")
        return stats

    logger.info("capture_retry.start count=%s", len(pending))
    provider = payments.WorldlineProvider()

    for tx in pending:
        provider_tx_id = str(tx.provider_transaction_id or "").strip()
        try:
            provider.capture_transaction(transaction_id=provider_tx_id)
            tx.status = "captured"
            db.commit()
            stats["captured"] += 1
            logger.info(
                "capture_retry.ok tx_id=%s org_id=%s provider_tx=%s",
                tx.id, tx.org_id, provider_tx_id[:20],
            )
        except (payments.PaymentConfigurationError, RuntimeError) as exc:
            stats["failed"] += 1
            stats["failed_tx_ids"].append(tx.id)
            logger.error(
                "capture_retry.failed tx_id=%s org_id=%s provider_tx=%s error=%s",
                tx.id, tx.org_id, provider_tx_id[:20], exc,
            )

    logger.info(
        "capture_retry.done attempted=%s captured=%s failed=%s",
        stats["attempted"], stats["captured"], stats["failed"],
    )
    return stats


def _next_period_end(current_end: datetime, billing_cycle: str) -> datetime:
    if billing_cycle == "yearly":
        # Add one year (handle leap years via timedelta approximation)
        try:
            return current_end.replace(year=current_end.year + 1)
        except ValueError:
            # Feb 29 → Feb 28 of next year
            return current_end.replace(year=current_end.year + 1, day=28)
    # monthly: advance to the next calendar month, clamping to the last valid day
    candidate = current_end + timedelta(days=31)
    last_day = calendar.monthrange(candidate.year, candidate.month)[1]
    return candidate.replace(day=min(current_end.day, last_day))


def run_billing_renewal(db: Session) -> dict[str, Any]:
    """Process all subscriptions due for renewal or cancellation.

    Steps:
      1. Retry any pending captures from a previous failed Capture call.
      2. Process cancellations and recurring charges for due subscriptions.

    Returns a stats dict: {processed, renewed, cancelled, failed, skipped, capture_retry}
    """
    from app.services.notifications.email import send_capture_failure_alert

    # ── Step 1: retry stuck captures ─────────────────────────────────────────
    capture_stats = retry_failed_captures(db)
    if capture_stats["failed_tx_ids"]:
        try:
            send_capture_failure_alert(
                to=_ALERT_EMAIL,
                failed_tx_ids=capture_stats["failed_tx_ids"],
            )
        except Exception as mail_exc:  # noqa: BLE001
            logger.error("capture_retry.alert_email_failed error=%s", mail_exc)

    # ── Step 2: subscription renewals / cancellations ─────────────────────────
    now = datetime.now(tz=timezone.utc)
    stats: dict[str, Any] = {
        "processed": 0,
        "renewed": 0,
        "cancelled": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
        "capture_retry": capture_stats,
    }

    # Find all paid orgs whose subscription_period_end is in the past.
    # Include a 1-minute buffer to catch subscriptions that just expired (handles clock skew).
    due_orgs = (
        db.query(Organization)
        .filter(
            Organization.tier_id > 0,  # not free
            Organization.subscription_period_end.isnot(None),
            Organization.subscription_period_end <= now + timedelta(minutes=1),
        )
        .all()
    )

    logger.info("billing_renewal.start due_count=%s now=%s", len(due_orgs), now.isoformat())

    for org in due_orgs:
        stats["processed"] += 1
        org_id = org.id
        tier = org.tier
        cycle = org.subscription_billing_cycle or "monthly"

        # --- Cancel-at-period-end ---
        if org.subscription_cancel_at_period_end:
            pending_tier = getattr(org, "pending_downgrade_tier", None)
            if pending_tier:
                # Scheduled downgrade: switch to the lower tier and charge for its
                # first period. On payment failure fall back to free.
                logger.info(
                    "billing_renewal.scheduled_downgrade org_id=%s from=%s to=%s",
                    org_id, tier, pending_tier,
                )
                org.tier = pending_tier
                org.pending_downgrade_tier = None
                org.subscription_cancel_at_period_end = False
                # Reuse the remaining renewal logic below — tier/cycle vars need refresh
                tier = org.tier
                cycle = org.subscription_billing_cycle or "monthly"
                db.commit()
                # Fall through to the recurring-charge block so the new plan's first
                # period is charged immediately.
            else:
                # Plain cancellation: downgrade to free.
                logger.info(
                    "billing_renewal.cancel org_id=%s tier=%s cycle=%s period_end=%s",
                    org_id, tier, cycle,
                    org.subscription_period_end.isoformat() if org.subscription_period_end else "None",
                )
                org.tier = "free"
                org.subscription_billing_cycle = "monthly"
                org.subscription_period_end = None
                org.subscription_cancel_at_period_end = False
                db.commit()
                stats["cancelled"] += 1
                continue

        # --- No recurring reference → cannot charge, downgrade ---
        ref_tx_id = str(org.recurring_transaction_id or "").strip()
        if not ref_tx_id:
            logger.warning(
                "billing_renewal.no_reference org_id=%s tier=%s — downgrading to free",
                org_id, tier,
            )
            org.tier = "free"
            org.subscription_billing_cycle = "monthly"
            org.subscription_period_end = None
            db.commit()
            stats["failed"] += 1
            stats["errors"].append(f"org_id={org_id}: no recurring_transaction_id")
            continue

        # --- Compute renewal amount ---
        try:
            amount_chf = payments.compute_subscription_price_chf(
                tier=tier,
                billing_cycle=cycle,  # type: ignore[arg-type]
                custom_features=getattr(org, "custom_features", None),
                verified_business=bool(getattr(org, "verified_business", False)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("billing_renewal.price_error org_id=%s tier=%s", org_id, tier)
            stats["failed"] += 1
            stats["errors"].append(f"org_id={org_id}: price error: {exc}")
            continue

        if amount_chf <= 0:
            logger.info("billing_renewal.skip_free org_id=%s tier=%s amount=0", org_id, tier)
            stats["skipped"] += 1
            continue

        # --- Apply VAT based on org's default billing address ---
        billing_country = _billing_country_for_org(db, org)
        vat_rate, vat_amount, amount_with_vat = apply_vat(amount_chf, billing_country, getattr(org, "vat_id", None))
        logger.info(
            "billing_renewal.vat org_id=%s country=%s vat_rate=%.4f base=%.2f vat=%.4f total=%.2f",
            org_id, billing_country or "unknown", vat_rate, amount_chf, vat_amount, amount_with_vat,
        )

        order_reference = f"wl_recur_{org_id}_{secrets.token_hex(6)}"
        description = f"Helvex {tier.title()} ({cycle}) renewal"

        # --- Charge via AuthorizeReferenced ---
        provider = payments.WorldlineProvider()
        charge_error: RuntimeError | None = None
        try:
            result = provider.authorize_referenced_transaction(
                org_id=org_id,
                transaction_id=ref_tx_id,
                amount_chf=amount_with_vat,
                order_reference=order_reference,
                description=description,
            )
        except RuntimeError as exc:
            charge_error = exc

        if charge_error is not None and _is_non_initial_recurring_reference_error(str(charge_error)):
            # Recovery path for legacy bad references: old code rotated
            # recurring_transaction_id to non-initial recurring transactions.
            # Worldline requires an initial recurring transaction as reference.
            recovered_ref = _find_latest_initial_subscription_reference(
                db,
                org_id=org_id,
                exclude_tx_id=ref_tx_id,
            )
            if recovered_ref:
                logger.warning(
                    "billing_renewal.recover_reference org_id=%s old_ref=%s new_ref=%s",
                    org_id,
                    ref_tx_id[:20],
                    recovered_ref[:20],
                )
                org.recurring_transaction_id = recovered_ref
                db.commit()
                ref_tx_id = recovered_ref
                try:
                    result = provider.authorize_referenced_transaction(
                        org_id=org_id,
                        transaction_id=ref_tx_id,
                        amount_chf=amount_with_vat,
                        order_reference=order_reference,
                        description=description,
                    )
                    charge_error = None
                except RuntimeError as retry_exc:
                    charge_error = retry_exc

        if charge_error is not None:
            logger.error(
                "billing_renewal.charge_failed org_id=%s tier=%s error=%s",
                org_id, tier, charge_error,
            )
            # Downgrade on payment failure
            org.tier = "free"
            org.subscription_billing_cycle = "monthly"
            org.subscription_period_end = None
            db.commit()
            stats["failed"] += 1
            stats["errors"].append(f"org_id={org_id}: charge failed: {charge_error}")
            continue

        transaction = result.get("Transaction") if isinstance(result, dict) else {}
        new_tx_id = str(transaction.get("Id") or "") if isinstance(transaction, dict) else ""
        tx_status = str(transaction.get("Status") or "").upper() if isinstance(transaction, dict) else ""
        normalized = payment_transactions.validate_transaction_status(tx_status)

        # AuthorizeReferenced returns AUTHORIZED; we must capture explicitly.
        if normalized == "authorized" and new_tx_id:
            try:
                provider.capture_transaction(transaction_id=new_tx_id)
                normalized = "captured"
                logger.info(
                    "billing_renewal.capture_ok org_id=%s new_tx_id=%s",
                    org_id, new_tx_id[:20],
                )
            except RuntimeError as cap_exc:
                logger.warning(
                    "billing_renewal.capture_pending org_id=%s new_tx_id=%s error=%s — retry next run",
                    org_id, new_tx_id[:20], cap_exc,
                )
                # normalized stays "authorized" so retry_failed_captures picks it up tonight

        # Log the renewal transaction
        try:
            payment_transactions.log_payment_transaction(
                db,
                org_id=org_id,
                provider="worldline",
                external_id=new_tx_id or order_reference,
                order_reference=order_reference,
                amount_chf=amount_chf,
                kind="subscription",
                status=normalized,
                provider_transaction_id=new_tx_id or None,
                subscription_tier=tier,
                subscription_billing_cycle=cycle,
            )
        except (payment_transactions.DuplicatePaymentError, payment_transactions.PaymentValidationError) as exc:
            logger.warning("billing_renewal.log_failed org_id=%s error=%s", org_id, exc)

        if normalized in {"authorized", "captured"}:
            # Renew: advance period_end
            current_end = org.subscription_period_end or now
            org.subscription_period_end = _next_period_end(current_end, cycle)
            # Keep the original initial recurring reference from checkout.
            # Worldline expects an initial recurring tx as the long-lived anchor
            # for future AuthorizeReferenced calls.
            db.commit()
            logger.info(
                "billing_renewal.renewed org_id=%s tier=%s new_period_end=%s captured=%s",
                org_id, tier, org.subscription_period_end.isoformat(), normalized == "captured",
            )
            stats["renewed"] += 1
        else:
            logger.warning(
                "billing_renewal.charge_declined org_id=%s tier=%s tx_status=%s — downgrading",
                org_id, tier, tx_status,
            )
            org.tier = "free"
            org.subscription_billing_cycle = "monthly"
            org.subscription_period_end = None
            db.commit()
            stats["failed"] += 1
            stats["errors"].append(f"org_id={org_id}: declined tx_status={tx_status}")

    logger.info(
        "billing_renewal.done processed=%s renewed=%s cancelled=%s failed=%s skipped=%s",
        stats["processed"], stats["renewed"], stats["cancelled"], stats["failed"], stats["skipped"],
    )
    return stats
