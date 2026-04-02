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
    assert captured["json"]["RedirectNotifyUrls"]["Success"].endswith("source=notify")
    assert captured["auth"] == ("wl_key", "wl_pwd")


def test_parse_worldline_merchant_reference_subscription():
    parsed = payments.parse_worldline_merchant_reference("wl_sub_42_researcher_yearly_deadbeef")
    assert parsed["kind"] == "sub"
    assert parsed["org_id"] == 42
    assert parsed["tier"] == "researcher"
    assert parsed["billing_cycle"] == "yearly"


