"""Org invite acceptance routes (public preview + auth'd accept)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import decode_invite_token, get_current_user
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/invites", tags=["invites"])


class InvitePreview(BaseModel):
    org_id: int
    org_name: str
    invited_email: str


class AcceptInviteRequest(BaseModel):
    token: str
    force: bool = False  # True = confirmed switch from existing org


@router.get(
    "/preview",
    response_model=InvitePreview,
    summary="Decode invite token and return org info (public)",
)
def preview_invite(
    token: str,
    db: Session = Depends(get_db),
):
    result = decode_invite_token(token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite link.",
        )
    org_id, invited_email = result
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return InvitePreview(org_id=org_id, org_name=org.name, invited_email=invited_email)


@router.post(
    "/accept",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Accept an org invite (authenticated user)",
)
def accept_invite(
    body: AcceptInviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = decode_invite_token(body.token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite link.",
        )
    org_id, invited_email = result

    if current_user.email.lower() != invited_email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This invite was sent to {invited_email}. Please log in with that account.",
        )

    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Already in this org — idempotent
    if current_user.org_id == org_id:
        return

    # In a different org — require explicit force confirmation
    if current_user.org_id is not None and not body.force:
        current_org = db.get(Organization, current_user.org_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_in_org",
                "current_org_name": current_org.name if current_org else "Unknown",
                "current_org_id": current_user.org_id,
            },
        )

    # Guard: don't leave current org if last owner
    if current_user.org_id is not None and current_user.org_role == "owner":
        owner_count = (
            db.query(User)
            .filter(User.org_id == current_user.org_id, User.org_role == "owner")
            .count()
        )
        if owner_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="You are the only owner of your current org. Transfer ownership before switching.",
            )

    current_user.org_id = org_id
    current_user.org_role = "member"
    db.commit()
