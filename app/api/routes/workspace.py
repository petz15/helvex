"""Org-scoped workspace routes.

These routes operate on overlay data (org_company_state, user_company_state)
on top of the global catalog. All endpoints require an authenticated user
who belongs to an org.

Route structure:
  /orgs/{org_id}                                 — org info + update
  /orgs/{org_id}/members                         — member management (admin+)
  /orgs/{org_id}/companies/{company_id}/state    — org-shared overlay (member+)
  /orgs/{org_id}/companies/{company_id}/my-state — private user overlay (any member)
  /orgs/{org_id}/jobs                            — org-scoped job list
  /orgs/{org_id}/settings                        — org settings overrides (admin+)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app import crud
from app.api.deps import get_current_org, require_org_role
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/orgs/{org_id}", tags=["workspace"])


def _validate_org_access(org_id: int, user_org: tuple[User, Organization]) -> tuple[User, Organization]:
    """Ensure the org_id in the path matches the user's org."""
    user, org = user_org
    if not user.is_superadmin and org.id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return user_org


# ── Schemas ────────────────────────────────────────────────────────────────────

class OrgStateUpdate(BaseModel):
    tags: str | None = None
    review_status: str | None = None
    contact_status: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class OrgStateOut(BaseModel):
    org_id: int
    company_id: int
    tags: str | None
    review_status: str | None
    contact_status: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    website_url: str | None
    web_score: float | None
    social_media_only: bool | None
    website_checked_at: str | None

    model_config = {"from_attributes": True}


class UserStateUpdate(BaseModel):
    personal_score_override: float | None = None


class UserStateOut(BaseModel):
    user_id: int
    company_id: int
    ai_score: float | None
    ai_category: str | None
    ai_freeform: str | None
    personal_score_override: float | None

    model_config = {"from_attributes": True}


class OrgSettingUpdate(BaseModel):
    key: str
    value: str | None


class OrgOut(BaseModel):
    id: int
    name: str
    slug: str
    tier: str
    member_count: int = 0

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: str | None = None


class MemberOut(BaseModel):
    id: int
    email: str
    org_role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    email: str
    password: str
    org_role: str = "member"

    @field_validator("password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class InviteMemberRequest(BaseModel):
    email: str


class UpdateRoleRequest(BaseModel):
    org_role: str


_VALID_ROLES = {"viewer", "member", "admin", "owner"}


# ── Org info & update ──────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=OrgOut,
    summary="Get org info",
)
def get_org(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    member_count = db.query(User).filter(User.org_id == org.id).count()
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        tier=org.tier,
        member_count=member_count,
    )


@router.patch(
    "",
    response_model=OrgOut,
    summary="Update org name (admin+)",
)
def update_org(
    org_id: int,
    body: OrgUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if body.name is not None:
        org.name = body.name.strip()
    db.commit()
    db.refresh(org)
    member_count = db.query(User).filter(User.org_id == org.id).count()
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        tier=org.tier,
        member_count=member_count,
    )


# ── Member management ──────────────────────────────────────────────────────────

