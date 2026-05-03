"""Org and user company state overlay routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.api.deps import get_current_org, require_org_role
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User

from app.api.routes.workspace._shared import (
    OrgStateOut,
    OrgStateUpdate,
    UserStateOut,
    UserStateUpdate,
    _validate_org_access,
)

router = APIRouter()


@router.get("/companies/{company_id}/state", response_model=OrgStateOut, summary="Get org overlay for a company")
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


@router.patch("/companies/{company_id}/state", response_model=OrgStateOut, summary="Update org-shared overlay for a company (member+)")
def update_org_state(
    org_id: int,
    company_id: int,
    body: OrgStateUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("contributor", "admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if not crud.get_company(db, company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    row = crud.get_or_create_org_company_state(db, org_id=org.id, company_id=company_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return crud.update_org_company_state(db, row, **updates)


@router.get("/companies/{company_id}/my-state", response_model=UserStateOut, summary="Get private user overlay for a company")
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


@router.patch("/companies/{company_id}/my-state", response_model=UserStateOut, summary="Update private user overlay for a company")
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
