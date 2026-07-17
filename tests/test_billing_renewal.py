from datetime import datetime, timedelta, timezone

from app.models.organization import Organization
from app.models.payment_transaction import PaymentTransaction
from app.services.billing.billing_renewal import run_billing_renewal


def _seed_due_paid_org(db, *, org_id: int, recurring_transaction_id: str) -> Organization:
    org = Organization(
        id=org_id,
        name=f"Org {org_id}",
        slug=f"org-{org_id}",
        subscription_billing_cycle="monthly",
        subscription_period_end=datetime.now(tz=timezone.utc) - timedelta(minutes=2),
        recurring_transaction_id=recurring_transaction_id,
    )
    org.tier = "simple"
    db.add(org)
    db.commit()
    return org


def test_billing_renewal_keeps_initial_recurring_reference(db, monkeypatch):
    org = _seed_due_paid_org(db, org_id=301, recurring_transaction_id="tx_initial_301")

    def _fake_authorize_referenced_transaction(self, *, org_id, transaction_id, amount_chf, order_reference, description):
        assert org_id == org.id
        assert transaction_id == "tx_initial_301"
        assert amount_chf > 0
        assert order_reference.startswith("wl_recur_")
        return {"Transaction": {"Id": "tx_new_301", "Status": "AUTHORIZED"}}

    def _fake_capture_transaction(self, *, transaction_id):
        assert transaction_id == "tx_new_301"
        return {"CaptureId": "cap_301"}

    monkeypatch.setattr(
        "app.services.billing.payments.WorldlineProvider.authorize_referenced_transaction",
        _fake_authorize_referenced_transaction,
    )
    monkeypatch.setattr(
        "app.services.billing.payments.WorldlineProvider.capture_transaction",
        _fake_capture_transaction,
    )

    stats = run_billing_renewal(db)
    db.refresh(org)

    assert stats["renewed"] == 1
    assert stats["failed"] == 0
    assert org.recurring_transaction_id == "tx_initial_301"


def test_billing_renewal_recovers_from_non_initial_reference_error(db, monkeypatch):
    org = _seed_due_paid_org(db, org_id=302, recurring_transaction_id="tx_bad_302")

    initial_tx = PaymentTransaction(
        org_id=org.id,
        provider="worldline",
        external_id="ext_sub_302",
        order_reference="wl_sub_302_1_simple_monthly_deadbeef",
        amount_chf=6.0,
        currency="CHF",
        kind="subscription",
        status="captured",
        provider_transaction_id="tx_initial_302",
        subscription_tier="simple",
        subscription_billing_cycle="monthly",
    )
    db.add(initial_tx)
    db.commit()

    calls = []

    def _fake_authorize_referenced_transaction(self, *, org_id, transaction_id, amount_chf, order_reference, description):
        calls.append(transaction_id)
        if transaction_id == "tx_bad_302":
            raise RuntimeError(
                "AuthorizeReferenced failed: 402 {\"Behavior\":\"DO_NOT_RETRY\",\"ErrorName\":\"ACTION_NOT_SUPPORTED\",\"ErrorDetail\":[\"The reference is not marked as initial recurring payment and therefore cannot be used map to payment means to new transaction.\"]}"
            )
        assert transaction_id == "tx_initial_302"
        return {"Transaction": {"Id": "tx_new_302", "Status": "AUTHORIZED"}}

    def _fake_capture_transaction(self, *, transaction_id):
        assert transaction_id == "tx_new_302"
        return {"CaptureId": "cap_302"}

    monkeypatch.setattr(
        "app.services.billing.payments.WorldlineProvider.authorize_referenced_transaction",
        _fake_authorize_referenced_transaction,
    )
    monkeypatch.setattr(
        "app.services.billing.payments.WorldlineProvider.capture_transaction",
        _fake_capture_transaction,
    )

    stats = run_billing_renewal(db)
    db.refresh(org)

    assert calls == ["tx_bad_302", "tx_initial_302"]
    assert stats["renewed"] == 1
    assert stats["failed"] == 0
    assert org.recurring_transaction_id == "tx_initial_302"
