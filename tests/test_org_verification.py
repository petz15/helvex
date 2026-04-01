import json

from app.auth import get_current_user
from app.main import app
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User


def _seed_org_and_membership(db, *, org_id: int = 1, role: str = "owner") -> Organization:
    org = Organization(id=org_id, name="Acme Org", slug=f"acme-org-{org_id}", tier="free")
    db.add(org)
    db.flush()
    db.add(OrgMember(org_id=org.id, user_id=1, role=role))
    db.commit()
    db.refresh(org)
    return org


def test_request_verification_auto_verifies_from_linked_company(client, db):
    org = _seed_org_and_membership(db)
    org.zefix_uid = "CHE-123.456.789"
    db.add(
        Company(
            uid="CHE-123.456.789",
            name="Acme AG",
            website_url="https://www.acme.ch/about",
            web_score=85,
        )
    )
    db.commit()

    resp = client.post(f"/api/v1/orgs/{org.id}/request-verification")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "verified"
    assert data["auto_verified"] is True
    assert data["verified_business"] is True
    assert data["verified_domain"] == "acme.ch"

    db.refresh(org)
    assert org.verified_business is True
    assert org.verified_domain == "acme.ch"


def test_request_verification_queues_pending_when_not_eligible(client, db):
    org = _seed_org_and_membership(db, org_id=2)
    org.zefix_uid = "CHE-999.999.999"
    db.add(
        Company(
            uid="CHE-999.999.999",
            name="Low Score AG",
            website_url="https://low-score.example",
            web_score=42,
        )
    )
    db.commit()

    resp = client.post(f"/api/v1/orgs/{org.id}/request-verification")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert data["auto_verified"] is False

    pending = (
        db.query(AuditLog)
        .filter(AuditLog.org_id == org.id, AuditLog.field == "verification_request")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert pending is not None
    payload = json.loads(pending.new_value or "{}")
    assert payload.get("company_web_score") == 42
    assert payload.get("zefix_uid") == "CHE-999.999.999"


def test_request_verification_requires_admin_or_owner(client, db):
    org = _seed_org_and_membership(db, org_id=3, role="member")

    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        email="testuser@example.com",
        hashed_password="x",
        is_active=True,
        tier="free",
        email_verified=False,
        is_superadmin=False,
    )
    try:
        resp = client.post(f"/api/v1/orgs/{org.id}/request-verification")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 403
    assert "Admin or owner role required" in resp.json()["detail"]
