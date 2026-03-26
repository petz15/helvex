"""Org lifecycle routes — create org, leave org."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/orgs", tags=["orgs"])


class CreateOrgRequest(BaseModel):
    name: str


class OrgOut(BaseModel):
    id: int
    name: str
    slug: str
    tier: str

    model_config = {"from_attributes": True}


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "org"


def _unique_slug(db: Session, base: str) -> str:
    slug = base
    n = 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


@router.post(
    "",
    response_model=OrgOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new organization (any authenticated user)",
)
def create_org(
    body: CreateOrgRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name cannot be empty")
    if current_user.org_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of an organization. Leave it first to create a new one.",
        )
    slug = _unique_slug(db, _slugify(name))
    org = Organization(name=name, slug=slug)
    db.add(org)
    db.flush()  # get the new org.id

    current_user.org_id = org.id
    current_user.org_role = "owner"
    db.commit()
    db.refresh(org)
    return org


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organization (owner only)",
)
def delete_org(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.org_id != org_id and not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this org")
    if not current_user.is_superadmin and current_user.org_role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can delete the org")
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    db.query(User).filter(User.org_id == org_id).update(
        {"org_id": None, "org_role": "member"}, synchronize_session=False
    )
    db.delete(org)
    db.commit()


@router.post(
    "/{org_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Leave an organization",
)
def leave_org(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this org")
    # Prevent leaving if you're the last owner
    if current_user.org_role == "owner":
        owner_count = (
            db.query(User)
            .filter(User.org_id == org_id, User.org_role == "owner")
            .count()
        )
        if owner_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="You are the only owner. Transfer ownership before leaving, or delete the org.",
            )
    current_user.org_id = None
    current_user.org_role = "member"
    db.commit()
