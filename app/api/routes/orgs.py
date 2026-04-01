"""Org lifecycle routes — create, list, switch, leave, delete."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/orgs", tags=["orgs"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateOrgRequest(BaseModel):
    name: str


class OrgOut(BaseModel):
    id: int
    name: str
    slug: str
    tier: str
    role: str | None = None  # caller's role in this org (populated for /me list)

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _get_member_row(db: Session, org_id: int, user_id: int) -> OrgMember | None:
    return db.query(OrgMember).filter(OrgMember.org_id == org_id, OrgMember.user_id == user_id).first()


def _owner_count(db: Session, org_id: int) -> int:
    return db.query(OrgMember).filter(OrgMember.org_id == org_id, OrgMember.role == "owner").count()


# ---------------------------------------------------------------------------
# List caller's orgs
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    response_model=list[OrgOut],
    summary="List all orgs the current user is a member of",
)
def list_my_orgs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OrgOut]:
    rows = (
        db.query(OrgMember, Organization)
        .join(Organization, OrgMember.org_id == Organization.id)
        .filter(OrgMember.user_id == current_user.id)
        .order_by(Organization.name)
        .all()
    )
    result = []
    for member, org in rows:
        result.append(OrgOut(
            id=org.id,
            name=org.name,
            slug=org.slug,
            tier=org.tier,
            role=member.role,
        ))
    return result


# ---------------------------------------------------------------------------
# Switch active org
# ---------------------------------------------------------------------------

@router.post(
    "/switch/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Switch the active organization for the current session",
)
def switch_org(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    member = _get_member_row(db, org_id, current_user.id)
    if member is None and not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    current_user.org_id = org_id
    if member:
        current_user.org_role = member.role
    db.commit()


# ---------------------------------------------------------------------------
# Create org
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=OrgOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new organization",
)
def create_org(
    body: CreateOrgRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrgOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name cannot be empty")
    slug = _unique_slug(db, _slugify(name))
    org = Organization(name=name, slug=slug)
    db.add(org)
    db.flush()

    # Add creator as owner in org_members
    db.add(OrgMember(org_id=org.id, user_id=current_user.id, role="owner"))

    # Set as active org
    current_user.org_id = org.id
    current_user.org_role = "owner"
    db.commit()
    db.refresh(org)
    return OrgOut(id=org.id, name=org.name, slug=org.slug, tier=org.tier, role="owner")


# ---------------------------------------------------------------------------
# Delete org
# ---------------------------------------------------------------------------

@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organization (owner only)",
)
def delete_org(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    member = _get_member_row(db, org_id, current_user.id)
    if member is None and not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this org")
    if not current_user.is_superadmin and member.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can delete the org")
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    # Clear active org pointer for all members of this org
    members = db.query(OrgMember).filter(OrgMember.org_id == org_id).all()
    affected_user_ids = [m.user_id for m in members]
    if affected_user_ids:
        db.query(User).filter(
            User.id.in_(affected_user_ids),
            User.org_id == org_id,
        ).update({"org_id": None, "org_role": "member"}, synchronize_session=False)

    # org_members rows cascade-delete with the org
    db.delete(org)
    db.commit()


# ---------------------------------------------------------------------------
# Leave org
# ---------------------------------------------------------------------------

@router.post(
    "/{org_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Leave an organization",
)
def leave_org(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    member = _get_member_row(db, org_id, current_user.id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this org")

    # Prevent leaving if last owner
    if member.role == "owner" and _owner_count(db, org_id) <= 1:
        raise HTTPException(
            status_code=400,
            detail="You are the only owner. Transfer ownership before leaving, or delete the org.",
        )

    # Remove from org_members
    db.delete(member)

    # If this was the active org, try to fall back to another membership
    if current_user.org_id == org_id:
        fallback = (
            db.query(OrgMember)
            .filter(OrgMember.user_id == current_user.id, OrgMember.org_id != org_id)
            .first()
        )
        if fallback:
            current_user.org_id = fallback.org_id
            current_user.org_role = fallback.role
        else:
            current_user.org_id = None
            current_user.org_role = "member"

    db.commit()
