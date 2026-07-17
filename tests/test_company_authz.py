"""Authorization: shared company master-data mutations are superadmin-only;
per-org workflow fields remain editable by normal users."""

from app.auth import get_current_user
from app.main import app
from app.models.company import Company
from app.models.organization import Organization
from app.models.user import User


def _as_normal_user(org_id=None):
    app.dependency_overrides[get_current_user] = lambda: User(
        id=99, email="user@example.com", hashed_password="x", is_active=True,
        email_verified=True, is_superadmin=False, org_id=org_id, org_role="viewer",
    )


def _company(db, *, cid, uid, name="Orig AG"):
    c = Company(id=cid, uid=uid, name=name)
    db.add(c)
    db.commit()
    return c


def test_normal_user_cannot_create_company(client, db):
    _as_normal_user()
    resp = client.post("/api/v1/companies", json={"uid": "CHE-123.456.789", "name": "X AG"})
    assert resp.status_code == 403


def test_normal_user_cannot_delete_company(client, db):
    _company(db, cid=1, uid="CHE-111.111.111")
    _as_normal_user()
    resp = client.delete("/api/v1/companies/1")
    assert resp.status_code == 403
    assert db.get(Company, 1) is not None  # still there


def test_normal_user_cannot_edit_master_field(client, db):
    c = _company(db, cid=2, uid="CHE-222.222.222", name="Orig AG")
    _as_normal_user()
    resp = client.patch("/api/v1/companies/2", json={"name": "Hacked"})
    assert resp.status_code == 403
    db.refresh(c)
    assert c.name == "Orig AG"  # unchanged


def test_normal_user_can_edit_workflow_field(client, db):
    db.add(Organization(id=5, name="o5", slug="o5", tier="free"))
    _company(db, cid=3, uid="CHE-333.333.333", name="WF AG")
    db.commit()
    _as_normal_user(org_id=5)
    resp = client.patch("/api/v1/companies/3", json={"review_status": "reviewed"})
    assert resp.status_code == 200  # per-org workflow field is allowed
