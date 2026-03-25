from unittest.mock import patch

import pytest

import app.auth as auth


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    # In-memory rate limiting state is module-global; reset between tests.
    auth._request_counts.clear()
    auth._login_attempts.clear()
    yield
    auth._request_counts.clear()
    auth._login_attempts.clear()


def test_register_creates_user(client):
    # Avoid sending real emails during tests
    with patch("app.api.routes.auth.send_verification_email", return_value=None):
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "newuser", "email": "newuser@example.com", "password": "long-enough"},
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "newuser"
    assert data["email"] == "newuser@example.com"


def test_register_rate_limit_is_per_forwarded_ip(client):
    with patch("app.api.routes.auth.send_verification_email", return_value=None):
        # Hit limit for IP A
        for i in range(10):
            resp = client.post(
                "/api/v1/auth/register",
                json={
                    "username": f"usera{i}",
                    "email": f"usera{i}@example.com",
                    "password": "long-enough",
                },
                headers={"X-Forwarded-For": "203.0.113.1"},
            )
            assert resp.status_code == 201

        resp_blocked = client.post(
            "/api/v1/auth/register",
            json={"username": "usera10", "email": "usera10@example.com", "password": "long-enough"},
            headers={"X-Forwarded-For": "203.0.113.1"},
        )
        assert resp_blocked.status_code == 429

        # Different IP should still be allowed
        resp_other_ip = client.post(
            "/api/v1/auth/register",
            json={"username": "userb0", "email": "userb0@example.com", "password": "long-enough"},
            headers={"X-Forwarded-For": "203.0.113.2"},
        )
        assert resp_other_ip.status_code == 201


def test_register_does_not_500_if_record_verification_sent_fails(client):
    with patch("app.api.routes.auth.crud.record_verification_sent", side_effect=Exception("db error")):
        with patch("app.api.routes.auth.send_verification_email", return_value=None):
            resp = client.post(
                "/api/v1/auth/register",
                json={"username": "dbfail", "email": "dbfail@example.com", "password": "long-enough"},
                headers={"X-Forwarded-For": "203.0.113.55"},
            )
    assert resp.status_code == 201
    assert resp.json()["username"] == "dbfail"
