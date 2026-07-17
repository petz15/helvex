import json

from app.auth import get_current_user
from app.main import app
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.job_run import JobRun
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User
from app.services.billing.payments import CheckoutSession
from datetime import datetime, timedelta, timezone


def _seed_org(db, *, org_id: int = 1) -> Organization:
    org = Organization(id=org_id, name=f"Org {org_id}", slug=f"org-{org_id}", tier="free")
    org.billing_address_json = json.dumps(
        {
            "first_name": "Billing",
            "last_name": "User",
            "street": "Main Street",
            "number": "1",
            "postal_code": "8000",
            "city": "Zurich",
            "country": "CH",
            "company_name": "",
        }
    )
    db.add(org)
    db.flush()
    db.add(
        User(
            id=1,
            email="billing@example.com",
            hashed_password="x",
            is_active=True,
            billing_address_json=org.billing_address_json,
            email_verified=True,
            is_superadmin=False,
            org_id=org_id,
            org_role="owner",
        )
    )
    db.add(OrgMember(org_id=org_id, user_id=1, role="owner"))
    db.commit()
    return org


def _override_user(org_id: int):
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        email="billing@example.com",
        hashed_password="x",
        is_active=True,
        billing_address_json=json.dumps(
            {
                "first_name": "Billing",
                "last_name": "User",
                "street": "Main Street",
                "number": "1",
                "postal_code": "8000",
                "city": "Zurich",
                "country": "CH",
                "company_name": "",
            }
        ),
        email_verified=True,
        is_superadmin=False,
        org_id=org_id,
        org_role="owner",
    )


def _seed_pending_tx(db, *, org_id, token, order_reference, kind, credits=None, tier=None, billing_cycle=None):
    """Seed the pending PaymentTransaction that checkout creates, so the Worldline
    return handler has a server-trusted entitlement source (mirrors production)."""
    from app.services.billing import payment_transactions
    payment_transactions.log_payment_transaction(
        db,
        org_id=org_id,
        provider="worldline",
        external_id=token,
        order_reference=order_reference,
        amount_chf=1.0,  # placeholder; the return handler recomputes from the trusted reference
        kind=kind,
        status="pending",
        credits_purchased=credits,
        subscription_tier=tier,
        subscription_billing_cycle=billing_cycle,
    )


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


def test_subscription_checkout_returns_service_unavailable_when_worldline_base_url_invalid(client, db, monkeypatch):
    org = _seed_org(db, org_id=112)
    _override_user(org.id)
    monkeypatch.setattr("app.services.billing.payments.settings.payment_provider_mode", "worldline")
    monkeypatch.setattr("app.services.billing.payments.settings.worldline_customer_id", "cust-1")
    monkeypatch.setattr("app.services.billing.payments.settings.worldline_terminal_id", "term-1")
    monkeypatch.setattr("app.services.billing.payments.settings.worldline_api_username", "api-user")
    monkeypatch.setattr("app.services.billing.payments.settings.worldline_api_password", "api-pass")
    monkeypatch.setattr("app.services.billing.payments.settings.worldline_api_base_url", "payment.preprod.direct.worldline-solutions.com")

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
    assert "WORLDLINE_API_BASE_URL" in resp.json()["detail"]


def test_subscription_checkout_requires_org_admin_role(client, db):
    org = _seed_org(db, org_id=113)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=2,
        email="member@example.com",
        hashed_password="x",
        is_active=True,
        billing_address_json=None,
        email_verified=True,
        is_superadmin=False,
        org_id=org.id,
        org_role="member",
    )

    resp = client.post(
        "/api/v1/billing/checkout/subscription",
        json={
            "tier": "simple",
            "billing_cycle": "monthly",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        },
    )

    assert resp.status_code == 403


def test_worldline_return_authorizes_and_redirects(client, db, monkeypatch):
    org = _seed_org(db, org_id=15)
    monkeypatch.setattr("app.services.billing.payments.settings.app_base_url", "https://example.com")

    def _fake_authorize(self, *, token, save_payment_method=False):
        assert token == "tok_15"
        return {
            "Transaction": {
                "Status": "AUTHORIZED",
                "Id": "tx_15",
            }
        }

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.authorize_transaction", _fake_authorize)
    _seed_pending_tx(db, org_id=org.id, token="tok_15",
                     order_reference=f"wl_topup_{org.id}_25000_deadbeef", kind="topup", credits=25000)

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

    assert resp.status_code == 200
    assert "example.com/success" in resp.text
    db.refresh(org)
    assert org.credits_balance == 25000


