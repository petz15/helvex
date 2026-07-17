"""Webhook and provider callback handlers: Worldline return, Worldline card return."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.services.billing import payment_transactions, payments
from app.services.billing.billing_addresses import get_default_billing_address
from app.services.billing.payments.pricing import apply_vat
from app.services.billing.tiers import get_tier_price_chf

from app.api.routes.billing._shared import (
    WebhookResponse,
    _append_query_params,
    _emit,
    _extract_card_info_from_worldline,
    _iframe_redirect,
    _safe_redirect_target,
    _save_alias,
    logger,
)

router = APIRouter()

# Token placeholders that Worldline may return before the customer fills the form.
_WORLDLINE_PLACEHOLDER_TOKENS = {
    "{TOKEN}", "%7BTOKEN%7D", "{token}", "%7Btoken%7D",
    "{Token}", "%7BToken%7D", "{{{PAYMENTPAGETOKEN}}}", "%7B%7B%7BPAYMENTPAGETOKEN%7D%7D%7D",
}


@router.get("/webhooks/worldline/return", response_model=None)
@router.get("/webhooks/worldline/return/{token}", response_model=None)
async def worldline_return(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
) -> HTMLResponse | RedirectResponse:
    params = request.query_params
    callback_ctx = payments.decode_worldline_callback_context(str(params.get("ctx") or "").strip())

    token = str(token or params.get("TOKEN") or params.get("token") or params.get("Token") or "").strip()
    if token in _WORLDLINE_PLACEHOLDER_TOKENS:
        token = ""

    success_url = str(callback_ctx.get("success_url") or params.get("success_url") or "").strip()
    cancel_url = str(callback_ctx.get("cancel_url") or params.get("cancel_url") or "").strip()
    source = str(params.get("source") or "").strip().lower()
    order_reference = str(callback_ctx.get("order_reference") or params.get("order_reference") or "").strip()
    kind = str(callback_ctx.get("kind") or params.get("kind") or "").strip().lower()
    save_payment_method = str(callback_ctx.get("save_payment_method") or "").strip().lower() in {"1", "true", "yes", "on"}
    query_string = request.url.query or ""

    _emit(
        "info",
        "billing.worldline_return_called source=%s token=%s kind=%s order_ref=%s ctx_valid=%s query_string=%s",
        source, token[:20] if token else "NONE", kind,
        order_reference[:30] if order_reference else "NONE",
        "yes" if callback_ctx else "no", query_string[:1000],
    )

    if not callback_ctx and not token:
        _emit(
            "warning",
            "billing.worldline_return_invalid_context source=%s token=%s query=%s",
            source, "NONE", str(request.query_params)[:300],
        )
        target = _append_query_params(cancel_url, {"reason": "invalid_callback_context"})
        return _iframe_redirect(_safe_redirect_target(target))

    pending_payment: PaymentTransaction | None = None
    if not token and callback_ctx and order_reference:
        pending_payment = payment_transactions.get_payment_transaction_by_order_reference(db, order_reference)
        if pending_payment is not None:
            token = str(pending_payment.external_id or "").strip()
            _emit(
                "info",
                "billing.worldline_token_from_pending source=%s order_ref=%s tx_id=%s token=%s",
                source, order_reference[:50], pending_payment.id, token[:20] if token else "NONE",
            )

    if not token:
        _emit(
            "warning",
            "billing.worldline_return_no_token source=%s kind=%s order_ref=%s query=%s query_string=%s",
            source, kind, order_reference[:50] if order_reference else "NONE",
            str(request.query_params)[:300], query_string[:1000],
        )
        target = _append_query_params(cancel_url, {"reason": "missing_token"})
        return _iframe_redirect(_safe_redirect_target(target))

    parsed_ref = payments.parse_worldline_merchant_reference(order_reference)
    _emit(
        "info",
        "billing.worldline_ref_parsed token=%s source=%s kind=%s org_id=%s user_id=%s tier=%s cycle=%s topup_credits=%s",
        token[:20], source, kind, parsed_ref.get("org_id"), parsed_ref.get("user_id"),
        parsed_ref.get("tier"), parsed_ref.get("billing_cycle"), parsed_ref.get("topup_credits"),
    )

    if not parsed_ref.get("org_id"):
        _emit(
            "warning",
            "billing.worldline_invalid_reference token=%s order_ref=%s",
            token[:20], order_reference[:50] if order_reference else "NONE",
        )
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "invalid_reference"}))
        )

    # SECURITY: Check for existing payment to prevent double-processing.
    existing_payment = payment_transactions.get_payment_transaction_by_external_id(db, token)
    if existing_payment:
        _created = existing_payment.created_at
        if _created is not None and _created.tzinfo is None:
            _created = _created.replace(tzinfo=timezone.utc)  # tolerate drivers that return naive datetimes
        if existing_payment.status == "pending" and _created is not None and _created <= datetime.now(tz=timezone.utc) - timedelta(minutes=15):
            existing_payment.status = "declined"
            existing_payment.error_code = "PENDING_TIMEOUT"
            existing_payment.error_message = "Payment expired after 15 minutes"
            existing_payment.webhook_processed_at = datetime.now(tz=timezone.utc)
            db.commit()
            db.refresh(existing_payment)

        _emit(
            "info",
            "billing.worldline_existing_payment token=%s tx_id=%s status=%s kind=%s",
            token[:20], existing_payment.id, existing_payment.status, existing_payment.kind,
        )
        if existing_payment.status in {"authorized", "captured"}:
            return _iframe_redirect(_safe_redirect_target(success_url))
        if existing_payment.status in {"declined", "error"}:
            return _iframe_redirect(
                _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"}))
            )
        pending_payment = existing_payment

    # SECURITY: the granted entitlement (tier / credits / kind) MUST come from a
    # server-trusted source — the pending transaction created at checkout with the
    # real, server-computed values — not the unsigned return query params.
    # decode_worldline_callback_context() returns {} on a bad/absent signature, so
    # `order_reference` and `kind` otherwise fall back to fully attacker-controlled
    # query params, letting a caller who holds any valid token forge a higher tier
    # or a large credit grant. When a pending tx exists, re-derive everything from
    # ITS stored order_reference. If there is neither a pending tx nor a validly
    # signed ctx, refuse to grant any entitlement.
    if pending_payment is not None:
        order_reference = (pending_payment.order_reference or order_reference).strip()
        kind = (pending_payment.kind or kind).strip().lower()
        parsed_ref = payments.parse_worldline_merchant_reference(order_reference)
    elif not callback_ctx and (
        kind in {"subscription", "topup"}
        or parsed_ref.get("tier")
        or parsed_ref.get("topup_credits")
    ):
        _emit(
            "warning",
            "billing.worldline_untrusted_grant_blocked token=%s kind=%s order_ref=%s",
            token[:20], kind, order_reference[:50] if order_reference else "NONE",
        )
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "invalid_callback_context"}))
        )

    try:
        result = payments.WorldlineProvider().authorize_transaction(
            token=token, save_payment_method=save_payment_method,
        )
        transaction = result.get("Transaction") if isinstance(result, dict) else {}
        transaction_status = str(transaction.get("Status") or "").upper() if isinstance(transaction, dict) else ""
        transaction_id = str(transaction.get("Id") or "") if isinstance(transaction, dict) else ""

        payment_means = result.get("PaymentMeans") if isinstance(result, dict) else {}
        card_obj = payment_means.get("Card") if isinstance(payment_means, dict) else {}
        alias_obj = card_obj.get("Alias") if isinstance(card_obj, dict) else {}
        alias_id = str(alias_obj.get("Id") or "") if isinstance(alias_obj, dict) else ""
        if not alias_id and isinstance(result, dict):
            registration_result = result.get("RegistrationResult")
            registration_alias = registration_result.get("Alias") if isinstance(registration_result, dict) else {}
            alias_id = str(registration_alias.get("Id") or "") if isinstance(registration_alias, dict) else ""
        if not alias_id and isinstance(result, dict):
            top_level_alias = result.get("Alias")
            alias_id = str(top_level_alias.get("Id") or "") if isinstance(top_level_alias, dict) else ""
        org = db.query(Organization).filter(Organization.id == int(parsed_ref["org_id"])).first()

        if alias_id and parsed_ref.get("user_id"):
            alias_owner = db.get(User, int(parsed_ref["user_id"]))
            if alias_owner is not None:
                _save_alias(db, user=alias_owner, alias_id=alias_id, card_info=_extract_card_info_from_worldline(result), org=org)
                db.flush()

        normalized_status = payment_transactions.validate_transaction_status(transaction_status)
        _emit(
            "info",
            "billing.worldline_authorize_result token=%s provider_status=%s normalized_status=%s provider_tx_id=%s",
            token[:20], transaction_status or "NONE", normalized_status,
            transaction_id[:30] if transaction_id else "NONE",
        )

        payment_method = payment_transactions._extract_payment_method_worldline(result)
        cardholder_name = payment_transactions._extract_cardholder_name_worldline(result)

        amount_chf: float = 0.01
        if kind == "subscription" and parsed_ref.get("tier"):
            tier_name = str(parsed_ref["tier"])
            billing_cycle = str(parsed_ref.get("billing_cycle") or "monthly")
            if tier_name == "custom" and org is not None:
                amount_chf = payments.compute_subscription_price_chf(
                    tier=tier_name,
                    billing_cycle=billing_cycle,  # type: ignore[arg-type]
                    custom_features=getattr(org, "custom_features", None),
                    verified_business=bool(getattr(org, "verified_business", False)),
                )
            else:
                amount_chf = round(
                    get_tier_price_chf(db, tier_name)
                    * (10.0 if billing_cycle == "yearly" else 1.0)
                    * (0.8 if org and org.verified_business else 1.0),
                    2,
                )
        elif kind == "topup" and parsed_ref.get("topup_credits"):
            amount_chf = payments.credits_to_chf(int(parsed_ref["topup_credits"]))

        billing_address = None
        if parsed_ref.get("user_id"):
            user = db.get(User, int(parsed_ref["user_id"]))
            default_address = get_default_billing_address(user.billing_address_json) if user else None
            billing_address = json.dumps(default_address) if default_address else None
        if not billing_address and org is not None:
            billing_address = getattr(org, "billing_address_json", None)

        billing_country = ""
        if billing_address:
            try:
                _addr = json.loads(billing_address)
                billing_country = str(_addr.get("country") or "") if isinstance(_addr, dict) else ""
            except (ValueError, TypeError):
                pass
        vat_rate_wb, vat_amount_chf_wb, amount_chf_total = apply_vat(
            amount_chf, billing_country, getattr(org, "vat_id", None) if org else None,
        )

        if pending_payment is not None:
            pending_payment.order_reference = order_reference or pending_payment.order_reference
            pending_payment.amount_chf = amount_chf
            pending_payment.vat_rate = vat_rate_wb
            pending_payment.vat_amount_chf = vat_amount_chf_wb
            pending_payment.kind = kind or pending_payment.kind
            pending_payment.status = normalized_status
            pending_payment.payment_method = payment_method
            pending_payment.provider_transaction_id = transaction_id or pending_payment.provider_transaction_id
            pending_payment.cardholder_name = cardholder_name
            pending_payment.billing_address = billing_address
            pending_payment.subscription_tier = str(parsed_ref["tier"]) if parsed_ref.get("tier") else pending_payment.subscription_tier
            pending_payment.subscription_billing_cycle = str(parsed_ref["billing_cycle"]) if parsed_ref.get("billing_cycle") else pending_payment.subscription_billing_cycle
            pending_payment.credits_purchased = int(parsed_ref["topup_credits"]) if parsed_ref.get("topup_credits") else pending_payment.credits_purchased
            db.commit()
            db.refresh(pending_payment)
            payment_tx = pending_payment
            _emit(
                "info",
                "billing.worldline_tx_updated token=%s tx_id=%s org_id=%s status=%s kind=%s amount_chf=%s",
                token[:20], payment_tx.id, payment_tx.org_id, payment_tx.status, payment_tx.kind, payment_tx.amount_chf,
            )
        else:
            payment_tx = payment_transactions.log_payment_transaction(
                db,
                org_id=int(parsed_ref["org_id"]),
                provider="worldline",
                external_id=token,
                order_reference=order_reference,
                amount_chf=amount_chf,
                kind=kind,
                status=normalized_status,
                payment_method=payment_method,
                provider_transaction_id=transaction_id or None,
                cardholder_name=cardholder_name,
                billing_address=billing_address,
                subscription_tier=(str(parsed_ref["tier"]) if parsed_ref.get("tier") else None),
                subscription_billing_cycle=(str(parsed_ref["billing_cycle"]) if parsed_ref.get("billing_cycle") else None),
                credits_purchased=(int(parsed_ref["topup_credits"]) if parsed_ref.get("topup_credits") else None),
                vat_rate=vat_rate_wb,
                vat_amount_chf=vat_amount_chf_wb,
            )
        _emit(
            "info",
            "billing.worldline_tx_logged token=%s tx_id=%s org_id=%s status=%s kind=%s amount_chf=%s",
            token[:20], payment_tx.id, payment_tx.org_id, payment_tx.status, payment_tx.kind, payment_tx.amount_chf,
        )

        # DOUBLE-VERIFICATION: confirm the amount Worldline actually authorized covers the
        # entitlement being granted. The entitlement is already bound to the server-trusted
        # pending transaction above; this is defense in depth against any amount/entitlement
        # drift. Worldline returns the authorized amount in minor units (cents), CHF. When
        # the field is absent we do not block — the entitlement binding is the primary control.
        if normalized_status in {"authorized", "captured"}:
            tx_amount = transaction.get("Amount") if isinstance(transaction, dict) else {}
            tx_amount = tx_amount if isinstance(tx_amount, dict) else {}
            try:
                authorized_cents = int(str(tx_amount.get("Value") or "0"))
            except (ValueError, TypeError):
                authorized_cents = 0
            authorized_currency = str(tx_amount.get("CurrencyCode") or "").upper()
            expected_cents = int(round(amount_chf_total * 100))
            # Reject only on a clear discrepancy: wrong currency, or paid materially less
            # than expected (>1% short, min 1 cent). Overpayment / rounding is tolerated.
            mismatch = authorized_cents > 0 and expected_cents > 0 and (
                (bool(authorized_currency) and authorized_currency != "CHF")
                or authorized_cents < expected_cents - max(1, int(expected_cents * 0.01))
            )
            if mismatch:
                _emit(
                    "error",
                    "billing.worldline_amount_mismatch token=%s tx_id=%s authorized=%s%s expected_cents=%s kind=%s",
                    token[:20], payment_tx.id, authorized_cents, authorized_currency or "?", expected_cents, kind,
                )
                payment_tx.status = "error"
                payment_tx.error_code = "AMOUNT_MISMATCH"
                payment_tx.error_message = (
                    f"Authorized {authorized_cents} {authorized_currency or '?'} does not cover "
                    f"expected {expected_cents} CHF cents"
                )
                db.commit()
                # Best-effort void so the customer isn't charged for a grant we refuse.
                if transaction_id:
                    try:
                        payments.WorldlineProvider().cancel_transaction(transaction_id=transaction_id)
                    except (payments.PaymentConfigurationError, RuntimeError) as _cx:
                        _emit("warning", "billing.worldline_amount_mismatch_cancel_failed token=%s error=%s", token[:20], _cx)
                return _iframe_redirect(
                    _safe_redirect_target(_append_query_params(cancel_url, {"reason": "amount_mismatch"}))
                )

        if normalized_status == "authorized" and transaction_id:
            try:
                payments.WorldlineProvider().capture_transaction(transaction_id=transaction_id)
                _emit(
                    "info",
                    "billing.worldline_capture_ok token=%s tx_id=%s provider_tx=%s",
                    token[:20], payment_tx.id, transaction_id[:20],
                )
                payment_tx.status = "captured"
                normalized_status = "captured"
                db.commit()
            except (payments.PaymentConfigurationError, RuntimeError) as cap_exc:
                _emit(
                    "warning",
                    "billing.worldline_capture_failed token=%s tx_id=%s provider_tx=%s error=%s",
                    token[:20], payment_tx.id, transaction_id[:20], cap_exc,
                )

        if normalized_status in {"authorized", "captured"}:
            _emit(
                "info",
                "billing.worldline_apply_start token=%s tx_id=%s kind=%s",
                token[:20], payment_tx.id, payment_tx.kind,
            )
            payment_transactions.apply_successful_payment(db, payment_tx)
            if kind == "subscription" and transaction_id and org is not None:
                org.recurring_transaction_id = transaction_id
                org.subscription_cancel_at_period_end = False
                db.commit()
                _emit("info", "billing.recurring_tx_stored org_id=%s tx_id=%s", org.id, transaction_id[:20])
            _emit(
                "info",
                "billing.worldline_apply_success token=%s tx_id=%s webhook_processed_at=%s credits_total_granted=%s",
                token[:20], payment_tx.id, payment_tx.webhook_processed_at, payment_tx.credits_total_granted,
            )
            return _iframe_redirect(_safe_redirect_target(success_url))

        _emit(
            "warning",
            "billing.worldline_not_success token=%s tx_id=%s normalized_status=%s redirect_reason=payment_declined",
            token[:20], payment_tx.id, normalized_status,
        )
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"}))
        )

    except payment_transactions.DuplicatePaymentError:
        _emit("warning", "billing.worldline_duplicate token=%s", token[:20])
        existing = payment_transactions.get_payment_transaction_by_external_id(db, token)
        if existing and existing.status in {"authorized", "captured"}:
            return _iframe_redirect(_safe_redirect_target(success_url))
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(cancel_url, {"reason": "payment_declined"}))
        )

    except RuntimeError as exc:
        message = str(exc)
        _emit("error", "billing.worldline_runtime_error token=%s message=%s", token[:20], message)
        logger.exception("billing.worldline_runtime_error token=%s message=%s", token[:20], message)
        if "TOKEN_INVALID" in message or "TRANSACTION_IN_WRONG_STATE" in message:
            return _iframe_redirect(
                _safe_redirect_target(_append_query_params(success_url, {"already_processed": "true"}))
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc

    except payment_transactions.PaymentValidationError as exc:
        _emit("error", "billing.worldline_validation_error token=%s message=%s", token[:20], str(exc))
        logger.exception("billing.worldline_validation_error token=%s message=%s", token[:20], str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/webhooks/worldline/card/return", response_model=None)
@router.get("/webhooks/worldline/card/return/{token}", response_model=None)
async def worldline_card_return(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
) -> HTMLResponse | RedirectResponse:
    params = request.query_params
    callback_ctx = payments.decode_worldline_callback_context(str(params.get("ctx") or "").strip())
    token = str(token or params.get("TOKEN") or params.get("token") or params.get("Token") or "").strip()
    if token in _WORLDLINE_PLACEHOLDER_TOKENS:
        token = ""

    success_url = str(callback_ctx.get("success_url") or params.get("success_url") or "").strip()
    cancel_url = str(callback_ctx.get("cancel_url") or params.get("cancel_url") or "").strip()
    order_reference = str(callback_ctx.get("order_reference") or params.get("order_reference") or "").strip()
    org_id = int(callback_ctx.get("org_id") or 0)
    user_id = int(callback_ctx.get("user_id") or 0)
    source = str(params.get("source") or "").strip().lower()
    query_string = request.url.query or ""

    _emit(
        "info",
        "billing.worldline_card_return_called source=%s token=%s ctx_valid=%s query_string=%s",
        source, token[:20] if token else "NONE", "yes" if callback_ctx else "no", query_string[:1000],
    )

    if not callback_ctx and not token:
        target = _append_query_params(cancel_url, {"reason": "invalid_callback_context"})
        return _iframe_redirect(_safe_redirect_target(target))

    if not token and order_reference:
        pending_token = payments.get_pending_card_alias_token(
            order_reference=order_reference,
            org_id=(org_id if org_id > 0 else None),
            user_id=(user_id if user_id > 0 else None),
        )
        if pending_token:
            token = pending_token
            _emit(
                "info",
                "billing.worldline_card_token_from_pending source=%s order_ref=%s token=%s",
                source, order_reference[:50], token[:20],
            )

    if not token and user_id > 0:
        owner = db.get(User, user_id)
        if owner is not None and str(owner.payment_customer_id or "").strip():
            return _iframe_redirect(
                _safe_redirect_target(_append_query_params(success_url, {"payment_method": "saved"}))
            )

    if not token:
        target = _append_query_params(cancel_url, {"reason": "missing_token"})
        return _iframe_redirect(_safe_redirect_target(target))

    try:
        if org_id <= 0:
            raise RuntimeError("Worldline alias callback missing org_id")
        if user_id <= 0:
            raise RuntimeError("Worldline alias callback missing user_id")
        owner = db.get(User, user_id)
        if owner is None:
            raise RuntimeError("User not found")
        result = payments.WorldlineProvider().wait_for_alias_registration(
            token=token, max_attempts=5, poll_interval_seconds=2,
        )
        alias = result.get("Alias") if isinstance(result, dict) else {}
        alias_id = str(alias.get("Id") or "") if isinstance(alias, dict) else ""
        payment_means = result.get("PaymentMeans") if isinstance(result, dict) else {}
        card = payment_means.get("Card") if isinstance(payment_means, dict) else {}
        display_text = str(payment_means.get("DisplayText") or "") if isinstance(payment_means, dict) else ""

        if not alias_id:
            raise RuntimeError("Worldline alias assertion did not include an alias id")

        card_reg_org = db.get(Organization, org_id) if org_id > 0 else None
        _card_scope = parse_qs(urlparse(success_url).query).get("card_scope", ["personal"])[0]
        _save_alias(db, user=owner, alias_id=alias_id, card_info=_extract_card_info_from_worldline(result), org=card_reg_org, scope=_card_scope)
        db.commit()
        if order_reference:
            payments.clear_pending_card_alias_token(order_reference=order_reference)

        _emit(
            "info",
            "billing.worldline_card_alias_saved org_id=%s alias_id=%s display_text=%s holder=%s",
            org_id, alias_id, display_text[:40] if display_text else "NONE",
            str(card.get("HolderName") or "")[:40] if isinstance(card, dict) else "NONE",
        )
        return _iframe_redirect(
            _safe_redirect_target(_append_query_params(success_url, {"payment_method": "saved"}))
        )
    except payments.PaymentConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        _emit(
            "warning",
            "billing.worldline_card_alias_failed source=%s org_id=%s user_id=%s order_ref=%s error=%s",
            source, org_id, user_id, order_reference[:50] if order_reference else "NONE", str(exc),
        )
        target = _append_query_params(cancel_url, {"reason": "alias_registration_failed"})
        return _iframe_redirect(_safe_redirect_target(target))
