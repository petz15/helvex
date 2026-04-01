import hashlib
import hmac
import json
import time

from app.auth import get_current_user
from app.main import app
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User
from app.services.payments import CheckoutSession


def _seed_org(db, *, org_id: int = 1) -> Organization:
    org = Organization(id=org_id, name=f"Org {org_id}", slug=f"org-{org_id}", tier="free")
    db.add(org)
    db.flush()
    db.add(OrgMember(org_id=org_id, user_id=1, role="owner"))
    db.commit()
    return org


def _override_user(org_id: int):
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        email="billing@example.com",
        hashed_password="x",
        is_active=True,
        tier="free",
        email_verified=True,
        is_superadmin=False,
        org_id=org_id,
        org_role="owner",
    )


def test_subscription_checkout_returns_provider_and_amount(client, db, monkeypatch):
    org = _seed_org(db, org_id=11)
    _override_user(org.id)
    monkeypatch.setattr("app.services.payments.settings.payment_provider_mode", "stripe")
    monkeypatch.setattr("app.services.payments.settings.stripe_secret_key", "sk_test_123")
    monkeypatch.setattr(
        "app.services.payments.StripeProvider._post_checkout_session",
        lambda self, form_data: CheckoutSession(provider="stripe", checkout_url="https://checkout.stripe.test/s/abc", external_id="cs_test_123"),
    )

    try:
        resp = client.post(
            "/api/v1/billing/checkout/subscription",
            json={
                "tier": "simple",
                "billing_cycle": "monthly",
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "stripe"
    assert data["amount_chf"] == 6.0
    assert data["checkout_url"]


def test_subscription_checkout_returns_service_unavailable_when_worldline_missing_config(client, db):
    org = _seed_org(db, org_id=111)
    _override_user(org.id)

    resp = client.post(
        "/api/v1/billing/checkout/subscription",
        json={
            "tier": "simple",
            "billing_cycle": "monthly",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        },
    )

    assert resp.status_code == 503
    assert "WORLDLINE_CUSTOMER_ID" in resp.json()["detail"]


def test_stripe_webhook_updates_org_subscription(client, db, monkeypatch):
    org = _seed_org(db, org_id=12)
    secret = "whsec_test"
    monkeypatch.setattr("app.services.payments.settings.stripe_webhook_secret", secret)

    payload_obj = {
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "customer": "cus_123",
                "current_period_end": int(time.time()) + 3600,
                "metadata": {
                    "org_id": str(org.id),
                    "tier": "researcher",
                    "billing_cycle": "yearly",
                },
            }
        },
    }
    payload = json.dumps(payload_obj).encode("utf-8")
    ts = str(int(time.time()))
    digest = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()

    resp = client.post(
        "/api/v1/billing/webhooks/stripe",
        data=payload,
        headers={"Stripe-Signature": f"t={ts},v1={digest}", "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    db.refresh(org)
    assert org.tier == "researcher"
    assert org.subscription_billing_cycle == "yearly"
    assert org.payment_customer_id == "cus_123"


def test_worldline_return_authorizes_and_redirects(client, db, monkeypatch):
    org = _seed_org(db, org_id=15)

    def _fake_authorize(self, *, token):
        assert token == "tok_15"
        return {
            "Transaction": {
                "Status": "AUTHORIZED",
                "Id": "tx_15",
            }
        }

    monkeypatch.setattr("app.services.payments.WorldlineProvider.authorize_transaction", _fake_authorize)

    resp = client.get(
        "/api/v1/billing/webhooks/worldline/return/tok_15",
        params={
            "kind": "topup",
            "order_reference": f"wl_topup_{org.id}_25000_deadbeef",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "source": "notify",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://example.com/success"
    db.refresh(org)
    assert org.credits_balance == 25000
