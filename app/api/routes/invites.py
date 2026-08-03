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
    decode_invite_token_with_timestamp,
    invite_predates_revocation,
    get_current_user,
)
from app.database import get_db
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/invites", tags=["invites"])


class InvitePreview(BaseModel):
    org_id: int
    org_name: str
    invited_email: str
    role: str
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


def _check_domain_restriction(org: Organization, email: str) -> None:
    """Raise 403 if the org is a verified business with a domain restriction
    and the invitee's email domain does not match."""
    if not org.verified_business or not org.verified_domain:
        return
    domain = email.lower().split("@")[-1]
    if domain != org.verified_domain.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This organization only accepts members from @{org.verified_domain} email addresses."
            ),
        )


def _upsert_org_member(db: Session, org_id: int, user_id: int, role: str = "viewer") -> None:
    """Insert or update the org_members row for this (org, user) pair."""
    existing = db.query(OrgMember).filter(
        OrgMember.org_id == org_id, OrgMember.user_id == user_id
    ).first()
    if existing:
        existing.role = role
    else:
        db.add(OrgMember(org_id=org_id, user_id=user_id, role=role))


@router.get(
    "/preview",
    response_model=InvitePreview,
    summary="Decode invite token and return org info (public)",
)
def preview_invite(
    token: str,
    db: Session = Depends(get_db),
) -> InvitePreview:
    result = decode_invite_token(token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite link.",
        )
    org_id, invited_email, invite_role = result
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    user_exists = crud.get_user_by_email(db, invited_email) is not None
    return InvitePreview(org_id=org_id, org_name=org.name, invited_email=invited_email,
                         role=invite_role, user_exists=user_exists)


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
    org_id, invited_email, invite_role = result

    if crud.get_user_by_email(db, invited_email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists. Please sign in and accept the invite.",
        )

    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    _check_domain_restriction(org, invited_email)

    # Create user — mark email verified immediately (invite proves ownership)
    user = crud.create_user(db, email=invited_email, password=body.password)
    user.email_verified = True

    # Join org: set active org and create/update org_members row
    user.org_id = org_id
    user.org_role = invite_role
    _upsert_org_member(db, org_id, user.id, role=invite_role)

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
) -> None:
    decoded = decode_invite_token_with_timestamp(body.token)
    if decoded is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite link.",
        )
    (org_id, invited_email, invite_role), issued_at = decoded

    # An invite minted before this user was removed from an org is dead. Without
    # this a removed member could re-open their original link and rejoin at the
    # original role for the remainder of the token's 7-day life — the membership
    # row was deleted, so nothing else here would stop them.
    if invite_predates_revocation(current_user, issued_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invite is no longer valid. Please ask for a new one.",
        )

    if current_user.email.lower() != invited_email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This invite was sent to {invited_email}. Please log in with that account.",
        )

    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    _check_domain_restriction(org, current_user.email)

    # Already in this org — idempotent
    existing_member = db.query(OrgMember).filter(
        OrgMember.org_id == org_id, OrgMember.user_id == current_user.id
    ).first()
    if existing_member:
        # Switch active org to this one if not already
        if current_user.org_id != org_id:
            current_user.org_id = org_id
            current_user.org_role = existing_member.role
            db.commit()
        return

    # Accepting moves the active org to the new org.
    # Guard: don't abandon current org if last owner (multi-org: user stays in both)
    if current_user.org_id is not None and not body.force:
        # In multi-org model, joining another org doesn't require leaving the current one.
        # The active org will switch; the old membership remains.
        pass

    # Add to org and switch active org
    _upsert_org_member(db, org_id, current_user.id, role=invite_role)
    current_user.org_id = org_id
    current_user.org_role = invite_role
    db.commit()
