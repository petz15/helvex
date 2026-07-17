"""Tests for the self-service password→JWT endpoint gate (disabled by default)."""

from app.config import settings


def test_token_endpoint_disabled_by_default(client):
    resp = client.post("/api/v1/auth/token", data={"email": "billing@example.com", "password": "x"})
    assert resp.status_code == 404


def test_token_endpoint_reachable_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_password_token_endpoint", True)
    # Reachable now → fails on bad credentials (401), not 404.
    resp = client.post("/api/v1/auth/token", data={"email": "nobody@example.com", "password": "wrong"})
    assert resp.status_code == 401
