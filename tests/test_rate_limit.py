"""Tests for the per-user/org request rate limit + anomaly detection."""

import time
from datetime import datetime, timedelta, timezone

from app import auth, crud


class _Req:
    """Minimal stand-in for a Starlette Request (headers + client)."""

    def __init__(self, headers=None, ip="1.2.3.4"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": ip})()


# ── coarse rate limit ────────────────────────────────────────────────────────

def test_disabled_flag_allows_everything(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_rate_limit_enabled", False)
    for _ in range(1000):
        allowed, _ = auth.check_request_rate(999001)
        assert allowed


def test_superadmin_never_limited(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_rate_limit_enabled", True)
    monkeypatch.setattr(auth, "_rate_limit_meta", lambda uid: (True, 7, None, None))  # superadmin
    monkeypatch.setattr(auth, "_REQ_RATE_MAX_USER", 2)
    for _ in range(10):
        allowed, _ = auth.check_request_rate(999002)
        assert allowed


def test_blocks_after_user_cap(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_rate_limit_enabled", True)
    monkeypatch.setattr(auth, "_rate_limit_meta", lambda uid: (False, 555, None, 10000))  # roomy org cap
    monkeypatch.setattr(auth, "_REQ_RATE_MAX_USER", 3)
    uid = 999003  # unique key so the in-process window isn't polluted by other tests
    results = [auth.check_request_rate(uid)[0] for _ in range(5)]
    assert results[:3] == [True, True, True]
    assert results[3] is False and results[4] is False


def test_blocks_after_org_cap(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_rate_limit_enabled", True)
    # membership-scaled org cap of 3, roomy per-user cap → org cap binds first
    monkeypatch.setattr(auth, "_rate_limit_meta", lambda uid: (False, 999044, None, 3))
    monkeypatch.setattr(auth, "_REQ_RATE_MAX_USER", 1000)
    results = [auth.check_request_rate(900000 + i)[0] for i in range(5)]
    assert results[:3] == [True, True, True]
    assert results[3] is False


def test_throttled_user_gets_lower_cap(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_rate_limit_enabled", True)
    future = time.time() + 3600
    monkeypatch.setattr(auth, "_rate_limit_meta", lambda uid: (False, 42, future, 10000))  # throttled
    monkeypatch.setattr(auth, "_THROTTLED_MAX_USER", 2)
    results = [auth.check_request_rate(999005)[0] for _ in range(4)]
    assert results[:2] == [True, True]
    assert results[2] is False  # throttled cap (2) hit before the normal 240


# ── anomaly detection ────────────────────────────────────────────────────────

def test_note_api_access_flags_script_like(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_rate_limit_enabled", True)
    monkeypatch.setattr(auth, "_SCRIPT_THRESHOLD", 3)
    flags = []
    monkeypatch.setattr(auth, "_flag_anomaly", lambda uid, reason, count, ip: flags.append((uid, reason, count)))
    req = _Req(headers={})  # no Sec-Fetch-Site → script-like
    uid = 991001
    for _ in range(3):
        auth.note_api_access(req, uid)
    assert flags and flags[0][1] == "script_access"


def test_note_api_access_browser_not_flagged(monkeypatch):
    monkeypatch.setattr(auth.settings, "api_rate_limit_enabled", True)
    monkeypatch.setattr(auth, "_SCRIPT_THRESHOLD", 3)
    flags = []
    monkeypatch.setattr(auth, "_flag_anomaly", lambda *a: flags.append(a))
    req = _Req(headers={"sec-fetch-site": "same-origin"})  # real browser
    uid = 991002
    for _ in range(50):
        auth.note_api_access(req, uid)
    assert not flags


def test_security_event_throttle_roundtrip(db):
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    crud.record_security_event(
        db, event_type="rate_burst", user_id=77, org_id=88,
        throttle_until=future, detail={"count": 1500},
    )
    assert crud.get_active_throttle_until(db, 77) is not None

    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    crud.record_security_event(db, event_type="rate_burst", user_id=78, throttle_until=past)
    assert crud.get_active_throttle_until(db, 78) is None
