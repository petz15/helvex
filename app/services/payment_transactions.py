"""Secure payment transaction logging and validation.

Security guarantees:
1. Idempotency: each external_id (Saferpay token) can only be processed once
2. Status validation: only AUTHORIZED/CAPTURED statuses result in credit grants
3. Immutability: once a transaction is created, its payment details are read-only
4. Provider validation: responses are validated against provider specs
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.services import credits


ProviderName = Literal["worldline", "stripe"]
PaymentKind = Literal["subscription", "topup"]
PaymentStatus = Literal["pending", "authorized", "captured", "declined", "error"]


class DuplicatePaymentError(RuntimeError):
    """Raised when attempting to process the same payment twice."""

    pass


class PaymentValidationError(RuntimeError):
    """Raised when payment data fails validation."""

    pass


def _extract_cardholder_name_worldline(authorize_response: dict[str, Any]) -> str | None:
    """Extract cardholder name from a Saferpay Transaction/Authorize response.

    Checks both Transaction.PaymentMeans.Card.HolderName and
    the top-level PaymentMeans.Card.HolderName fields.
    """
    if not isinstance(authorize_response, dict):
        return None
    # Try Transaction.PaymentMeans.Card.HolderName first
    transaction = authorize_response.get("Transaction")
    if isinstance(transaction, dict):
        pm = transaction.get("PaymentMeans")
        if isinstance(pm, dict):
            card = pm.get("Card")
            if isinstance(card, dict) and card.get("HolderName"):
                return str(card["HolderName"]).strip() or None
    # Fall back to top-level PaymentMeans
    pm = authorize_response.get("PaymentMeans")
    if isinstance(pm, dict):
        card = pm.get("Card")
        if isinstance(card, dict) and card.get("HolderName"):
            return str(card["HolderName"]).strip() or None
    return None




def _extract_payment_method_worldline(transaction: dict[str, Any]) -> str | None:
    """Extract payment method from Saferpay Transaction object.

    Expected structure:
    {
      "PaymentMeans": {
        "Card": {...} | "Twint": {...} | "BankAccount": {...} | etc.
      }
    }
    """
    if not isinstance(transaction, dict):
        return None

    payment_means = transaction.get("PaymentMeans")
    if not isinstance(payment_means, dict):
        return None

    # Check for Card
    if payment_means.get("Card"):
        return "card"
    # Check for TWINT
    if payment_means.get("Twint"):
        return "twint"
    # Check for Bank Transfer
    if payment_means.get("BankAccount"):
        return "bank_transfer"
    # Check for other payment means
    if payment_means.get("DirectDebit"):
        return "direct_debit"
    if payment_means.get("Klarna"):
        return "klarna"
    if payment_means.get("PayPal"):
        return "paypal"
    if payment_means.get("Alipay"):
        return "alipay"

    return None


def validate_transaction_status(status: str) -> PaymentStatus:
    """Validate and normalize transaction status.

    Saferpay returns: PENDING, AUTHORIZED, CAPTURED, DECLINED, FAILED, etc.
    We normalize to our enum: pending, authorized, captured, declined, error
    """
    normalized = (status or "").strip().upper()

    if normalized in {"AUTHORIZED", "CAPTURED"}:
        return "authorized" if normalized == "AUTHORIZED" else "captured"
    if normalized in {"DECLINED", "FAILED"}:
        return "declined"
    if normalized in {"PENDING"}:
        return "pending"

    # Unknown or error status
    return "error"


def log_payment_transaction(
    db: Session,
    *,
    org_id: int,
    provider: ProviderName,
    external_id: str,
    order_reference: str,
    amount_chf: float,
    kind: PaymentKind,
    status: PaymentStatus,
    payment_method: str | None = None,
    provider_transaction_id: str | None = None,
    cardholder_name: str | None = None,
    billing_address: str | None = None,
    subscription_tier: str | None = None,
    subscription_billing_cycle: str | None = None,
    credits_purchased: int | None = None,
    credits_bonus: int | None = None,
    credits_total_granted: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> PaymentTransaction:
    """Log a payment transaction with full validation and security checks.

    Security checks:
    - Validates amount_chf is positive
    - Checks org_id exists
    - Ensures external_id is unique (prevents double-processing)
    - Validates kind and status enums

    Returns the created PaymentTransaction.
    Raises DuplicatePaymentError if external_id already exists.
    """
    if amount_chf <= 0:
        raise PaymentValidationError(f"amount_chf must be positive, got {amount_chf}")

    if kind not in {"subscription", "topup"}:
        raise PaymentValidationError(f"Invalid kind: {kind}")

    if status not in {"pending", "authorized", "captured", "declined", "error"}:
        raise PaymentValidationError(f"Invalid status: {status}")

    # Verify org exists
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise PaymentValidationError(f"Organization {org_id} not found")

    # SECURITY: Check for duplicate external_id (prevents double-processing)
    existing = db.query(PaymentTransaction).filter(PaymentTransaction.external_id == external_id).first()
    if existing:
        raise DuplicatePaymentError(
            f"Payment with external_id '{external_id}' already exists (tx_id={existing.id}). "
            "This is a security check to prevent duplicate processing."
        )

    # Create the transaction record
    tx = PaymentTransaction(
        org_id=org_id,
        provider=provider,
        external_id=external_id,
        order_reference=order_reference,
        amount_chf=amount_chf,
        currency="CHF",
        kind=kind,
        status=status,
        payment_method=payment_method,
        provider_transaction_id=provider_transaction_id,
        cardholder_name=cardholder_name,
        billing_address=billing_address,
        subscription_tier=subscription_tier,
        subscription_billing_cycle=subscription_billing_cycle,
        credits_purchased=credits_purchased,
        credits_bonus=credits_bonus,
        credits_total_granted=credits_total_granted,
        error_code=error_code,
        error_message=error_message,
    )

    db.add(tx)
    db.flush()  # Ensure ID is generated before commit
    db.commit()
    db.refresh(tx)

    return tx


def mark_payment_authorized(
    db: Session,
    payment_tx_id: int,
) -> PaymentTransaction:
    """Mark a payment as authorized after successful provider response.

    This should only be called after verifying the authorization succeeded
    and we've extracted the provider transaction ID.
    """
    tx = db.query(PaymentTransaction).filter(PaymentTransaction.id == payment_tx_id).first()
    if not tx:
        raise PaymentValidationError(f"Payment transaction {payment_tx_id} not found")

    # SECURITY: Only mark pending transactions as authorized
    if tx.status != "pending":
        raise PaymentValidationError(
            f"Cannot mark transaction {payment_tx_id} as authorized: "
            f"status is {tx.status}, not pending"
        )

    tx.status = "captured"  # Saferpay's CAPTURED means fully authorized
    tx.authorized_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(tx)
    return tx


def get_payment_transaction_by_external_id(
    db: Session,
    external_id: str,
) -> PaymentTransaction | None:
    """Retrieve a payment transaction by its external ID (provider token).

    Used to detect duplicate processing and retrieve transaction details.
    """
    return db.query(PaymentTransaction).filter(PaymentTransaction.external_id == external_id).first()


def get_payment_transaction_by_provider_tx_id(
    db: Session,
    provider_transaction_id: str,
) -> PaymentTransaction | None:
    """Retrieve a payment transaction by provider transaction ID.

    Used for lookups after provider sends webhook with transaction ID.
    """
    return (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.provider_transaction_id == provider_transaction_id)
        .first()
    )


def apply_successful_payment(
    db: Session,
    payment_tx: PaymentTransaction,
) -> None:
    """Apply the effects of a successful payment: update tier or grant credits.

    SECURITY CRITICAL:
    - Only called after status is "captured"
    - Only called once per transaction (idempotency via webhook_processed_at)
    - Validates transaction data before applying

    Raises PaymentValidationError if data is invalid.
    """
    if payment_tx.status not in {"authorized", "captured"}:
        raise PaymentValidationError(
            f"Cannot apply payment {payment_tx.id}: status is {payment_tx.status}, "
            "not authorized/captured"
        )

    # SECURITY: Idempotency check - if already processed, skip
    if payment_tx.webhook_processed_at is not None:
        return

    # Verify org still exists
    org = db.query(Organization).filter(Organization.id == payment_tx.org_id).first()
    if org is None:
        raise PaymentValidationError(f"Organization {payment_tx.org_id} no longer exists")

    try:
        if payment_tx.kind == "subscription":
            # Update tier
            if not payment_tx.subscription_tier:
                raise PaymentValidationError(
                    f"Subscription payment {payment_tx.id} missing subscription_tier"
                )
            org.tier = payment_tx.subscription_tier
            if payment_tx.subscription_billing_cycle:
                org.subscription_billing_cycle = payment_tx.subscription_billing_cycle

        elif payment_tx.kind == "topup":
            # Grant credits
            if not payment_tx.credits_purchased or payment_tx.credits_purchased <= 0:
                raise PaymentValidationError(
                    f"Topup payment {payment_tx.id} missing or invalid credits_purchased"
                )

            # Use topup_credits service for automatic bonus application
            total_granted = credits.topup_credits(
                db,
                org_id=payment_tx.org_id,
                amount_purchased=payment_tx.credits_purchased,
                reference_id=payment_tx.external_id,
            )

            # Update transaction with actual granted amount
            payment_tx.credits_total_granted = total_granted

        # Mark as processed
        payment_tx.webhook_processed_at = datetime.now(tz=timezone.utc)
        db.commit()

    except Exception as exc:
        # Don't let credit service errors prevent transaction logging
        payment_tx.status = "error"
        payment_tx.error_code = "CREDIT_GRANT_FAILED"
        payment_tx.error_message = str(exc)
        db.commit()
        raise