def test_worldline_return_ignores_forged_order_reference(client, db, monkeypatch):
    """SECURITY: a caller who holds a valid token for a small real purchase cannot
    grant themselves more by forging the (unsigned) order_reference query param.
    The grant must come from the pending transaction created at checkout."""
    org = _seed_org(db, org_id=1500)
    monkeypatch.setattr("app.services.billing.payments.settings.app_base_url", "https://example.com")

    def _fake_authorize(self, *, token, save_payment_method=False):
        return {"Transaction": {"Status": "AUTHORIZED", "Id": "tx_atk"}}

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.authorize_transaction", _fake_authorize)
    # Real checkout: the user actually purchased 100 credits.
    _seed_pending_tx(db, org_id=org.id, token="tok_atk",
                     order_reference=f"wl_topup_{org.id}_1_100_realnonce", kind="topup", credits=100)

    # Attack: hit the return URL with a FORGED order_reference (25000 credits), no ctx.
    resp = client.get(
        "/api/v1/billing/webhooks/worldline/return/tok_atk",
        params={
            "kind": "topup",
            "order_reference": f"wl_topup_{org.id}_1_25000_forged",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "source": "notify",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    db.refresh(org)
    assert org.credits_balance == 100  # the real purchase, NOT the forged 25000


def test_worldline_return_rejects_amount_mismatch(client, db, monkeypatch):
    """DOUBLE-VERIFICATION: if Worldline authorized less than the entitlement's price,
    the grant is refused (and the authorization voided) even though a valid pending tx exists."""
    org = _seed_org(db, org_id=1700)
    monkeypatch.setattr("app.services.billing.payments.settings.app_base_url", "https://example.com")

    def _fake_authorize(self, *, token, save_payment_method=False):
        # Provider only authorized 1 cent — far below the price of 25000 credits.
        return {"Transaction": {"Status": "AUTHORIZED", "Id": "tx_mm", "Amount": {"Value": "1", "CurrencyCode": "CHF"}}}

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.authorize_transaction", _fake_authorize)
    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.cancel_transaction",
                        lambda self, *, transaction_id: {})
    _seed_pending_tx(db, org_id=org.id, token="tok_mm",
                     order_reference=f"wl_topup_{org.id}_1_25000_nonce", kind="topup", credits=25000)

    resp = client.get(
        "/api/v1/billing/webhooks/worldline/return/tok_mm",
        params={
            "kind": "topup",
            "order_reference": f"wl_topup_{org.id}_1_25000_nonce",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "source": "notify",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "amount_mismatch" in resp.text
    db.refresh(org)
    assert org.credits_balance == 0  # grant refused


def test_worldline_return_blocks_grant_without_trusted_context(client, db, monkeypatch):
    """SECURITY: a token with no pending transaction and no signed ctx cannot grant
    entitlement from unsigned query params."""
    org = _seed_org(db, org_id=1600)
    monkeypatch.setattr("app.services.billing.payments.settings.app_base_url", "https://example.com")

    def _fake_authorize(self, *, token, save_payment_method=False):
        return {"Transaction": {"Status": "AUTHORIZED", "Id": "tx_notrust"}}

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.authorize_transaction", _fake_authorize)

    resp = client.get(
        "/api/v1/billing/webhooks/worldline/return/tok_notrust",
        params={
            "kind": "topup",
            "order_reference": f"wl_topup_{org.id}_1_25000_forged",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "source": "notify",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    db.refresh(org)
    assert org.credits_balance == 0  # no pending tx + no signed ctx → no grant


def test_worldline_return_persists_alias_from_checkout_authorize(client, db, monkeypatch):
    org = _seed_org(db, org_id=19)
    monkeypatch.setattr("app.services.billing.payments.settings.app_base_url", "https://example.com")

    def _fake_authorize(self, *, token, save_payment_method=False):
        assert token == "tok_19"
        return {
            "Transaction": {
                "Status": "AUTHORIZED",
                "Id": "tx_19",
            },
            "PaymentMeans": {
                "Card": {
                    "Alias": {"Id": "alias_from_checkout_1"},
                }
            },
        }

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.authorize_transaction", _fake_authorize)
    _seed_pending_tx(db, org_id=org.id, token="tok_19",
                     order_reference=f"wl_topup_{org.id}_1_25000_deadbeef", kind="topup", credits=25000)

    resp = client.get(
        "/api/v1/billing/webhooks/worldline/return/tok_19",
        params={
            "kind": "topup",
            "order_reference": f"wl_topup_{org.id}_1_25000_deadbeef",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "source": "notify",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    saved_user = db.get(User, 1)
    assert saved_user is not None
    assert saved_user.payment_customer_id == "alias_from_checkout_1"


def test_worldline_return_persists_alias_from_registration_result(client, db, monkeypatch):
    org = _seed_org(db, org_id=119)
    monkeypatch.setattr("app.services.billing.payments.settings.app_base_url", "https://example.com")

    def _fake_authorize(self, *, token, save_payment_method=False):
        assert token == "tok_119"
        return {
            "Transaction": {
                "Status": "AUTHORIZED",
                "Id": "tx_119",
            },
            "RegistrationResult": {
                "Alias": {"Id": "alias_from_registration_result_1"},
            },
        }

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.authorize_transaction", _fake_authorize)
    _seed_pending_tx(db, org_id=org.id, token="tok_119",
                     order_reference=f"wl_topup_{org.id}_1_25000_deadbeef", kind="topup", credits=25000)

    resp = client.get(
        "/api/v1/billing/webhooks/worldline/return/tok_119",
        params={
            "kind": "topup",
            "order_reference": f"wl_topup_{org.id}_1_25000_deadbeef",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "source": "notify",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    saved_user = db.get(User, 1)
    assert saved_user is not None
    assert saved_user.payment_customer_id == "alias_from_registration_result_1"


def test_worldline_card_registration_saves_alias(client, db, monkeypatch):
    org = _seed_org(db, org_id=16)
    _override_user(org.id)

    def _fake_register(self, *, org_id, user_id, success_url, cancel_url, billing_address):
        assert org_id == org.id
        assert user_id == 1
        assert success_url == "https://example.com/success?card_scope=personal"
        assert cancel_url == "https://example.com/cancel?card_scope=personal"
        return CheckoutSession(provider="worldline", checkout_url="https://payment.preprod.worldline/alias_tok_1", external_id="alias_tok_1", order_reference="wl_alias_16_1_test")

    def _fake_assert(self, *, token):
        assert token == "alias_tok_1"
        return {
            "Alias": {"Id": "alias_123"},
            "PaymentMeans": {"Card": {"HolderName": "Max Mustermann"}, "DisplayText": "**** 1234"},
        }

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.create_card_alias_registration", _fake_register)
    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.assert_alias_insert", _fake_assert)

    resp = client.post(
        "/api/v1/billing/payment-methods/worldline/register",
        json={
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["external_id"] == "alias_tok_1"
    db.refresh(org)
    saved_user = db.get(User, 1)
    assert saved_user is not None
    assert saved_user.payment_customer_id is None


def test_worldline_card_return_saves_alias(client, db, monkeypatch):
    org = _seed_org(db, org_id=17)
    monkeypatch.setattr("app.services.billing.payments.settings.app_base_url", "https://example.com")

    def _fake_wait(self, *, token, max_attempts=15, poll_interval_seconds=60):
        assert token == "alias_tok_1"
        return {
            "Alias": {"Id": "alias_123"},
            "PaymentMeans": {"Card": {"HolderName": "Max Mustermann"}, "DisplayText": "**** 1234"},
        }

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.wait_for_alias_registration", _fake_wait)

    from app.services.billing import payments as payments_module

    ctx = payments_module.create_worldline_callback_context(
        org_id=org.id,
        user_id=1,
        kind="alias",
        order_reference="wl_alias_17_1_test",
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )

    resp = client.get(
        "/api/v1/billing/webhooks/worldline/card/return/alias_tok_1",
        params={
            "ctx": ctx,
            "source": "return",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "example.com/success" in resp.text
    saved_user = db.get(User, 1)
    assert saved_user is not None
    assert saved_user.payment_customer_id == "alias_123"


def test_update_default_payment_user_selects_saved_card_owner(client, db, monkeypatch):
    org = _seed_org(db, org_id=18)
    _override_user(org.id)

    saved_user = User(
        id=2,
        email="saved@example.com",
        hashed_password="x",
        is_active=True,
        billing_address_json=org.billing_address_json,
        email_verified=True,
        is_superadmin=False,
        org_id=org.id,
        org_role="admin",
        payment_customer_id="alias_saved_1",
    )
    db.add(saved_user)
    db.add(OrgMember(org_id=org.id, user_id=2, role="admin"))
    db.commit()

    resp = client.put(
        f"/api/v1/orgs/{org.id}/default-payment-user",
        json={"user_id": 2},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["default_payment_user_id"] == 2
    db.refresh(org)
    assert org.default_payment_user_id == 2


def test_topup_checkout_prefers_current_users_saved_alias_over_org_default(client, db, monkeypatch):
    org = _seed_org(db, org_id=19)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        email="billing@example.com",
        hashed_password="x",
        is_active=True,
        billing_address_json=org.billing_address_json,
        email_verified=True,
        is_superadmin=False,
        org_id=org.id,
        org_role="owner",
        payment_customer_id="alias_current_1",
    )

    current_user = db.get(User, 1)
    assert current_user is not None
    current_user.payment_customer_id = "alias_current_1"

    saved_user = User(
        id=2,
        email="saved@example.com",
        hashed_password="x",
        is_active=True,
        billing_address_json=org.billing_address_json,
        email_verified=True,
        is_superadmin=False,
        org_id=org.id,
        org_role="admin",
        payment_customer_id="alias_org_default_1",
    )
    db.add(saved_user)
    db.add(OrgMember(org_id=org.id, user_id=2, role="admin"))
    org.default_payment_user_id = 2
    db.commit()

    captured = {}

    def _fake_create_topup_checkout(*, org_id, user_id, payment_alias_id, save_payment_method, credits, success_url, cancel_url, billing_address, preferred_provider=None, amount_chf=None, use_payment_page=False):
        captured["org_id"] = org_id
        captured["user_id"] = user_id
        captured["payment_alias_id"] = payment_alias_id
        captured["save_payment_method"] = save_payment_method
        return CheckoutSession(provider="worldline", checkout_url="https://payment.preprod.worldline/tok_19", external_id="tok_19", order_reference="wl_topup_19_1_10000_deadbeef")

    monkeypatch.setattr("app.services.billing.payments.create_topup_checkout", _fake_create_topup_checkout)

    resp = client.post(
        "/api/v1/billing/checkout/topup",
        json={
            "credits": 10000,
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
            "save_payment_method": False,
        },
    )

    assert resp.status_code == 200
    assert captured["payment_alias_id"] == "alias_current_1"
    assert captured["save_payment_method"] is False


def test_topup_checkout_rejects_amounts_below_100_credits(client, db):
    org = _seed_org(db, org_id=191)
    _override_user(org.id)

    resp = client.post(
        "/api/v1/billing/checkout/topup",
        json={
            "credits": 99,
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        },
    )

    assert resp.status_code == 422


def test_jobs_list_scoped_to_current_org_and_user(client, db):
    org = _seed_org(db, org_id=24)
    _override_user(org.id)

    visible_org_job = JobRun(job_type="bulk", label="Org job", status="queued", org_id=org.id, user_id=1)
    visible_user_job = JobRun(job_type="csv_export", label="User-only export", status="completed", org_id=None, user_id=1)
    hidden_other_org_job = JobRun(job_type="bulk", label="Other org job", status="queued", org_id=999, user_id=2)
    hidden_unscoped_job = JobRun(job_type="catalog", label="Superadmin catalog", status="completed", org_id=None, user_id=None)

    db.add(visible_org_job)
    db.add(visible_user_job)
    db.add(hidden_other_org_job)
    db.add(hidden_unscoped_job)
    db.commit()

    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    labels = [item["label"] for item in resp.json()]

    assert "Org job" in labels
    assert "User-only export" in labels
    assert "Other org job" not in labels
    assert "Superadmin catalog" not in labels


def test_job_detail_returns_404_for_job_outside_scope(client, db):
    org = _seed_org(db, org_id=25)
    _override_user(org.id)

    hidden_job = JobRun(job_type="bulk", label="Hidden job", status="queued", org_id=999, user_id=2)
    db.add(hidden_job)
    db.commit()

    resp = client.get(f"/api/v1/jobs/{hidden_job.id}")
    assert resp.status_code == 404


def test_cancel_pending_payment_marks_declined(client, db):
    org = _seed_org(db, org_id=20)
    _override_user(org.id)

    tx = PaymentTransaction(
        org_id=org.id,
        provider="worldline",
        external_id="tok_cancel_20",
        order_reference="wl_topup_20_1_10000_deadbeef",
        amount_chf=1.0,
        currency="CHF",
        kind="topup",
        status="pending",
    )
    db.add(tx)
    db.commit()

    resp = client.post(f"/api/v1/billing/payments/{tx.id}/cancel")
    assert resp.status_code == 200

    history = client.get("/api/v1/billing/payments")
    assert history.status_code == 200
    first = history.json()["items"][0]
    assert first["decline_reason"] == "Cancelled by user"

    db.refresh(tx)
    assert tx.status == "declined"
    assert tx.error_code == "MANUAL_CANCELLED"


def test_cancel_pending_payment_triggers_worldline_cancel_when_provider_tx_id_present(client, db, monkeypatch):
    org = _seed_org(db, org_id=22)
    _override_user(org.id)

    called = {}

    def _fake_cancel(self, *, transaction_id):
        called["transaction_id"] = transaction_id
        return {
            "ResponseHeader": {"RequestId": "wl_cancel_test_22"},
            "TransactionId": transaction_id,
            "Date": "2026-04-02T12:00:00.000+01:00",
        }

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.cancel_transaction", _fake_cancel)

    tx = PaymentTransaction(
        org_id=org.id,
        provider="worldline",
        external_id="tok_cancel_22",
        order_reference="wl_topup_22_1_10000_deadbeef",
        amount_chf=1.0,
        currency="CHF",
        kind="topup",
        status="pending",
        provider_transaction_id="wl_provider_tx_22",
    )
    db.add(tx)
    db.commit()

    resp = client.post(f"/api/v1/billing/payments/{tx.id}/cancel")
    assert resp.status_code == 200
    assert called["transaction_id"] == "wl_provider_tx_22"

    db.refresh(tx)
    assert tx.status == "declined"
    assert tx.error_code == "MANUAL_CANCELLED"


def test_payment_history_expires_stale_pending_after_15_min(client, db):
    org = _seed_org(db, org_id=21)
    _override_user(org.id)

    stale = PaymentTransaction(
        org_id=org.id,
        provider="worldline",
        external_id="tok_stale_21",
        order_reference="wl_topup_21_1_10000_deadbeef",
        amount_chf=1.0,
        currency="CHF",
        kind="topup",
        status="pending",
        created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=20),
    )
    db.add(stale)
    db.commit()

    resp = client.get("/api/v1/billing/payments")
    assert resp.status_code == 200

    db.refresh(stale)
    assert stale.status == "declined"
    assert stale.error_code == "PENDING_TIMEOUT"


def test_payment_history_expiration_triggers_worldline_cancel_when_provider_tx_id_present(client, db, monkeypatch):
    org = _seed_org(db, org_id=23)
    _override_user(org.id)

    called = {}

    def _fake_cancel(self, *, transaction_id):
        called["transaction_id"] = transaction_id
        return {
            "ResponseHeader": {"RequestId": "wl_cancel_test_23"},
            "TransactionId": transaction_id,
            "Date": "2026-04-02T12:00:00.000+01:00",
        }

    monkeypatch.setattr("app.services.billing.payments.WorldlineProvider.cancel_transaction", _fake_cancel)

    stale = PaymentTransaction(
        org_id=org.id,
        provider="worldline",
        external_id="tok_stale_23",
        order_reference="wl_topup_23_1_10000_deadbeef",
        amount_chf=1.0,
        currency="CHF",
        kind="topup",
        status="pending",
        provider_transaction_id="wl_provider_tx_23",
        created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=20),
    )
    db.add(stale)
    db.commit()

    resp = client.get("/api/v1/billing/payments")
    assert resp.status_code == 200
    assert called["transaction_id"] == "wl_provider_tx_23"

    db.refresh(stale)
    assert stale.status == "declined"
    assert stale.error_code == "PENDING_TIMEOUT"
