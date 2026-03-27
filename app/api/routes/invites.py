"""Org invite acceptance routes (public preview + auth'd accept)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app import crud
from app.auth import (
    COOKIE_NAME,
    create_session_cookie,
    decode_invite_token,
    get_current_user,
)
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/invites", tags=["invites"])


class InvitePreview(BaseModel):
    org_id: int
    org_name: str
    invited_email: str
    user_exists: bool  # False = new user, should see inline registration form


class AcceptInviteRequest(BaseModel):
    token: str
    force: bool = False  # True = confirmed switch from existing org


class RegisterAndAcceptRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


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
    user_exists = crud.get_user_by_email(db, invited_email) is not None
    return InvitePreview(org_id=org_id, org_name=org.name, invited_email=invited_email, user_exists=user_exists)


@router.post(
    "/register-and-accept",
    summary="Create account via invite link and immediately join the org (sets session cookie)",
)
def register_and_accept(
    request: Request,
    body: RegisterAndAcceptRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """New-user invite path.  The invited email is embedded in the signed token,
    so we skip the normal email-verification step — the fact that they received
    the invite proves they own the address.
    """
    result = decode_invite_token(body.token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite link.",
        )
    org_id, invited_email = result

    if crud.get_user_by_email(db, invited_email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists. Please sign in and accept the invite.",
        )

    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Create user — mark email verified immediately (invite proves ownership)
    user = crud.create_user(db, email=invited_email, password=body.password)
    user.email_verified = True
    user.org_id = org_id
    user.org_role = "member"
    db.commit()
    db.refresh(user)

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_cookie(user.id),
        httponly=True,
        samesite="lax",
        secure=is_https,
        max_age=8 * 3600,
    )
    return response


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
