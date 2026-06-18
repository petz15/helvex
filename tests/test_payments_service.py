from app.services import payments


def test_enabled_provider_order_defaults_worldline(monkeypatch):
    monkeypatch.setattr(payments.settings, "payment_provider_mode", "")
    assert payments.get_enabled_provider_order() == ["worldline"]


def test_enabled_provider_order_dual(monkeypatch):
    monkeypatch.setattr(payments.settings, "payment_provider_mode", "dual")
    assert payments.get_enabled_provider_order() == ["worldline", "stripe"]


def test_enabled_provider_order_stripe(monkeypatch):
    monkeypatch.setattr(payments.settings, "payment_provider_mode", "stripe")
    assert payments.get_enabled_provider_order() == ["stripe"]


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_stripe_subscription_checkout_calls_api(monkeypatch):
    captured = {}

    def _fake_post(self, url, data=None, headers=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return _FakeResponse(200, {"id": "cs_test_1", "url": "https://checkout.stripe.test/s/cs_test_1"})

    monkeypatch.setattr(payments.settings, "payment_provider_mode", "stripe")
    monkeypatch.setattr(payments.settings, "stripe_secret_key", "sk_test_123")
    monkeypatch.setattr(payments.httpx.Client, "post", _fake_post)

    out = payments.create_subscription_checkout(
        org_id=42,
        tier="simple",
        billing_cycle="monthly",
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    assert out.provider == "stripe"
    assert out.external_id == "cs_test_1"
    assert out.checkout_url.startswith("https://checkout.stripe.test/")
    assert captured["url"].endswith("/v1/checkout/sessions")
    assert captured["data"]["mode"] == "subscription"
    assert captured["data"]["metadata[org_id]"] == "42"


def test_worldline_topup_checkout_calls_api(monkeypatch):
    captured = {}

    def _fake_post(self, url, headers=None, json=None, auth=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["auth"] = auth
        return _FakeResponse(200, {"Token": "tok_1", "RedirectUrl": "https://payment.preprod.worldline/tok_1"})

    monkeypatch.setattr(payments.settings, "payment_provider_mode", "worldline")
    monkeypatch.setattr(payments.settings, "worldline_customer_id", "customer_test")
    monkeypatch.setattr(payments.settings, "worldline_terminal_id", "12345678")
    monkeypatch.setattr(payments.settings, "worldline_api_username", "wl_key")
    monkeypatch.setattr(payments.settings, "worldline_api_password", "wl_pwd")
    monkeypatch.setattr(payments.httpx.Client, "post", _fake_post)

    out = payments.create_topup_checkout(
        org_id=7,
        payment_alias_id="alias_saved_1",
        credits=25000,
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    assert out.provider == "worldline"
    assert out.external_id == "tok_1"
    assert out.checkout_url.endswith("tok_1")
    assert captured["url"].endswith("/Payment/v1/Transaction/Initialize")
    assert captured["json"]["RequestHeader"]["CustomerId"] == "customer_test"
    assert captured["json"]["TerminalId"] == "12345678"
    assert captured["json"]["Payment"]["OrderId"].startswith("wl_topup_7_25000_")
    assert captured["json"]["PaymentMeans"]["Alias"]["Id"] == "alias_saved_1"
    assert captured["json"]["RedirectNotifyUrls"]["Success"].endswith("source=notify")
    assert captured["auth"] == ("wl_key", "wl_pwd")


def test_worldline_topup_checkout_registers_alias_when_requested(monkeypatch):
    captured = {}

    def _fake_post(self, url, headers=None, json=None, auth=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200, {"Token": "tok_2", "RedirectUrl": "https://payment.preprod.worldline/tok_2"})

    monkeypatch.setattr(payments.settings, "payment_provider_mode", "worldline")
    monkeypatch.setattr(payments.settings, "worldline_customer_id", "customer_test")
    monkeypatch.setattr(payments.settings, "worldline_terminal_id", "12345678")
    monkeypatch.setattr(payments.settings, "worldline_api_username", "wl_key")
    monkeypatch.setattr(payments.settings, "worldline_api_password", "wl_pwd")
    monkeypatch.setattr(payments.httpx.Client, "post", _fake_post)

    out = payments.create_topup_checkout(
        org_id=7,
        credits=15000,
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
        save_payment_method=True,
    )

    assert out.external_id == "tok_2"
    assert captured["url"].endswith("/Payment/v1/Transaction/Initialize")
    # save_payment_method is encoded in the callback ctx URL, not in the Initialize payload
    assert "RegisterAlias" not in captured["json"]


def test_worldline_card_alias_registration_calls_api(monkeypatch):
    captured = {}

    def _fake_post(self, url, headers=None, json=None, auth=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["auth"] = auth
        if url.endswith("/Alias/Insert"):
            return _FakeResponse(200, {"Token": "alias_tok_1", "Redirect": {"RedirectUrl": "https://payment.preprod.worldline/alias_tok_1"}})
        return _FakeResponse(200, {"Alias": {"Id": "alias_123"}, "PaymentMeans": {"Card": {"HolderName": "Max Mustermann"}, "DisplayText": "**** 1234"}})

    monkeypatch.setattr(payments.settings, "payment_provider_mode", "worldline")
    monkeypatch.setattr(payments.settings, "worldline_customer_id", "customer_test")
    monkeypatch.setattr(payments.settings, "worldline_terminal_id", "12345678")
    monkeypatch.setattr(payments.settings, "worldline_api_username", "wl_key")
    monkeypatch.setattr(payments.settings, "worldline_api_password", "wl_pwd")
    monkeypatch.setattr(payments.httpx.Client, "post", _fake_post)

    out = payments.WorldlineProvider().create_card_alias_registration(
        org_id=7,
        user_id=9,
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )

    assert out.provider == "worldline"
    assert out.external_id == "alias_tok_1"
    assert out.checkout_url.endswith("alias_tok_1")
    assert captured["url"].endswith("/Payment/v1/Alias/Insert")
    assert captured["json"]["RequestHeader"]["CustomerId"] == "customer_test"
    assert captured["json"]["Type"] == "CARD"
    assert captured["json"]["RegisterAlias"]["IdGenerator"] == "RANDOM"


def test_worldline_alias_assertion_calls_api(monkeypatch):
    captured = {}

    def _fake_post(self, url, headers=None, json=None, auth=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["auth"] = auth
        return _FakeResponse(200, {"Alias": {"Id": "alias_123"}, "PaymentMeans": {"Card": {"HolderName": "Max Mustermann"}}})

    monkeypatch.setattr(payments.settings, "payment_provider_mode", "worldline")
    monkeypatch.setattr(payments.settings, "worldline_customer_id", "customer_test")
    monkeypatch.setattr(payments.settings, "worldline_terminal_id", "12345678")
    monkeypatch.setattr(payments.settings, "worldline_api_username", "wl_key")
    monkeypatch.setattr(payments.settings, "worldline_api_password", "wl_pwd")
    monkeypatch.setattr(payments.httpx.Client, "post", _fake_post)

    out = payments.WorldlineProvider().assert_alias_insert(token="alias_tok_1")

    assert out["Alias"]["Id"] == "alias_123"
    assert captured["url"].endswith("/Payment/v1/Alias/AssertInsert")
    assert captured["json"]["Token"] == "alias_tok_1"


def test_worldline_transaction_cancel_calls_api(monkeypatch):
    captured = {}

    def _fake_post(self, url, headers=None, json=None, auth=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["auth"] = auth
        return _FakeResponse(
            200,
            {
                "ResponseHeader": {"RequestId": "wl_cancel_test_1"},
                "TransactionId": "tx_123",
                "Date": "2026-04-02T12:00:00.000+01:00",
            },
        )

    monkeypatch.setattr(payments.settings, "payment_provider_mode", "worldline")
    monkeypatch.setattr(payments.settings, "worldline_customer_id", "customer_test")
    monkeypatch.setattr(payments.settings, "worldline_terminal_id", "12345678")
    monkeypatch.setattr(payments.settings, "worldline_api_username", "wl_key")
    monkeypatch.setattr(payments.settings, "worldline_api_password", "wl_pwd")
    monkeypatch.setattr(payments.httpx.Client, "post", _fake_post)

    out = payments.WorldlineProvider().cancel_transaction(transaction_id="tx_123")

    assert out["TransactionId"] == "tx_123"
    assert captured["url"].endswith("/Payment/v1/Transaction/Cancel")
    assert captured["json"]["TransactionReference"]["TransactionId"] == "tx_123"


def test_parse_worldline_merchant_reference_subscription():
    parsed = payments.parse_worldline_merchant_reference("wl_sub_42_researcher_yearly_deadbeef")
    assert parsed["kind"] == "sub"
    assert parsed["org_id"] == 42
    assert parsed["tier"] == "researcher"
    assert parsed["billing_cycle"] == "yearly"


