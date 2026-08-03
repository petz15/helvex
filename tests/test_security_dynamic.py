"""Dynamic security tests — real requests through the full middleware stack.

These exercise the app end to end (auth_gate middleware, dependency graph,
route handlers) rather than calling functions directly, so they catch gaps that
unit tests structurally cannot: a route that forgets its auth dependency, a
public-prefix match that swallows a protected path, an authorization decision
served from a stale cache.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.auth import clear_user_cache, set_session_cookie
from app.main import app
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User


@pytest.fixture
def client(db):
    """A client with the REAL auth stack.

    Deliberately unlike conftest's `client`, which overrides get_current_user
    and so cannot exercise authentication or authorization at all. Startup is
    patched the same way (no prod DB, no worker thread); only the auth bypass
    is removed.
    """
    import contextlib
    from tests.conftest import _STARTUP_PATCHES
    from app.database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    clear_user_cache()
    with contextlib.ExitStack() as stack:
        for p in _STARTUP_PATCHES:
            stack.enter_context(p)
        with TestClient(app) as c:
            app.state.ready = True
            app.state.startup_error = None
            app.state.startup_message = "Ready"
            app.state.disable_job_worker = True
            from app.config import settings as _settings
            _settings.api_rate_limit_enabled = False
            yield c
    app.dependency_overrides.clear()
    clear_user_cache()


def _mk_user(db, email: str, *, superadmin: bool = False, org: Organization | None = None) -> User:
    u = User(
        email=email,
        hashed_password=crud.hash_password("Str0ngPassw0rd!"),
        is_active=True,
        is_superadmin=superadmin,
        email_verified=True,
        org_id=org.id if org else None,
    )
    db.add(u)
    db.flush()
    if org is not None:
        db.add(OrgMember(org_id=org.id, user_id=u.id, role="owner"))
    db.commit()
    db.refresh(u)
    return u


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _mk_org(db, name: str) -> Organization:
    o = Organization(name=name, slug=name.lower().replace(" ", "-"))
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def _revoke(fn) -> None:
    """Apply an identity mutation in its OWN session, as a real admin request
    would. Reusing the victim's session hides the bug: the cache expunges the
    very object the test then mutates, so no UPDATE is ever emitted."""
    from tests.conftest import TestingSessionLocal
    s = TestingSessionLocal()
    try:
        fn(s)
        s.commit()
    finally:
        s.close()


def _login(client: TestClient, user: User) -> None:
    """Attach a valid session cookie for `user` to the client."""
    from app.auth import COOKIE_NAME, create_session_cookie
    client.cookies.set(COOKIE_NAME, create_session_cookie(user.id))


# ── auth_gate: unauthenticated access ─────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/v1/companies",
    "/api/v1/jobs",
    "/api/v1/admin/analytics",
    "/api/v1/auth/me",
    "/api/v1/auth/me/billing-addresses",   # under the public /api/v1/auth prefix
])
def test_protected_endpoints_reject_anonymous(client, path):
    """The /api/v1/auth prefix bypasses auth_gate, so routes beneath it must
    carry their own dependency. This proves they do."""
    r = client.get(path)
    assert r.status_code in (401, 403), f"{path} returned {r.status_code} to an anonymous caller"


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


# ── Privilege revocation must take effect immediately ─────────────────────────

def test_deactivation_takes_effect_on_the_next_request(client, db):
    """Regression: the 30s user cache served is_active/is_superadmin/org_id, and
    was only evicted on logout — so a deactivated account kept working."""
    user = _mk_user(db, "victim@example.com")
    _login(client, user)
    assert client.get("/api/v1/auth/me").status_code == 200

    def deactivate(s):
        s.get(User, user.id).is_active = False
    _revoke(deactivate)              # mapper event evicts the cache

    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401, "deactivated user still authenticated from cache"


def test_superadmin_revocation_takes_effect_on_the_next_request(client, db):
    admin = _mk_user(db, "admin@example.com", superadmin=True)
    _login(client, admin)
    assert client.get("/api/v1/admin/analytics").status_code == 200

    def demote(s):
        s.get(User, admin.id).is_superadmin = False
    _revoke(demote)

    r = client.get("/api/v1/admin/analytics")
    assert r.status_code in (401, 403), "demoted admin still had superadmin access"


def test_org_move_takes_effect_on_the_next_request(client, db):
    org_a, org_b = _mk_org(db, "Org A"), _mk_org(db, "Org B")
    user = _mk_user(db, "mover@example.com", org=org_a)
    _login(client, user)

    r = client.get(f"/api/v1/orgs/{org_a.id}/companies/1/state")
    assert r.status_code != 403, "owner denied access to their own org"

    def move(s):
        s.get(User, user.id).org_id = org_b.id
    _revoke(move)

    # Denial, not a specific code: get_current_org rejects before the path-vs-org
    # comparison runs (the user has no OrgMember row in their new org), which
    # surfaces as 404 and leaks less than a 403 would. What matters is that the
    # move takes effect on the very next request rather than after the cache TTL.
    r = client.get(f"/api/v1/orgs/{org_a.id}/companies/1/state")
    assert r.status_code in (401, 403, 404), (
        f"user still reached the old org after being moved (got {r.status_code})"
    )


def test_membership_change_evicts_the_cache(client, db):
    """OrgMember writes must evict too — role lives there, not on User."""
    from app.auth import _user_cache
    org = _mk_org(db, "Org C")
    user = _mk_user(db, "member@example.com", org=org)
    _login(client, user)
    client.get("/api/v1/auth/me")
    assert user.id in _user_cache

    def demote_member(s):
        s.query(OrgMember).filter(OrgMember.user_id == user.id).first().role = "viewer"
    _revoke(demote_member)

    assert user.id not in _user_cache, "OrgMember change did not evict the user cache"


# ── Cross-tenant isolation ────────────────────────────────────────────────────

def test_cannot_read_another_orgs_workspace_state(client, db):
    org_a, org_b = _mk_org(db, "Tenant A"), _mk_org(db, "Tenant B")
    attacker = _mk_user(db, "attacker@example.com", org=org_a)
    _login(client, attacker)

    r = client.get(f"/api/v1/orgs/{org_b.id}/companies/1/state")
    assert r.status_code == 403, "cross-tenant read was allowed"


def test_cannot_write_another_orgs_workspace_state(client, db):
    org_a, org_b = _mk_org(db, "Tenant A2"), _mk_org(db, "Tenant B2")
    attacker = _mk_user(db, "attacker2@example.com", org=org_a)
    _login(client, attacker)

    r = client.patch(
        f"/api/v1/orgs/{org_b.id}/companies/1/state",
        json={"review_status": "owned"},
    )
    assert r.status_code in (403, 404), "cross-tenant write was allowed"


def test_non_superadmin_cannot_scope_analytics_to_another_org(client, db):
    """org_id is accepted as a query param; it must be ignored for non-admins."""
    org_a, org_b = _mk_org(db, "Tenant A3"), _mk_org(db, "Tenant B3")
    user = _mk_user(db, "scoper@example.com", org=org_a)
    _login(client, user)

    own = client.get("/api/v1/companies/taxonomy")
    spoofed = client.get(f"/api/v1/companies/taxonomy?org_id={org_b.id}")
    assert own.status_code == spoofed.status_code == 200
    assert own.json() == spoofed.json(), "client-supplied org_id changed a non-admin's scope"


# ── Admin surface ─────────────────────────────────────────────────────────────

def test_regular_user_cannot_reach_admin_routes(client, db):
    user = _mk_user(db, "plain@example.com")
    _login(client, user)
    for path in ("/api/v1/admin/analytics", "/api/v1/admin/users", "/api/v1/admin/orgs"):
        r = client.get(path)
        assert r.status_code in (401, 403), f"{path} reachable by a non-superadmin"


def test_regular_user_cannot_grant_credits(client, db):
    org = _mk_org(db, "Credit Target")
    user = _mk_user(db, "greedy@example.com", org=org)
    _login(client, user)

    r = client.post(f"/api/v1/admin/orgs/{org.id}/credits", json={"amount": 100000, "reason": "x"})
    assert r.status_code in (401, 403), "non-superadmin granted credits"


# ── Session integrity ─────────────────────────────────────────────────────────

def test_forged_session_cookie_is_rejected(client, db):
    from app.auth import COOKIE_NAME
    _mk_user(db, "real@example.com")
    client.cookies.set(COOKIE_NAME, "1|forged|nonsense")
    assert client.get("/api/v1/auth/me").status_code in (401, 403)


def test_session_cookie_is_httponly_and_samesite(client, db):
    from starlette.responses import Response
    r = Response()
    set_session_cookie(r, 1, is_https=True)
    cookie = r.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "secure" in cookie


# ── Invite revocation ─────────────────────────────────────────────────────────

def test_removed_member_cannot_replay_their_invite(client, db):
    """Invites are stateless 7-day signed tokens with no DB record, so deleting
    the OrgMember row alone left the original link usable to rejoin at the same
    role. `org_membership_revoked_at` is what closes that."""
    from app.auth import create_invite_token
    org = _mk_org(db, "Replay Org")
    victim = _mk_user(db, "removed@example.com", org=org)
    token = create_invite_token(org.id, victim.email, "admin")
    _login(client, victim)

    # Mutate on the request session: conftest binds a StaticPool (one shared
    # SQLite connection), so a second Session does not give real isolation here.
    db.query(OrgMember).filter(
        OrgMember.org_id == org.id, OrgMember.user_id == victim.id
    ).delete()
    db.get(User, victim.id).org_membership_revoked_at = _now()
    db.commit()
    db.expire_all()   # next read re-queries, as a fresh request session would

    r = client.post("/api/v1/invites/accept", json={"token": token})
    assert r.status_code == 400, "removed member replayed their invite and rejoined"

    fresh = db.query(OrgMember).filter(
        OrgMember.org_id == org.id, OrgMember.user_id == victim.id
    ).first()
    assert fresh is None, "membership was recreated by a replayed invite"


def test_invite_issued_after_removal_still_works(client, db):
    """The stamp must not brick legitimate re-invites."""
    import time
    from app.auth import create_invite_token
    org = _mk_org(db, "Reinvite Org")
    user = _mk_user(db, "reinvited@example.com")
    _login(client, user)

    db.get(User, user.id).org_membership_revoked_at = _now()
    db.commit()
    db.expire_all()

    time.sleep(1.1)  # itsdangerous timestamps have 1-second resolution
    token = create_invite_token(org.id, user.email, "viewer")
    r = client.post("/api/v1/invites/accept", json={"token": token})
    assert r.status_code in (200, 204), f"fresh invite rejected ({r.status_code})"


# ── OAuth: provider email must be verified before auto-linking ────────────────

def test_oauth_rejects_unverified_provider_email():
    """crud.get_or_create_oauth_user auto-links by email, so an OAuth identity
    asserting an unverified address would take over the matching local account."""
    from fastapi import HTTPException
    from app.api.routes.auth import _require_verified_provider_email

    for payload in (
        {"email": "victim@corp.ch"},                          # claim absent
        {"email": "victim@corp.ch", "email_verified": False},
        {"email": "victim@corp.ch", "email_verified": "true"},  # string, not bool
    ):
        with pytest.raises(HTTPException) as exc:
            _require_verified_provider_email(payload, "google")
        assert exc.value.status_code == 400


def test_oauth_accepts_verified_provider_email():
    from app.api.routes.auth import _require_verified_provider_email
    _require_verified_provider_email({"email": "real@corp.ch", "email_verified": True}, "google")
