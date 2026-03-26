"""Org-scoped workspace routes.

These routes operate on overlay data (org_company_state, user_company_state)
on top of the global catalog. All endpoints require an authenticated user
who belongs to an org.

Route structure:
  /orgs/{org_id}/companies/{company_id}/state    — org-shared overlay (member+)
  /orgs/{org_id}/companies/{company_id}/my-state — private user overlay (any member)
  /orgs/{org_id}/jobs                            — org-scoped job list
  /orgs/{org_id}/settings                        — org settings overrides (admin+)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
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
    proposal_status: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class OrgStateOut(BaseModel):
    org_id: int
    company_id: int
    tags: str | None
    review_status: str | None
    proposal_status: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    website_url: str | None
    website_match_score: float | None
    social_media_only: bool | None
    website_checked_at: str | None

    model_config = {"from_attributes": True}


class UserStateUpdate(BaseModel):
    personal_score_override: float | None = None


class UserStateOut(BaseModel):
    user_id: int
    company_id: int
    claude_score: float | None
    claude_category: str | None
    claude_freeform: str | None
    personal_score_override: float | None

    model_config = {"from_attributes": True}


class OrgSettingUpdate(BaseModel):
    key: str
    value: str | None


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
