"""Member management and invite routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.api.deps import get_current_org, require_org_role
from app.database import get_db
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User
from app.services.tiers import has_feature

from app.api.routes.workspace._shared import (
    AddMemberRequest,
    InviteMemberRequest,
    MemberOut,
    UpdateRoleRequest,
    _VALID_ROLES,
    _validate_org_access,
)

router = APIRouter()


@router.get("/members", response_model=list[MemberOut], summary="List org members (admin+)")
def list_members(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    rows = (
        db.query(OrgMember, User)
        .join(User, OrgMember.user_id == User.id)
        .filter(OrgMember.org_id == org.id)
        .order_by(User.created_at)
        .all()
    )
    return [
        MemberOut(
            id=user.id,
            email=user.email,
            org_role=member.role,
            is_active=user.is_active,
            created_at=user.created_at,
            has_saved_payment_method=bool(user.payment_customer_id),
        )
        for member, user in rows
    ]


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED, summary="Create a new user and add them to the org (owner only)")
def add_member(
    org_id: int,
    body: AddMemberRequest,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if not has_feature(org, "multi_user"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your plan does not support multiple users. Upgrade to Simple or above.",
        )
    if body.org_role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {sorted(_VALID_ROLES)}")
    if crud.get_user_by_email(db, body.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    new_user = crud.create_user(db, email=body.email, password=body.password)
    new_user.org_id = org.id
    new_user.org_role = body.org_role
    new_user.email_verified = True
    existing_member = db.query(OrgMember).filter(
        OrgMember.org_id == org.id, OrgMember.user_id == new_user.id
    ).first()
    if existing_member:
        existing_member.role = body.org_role
    else:
        db.add(OrgMember(org_id=org.id, user_id=new_user.id, role=body.org_role))
    db.commit()
    db.refresh(new_user)
    return new_user


@router.patch("/members/{user_id}", response_model=MemberOut, summary="Update a member's role (owner only)")
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
    target_member = db.query(OrgMember).filter(
        OrgMember.org_id == org.id, OrgMember.user_id == user_id
    ).first() if target else None
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.id == actor.id and body.org_role != "owner":
        owner_count = db.query(OrgMember).filter(
            OrgMember.org_id == org.id, OrgMember.role == "owner"
        ).count()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last owner")
    target_member.role = body.org_role
    target.org_role = body.org_role
    db.commit()
    db.refresh(target)
    return target


@router.delete("/members/{user_id}", status_code=204, summary="Remove a member from the org (owner only)")
def remove_member(
    org_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("owner")),
):
    _validate_org_access(org_id, user_org)
    actor, org = user_org
    target = db.get(User, user_id)
    target_member = db.query(OrgMember).filter(
        OrgMember.org_id == org.id, OrgMember.user_id == user_id
    ).first() if target else None
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.id == actor.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    if target_member.role == "owner":
        owner_count = db.query(OrgMember).filter(
            OrgMember.org_id == org.id, OrgMember.role == "owner"
        ).count()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")
    db.delete(target_member)
    if target.org_id == org.id:
        fallback = db.query(OrgMember).filter(
            OrgMember.user_id == target.id, OrgMember.org_id != org.id
        ).first()
        target.org_id = fallback.org_id if fallback else None
        target.org_role = fallback.role if fallback else "viewer"
    db.commit()


@router.post("/invites", status_code=status.HTTP_204_NO_CONTENT, summary="Send an invite email to join this org (admin+)")
def send_invite(
    org_id: int,
    body: InviteMemberRequest,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    actor, org = user_org
    if not has_feature(org, "multi_user"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your plan does not support multiple users. Upgrade to Simple or above.",
        )
    existing = crud.get_user_by_email(db, body.email)
    existing_member = db.query(OrgMember).filter(
        OrgMember.org_id == org.id, OrgMember.user_id == existing.id
    ).first() if existing else None
    if existing_member:
        raise HTTPException(status_code=409, detail="User is already a member of this org")
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {sorted(_VALID_ROLES)}")
    from app.auth import create_invite_token
    from app.services.email import send_invite_email
    token = create_invite_token(org.id, body.email, role=body.role)
    send_invite_email(to=body.email, org_name=org.name, invited_by_email=actor.email, token=token)