@router.get(
    "/members",
    response_model=list[MemberOut],
    summary="List org members (admin+)",
)
def list_members(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    members = db.query(User).filter(User.org_id == org.id).order_by(User.created_at).all()
    return members


@router.post(
    "/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user and add them to the org (owner only)",
)
def add_member(
    org_id: int,
    body: AddMemberRequest,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if body.org_role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {sorted(_VALID_ROLES)}")
    if crud.get_user_by_email(db, body.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    new_user = crud.create_user(
        db,
        email=body.email,
        password=body.password,
    )
    new_user.org_id = org.id
    new_user.org_role = body.org_role
    new_user.email_verified = True  # admin-created users skip email verification
    db.commit()
    db.refresh(new_user)
    return new_user


@router.patch(
    "/members/{user_id}",
    response_model=MemberOut,
    summary="Update a member's role (owner only)",
)
def update_member_role(
    org_id: int,
    user_id: int,
    body: UpdateRoleRequest,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("owner")),
):
    _validate_org_access(org_id, user_org)
    actor, org = user_org
    if body.org_role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {sorted(_VALID_ROLES)}")
    target = db.get(User, user_id)
    if not target or target.org_id != org.id:
        raise HTTPException(status_code=404, detail="Member not found")
    # Prevent demoting self if last owner
    if target.id == actor.id and body.org_role != "owner":
        owner_count = db.query(User).filter(User.org_id == org.id, User.org_role == "owner").count()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last owner")
    target.org_role = body.org_role
    db.commit()
    db.refresh(target)
    return target


@router.delete(
    "/members/{user_id}",
    status_code=204,
    summary="Remove a member from the org (owner only)",
)
def remove_member(
    org_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("owner")),
):
    _validate_org_access(org_id, user_org)
    actor, org = user_org
    target = db.get(User, user_id)
    if not target or target.org_id != org.id:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.id == actor.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    # Check: don't leave org with no owner
    if target.org_role == "owner":
        owner_count = db.query(User).filter(User.org_id == org.id, User.org_role == "owner").count()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")
    target.org_id = None
    target.org_role = "member"
    db.commit()


# ── Org invites ───────────────────────────────────────────────────────────────

@router.post(
    "/invites",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Send an invite email to join this org (admin+)",
)
def send_invite(
    org_id: int,
    body: InviteMemberRequest,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    actor, org = user_org
    # Don't invite someone already in this org
    existing = crud.get_user_by_email(db, body.email)
    if existing and existing.org_id == org.id:
        raise HTTPException(status_code=409, detail="User is already a member of this org")
    from app.auth import create_invite_token
    from app.services.email import send_invite_email
    token = create_invite_token(org.id, body.email)
    send_invite_email(to=body.email, org_name=org.name, invited_by_email=actor.email, token=token)


# ── Org company state ──────────────────────────────────────────────────────────

@router.get(
    "/companies/{company_id}/state",
    response_model=OrgStateOut,
    summary="Get org overlay for a company",
)
def get_org_state(
    org_id: int,
    company_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    row = crud.get_org_company_state(db, org_id=org.id, company_id=company_id)
    if not row:
        raise HTTPException(status_code=404, detail="No org state for this company")
    return row


@router.patch(
    "/companies/{company_id}/state",
    response_model=OrgStateOut,
    summary="Update org-shared overlay for a company (member+)",
)
def update_org_state(
    org_id: int,
    company_id: int,
    body: OrgStateUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("member", "admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if not crud.get_company(db, company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    row = crud.get_or_create_org_company_state(db, org_id=org.id, company_id=company_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return crud.update_org_company_state(db, row, **updates)


# ── User company state (private) ───────────────────────────────────────────────

@router.get(
    "/companies/{company_id}/my-state",
    response_model=UserStateOut,
    summary="Get private user overlay for a company",
)
def get_my_state(
    org_id: int,
    company_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    user, org = user_org
    row = crud.get_user_company_state(db, user_id=user.id, company_id=company_id)
    if not row:
        raise HTTPException(status_code=404, detail="No private state for this company")
    return row


@router.patch(
    "/companies/{company_id}/my-state",
    response_model=UserStateOut,
    summary="Update private user overlay for a company",
)
def update_my_state(
    org_id: int,
    company_id: int,
    body: UserStateUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    user, org = user_org
    if not crud.get_company(db, company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    return crud.update_personal_score_override(
        db,
        user_id=user.id,
        company_id=company_id,
        org_id=org.id,
        personal_score_override=body.personal_score_override,
    )


# ── Org jobs ──────────────────────────────────────────────────────────────────

@router.get(
    "/jobs",
    summary="List jobs scoped to this org",
)
def list_org_jobs(
    org_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app.api.routes.jobs import JobOut
    return [JobOut.from_orm_obj(j) for j in crud.list_org_jobs(db, org_id=org.id, limit=limit)]


# ── Org settings ──────────────────────────────────────────────────────────────

@router.get(
    "/settings",
    summary="Get org-specific settings overrides",
)
def get_org_settings(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app.models.org_setting import OrgSetting
    rows = db.query(OrgSetting).filter(OrgSetting.org_id == org.id).all()
    return {r.key: r.value for r in rows}


@router.put(
    "/settings/{key}",
    summary="Set an org-specific setting override (admin+)",
)
def set_org_setting(
    org_id: int,
    key: str,
    body: OrgSettingUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app.models.org_setting import OrgSetting
    row = db.query(OrgSetting).filter(OrgSetting.org_id == org.id, OrgSetting.key == key).first()
    if row is None:
        row = OrgSetting(org_id=org.id, key=key, value=body.value)
        db.add(row)
    else:
        row.value = body.value
    db.commit()
    return {"key": key, "value": body.value}


@router.delete(
    "/settings/{key}",
    status_code=204,
    summary="Remove an org-specific setting override (admin+)",
)
def delete_org_setting(
    org_id: int,
    key: str,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app.models.org_setting import OrgSetting
    row = db.query(OrgSetting).filter(OrgSetting.org_id == org.id, OrgSetting.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail="Setting not found")
    db.delete(row)
    db.commit()
