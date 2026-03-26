"""Shared FastAPI dependencies for org-scoped routes."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User

_ROLE_ORDER = ["viewer", "member", "admin", "owner"]


def get_current_org(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> tuple[User, Organization]:
    """Dependency: validates the user belongs to an org and returns (user, org)."""
    if not current_user.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization")
    org = db.get(Organization, current_user.org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization not found")
    return current_user, org


def require_org_role(*roles: str):
    """Dependency factory — raises 403 if the user's org_role is not in *roles*.

    Superadmins always pass.

    Usage::

        @router.patch("/foo")
        def update_foo(user_org=Depends(require_org_role("admin", "owner"))):
            user, org = user_org
            ...
    """
    def _check(user_org: tuple[User, Organization] = Depends(get_current_org)) -> tuple[User, Organization]:
        user, org = user_org
        if user.is_superadmin:
            return user_org
        if user.org_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.org_role}' is not permitted. Required: {list(roles)}",
            )
        return user_org
    return _check
