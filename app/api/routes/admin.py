"""Superadmin-only routes for platform management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])

_VALID_TIERS = {"free", "starter", "professional", "enterprise"}


def _require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin access required")
    return current_user


# ── Schemas ────────────────────────────────────────────────────────────────────

class AdminUserUpdate(BaseModel):
    tier: str | None = None
    is_active: bool | None = None
    is_superadmin: bool | None = None


class AdminOrgUpdate(BaseModel):
    name: str | None = None
    tier: str | None = None


# ── Stats ──────────────────────────────────────────────────────────────────────

@router.get("/stats", summary="Platform-wide stats (superadmin)")
def get_stats(
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0
    verified_users = db.query(func.count(User.id)).filter(User.email_verified.is_(True)).scalar() or 0
    total_orgs = db.query(func.count(Organization.id)).scalar() or 0
    users_in_org = db.query(func.count(User.id)).filter(User.org_id.isnot(None)).scalar() or 0
    return {
        "total_users": total_users,
        "active_users": active_users,
        "verified_users": verified_users,
        "total_orgs": total_orgs,
        "users_in_org": users_in_org,
    }


# ── Users ──────────────────────────────────────────────────────────────────────

def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "tier": u.tier,
        "is_active": u.is_active,
        "email_verified": u.email_verified,
        "is_superadmin": u.is_superadmin,
        "org_id": u.org_id,
        "org_name": u.org.name if u.org else None,
        "org_role": u.org_role,
        "created_at": u.created_at.isoformat(),
    }


@router.get("/users", summary="List all users (superadmin)")
def list_users(
    q: str | None = Query(None),
    tier: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    query = db.query(User)
    if q:
        query = query.filter(User.email.ilike(f"%{q}%"))
    if tier:
        query = query.filter(User.tier == tier)
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    total = query.count()
    users = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"items": [_user_dict(u) for u in users], "total": total, "page": page, "page_size": page_size}


@router.patch("/users/{user_id}", summary="Update user tier/status (superadmin)")
def update_user(
    user_id: int,
    body: AdminUserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(_require_superadmin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.tier is not None:
        if body.tier not in _VALID_TIERS:
            raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {sorted(_VALID_TIERS)}")
        user.tier = body.tier
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_superadmin is not None:
        if user.id == actor.id and not body.is_superadmin:
            raise HTTPException(status_code=400, detail="Cannot revoke your own superadmin")
        user.is_superadmin = body.is_superadmin
    db.commit()
    db.refresh(user)
    return _user_dict(user)


# ── Orgs ──────────────────────────────────────────────────────────────────────

def _org_dict(org: Organization, member_count: int) -> dict:
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "tier": org.tier,
        "member_count": member_count,
        "created_at": org.created_at.isoformat(),
    }


@router.get("/orgs", summary="List all orgs (superadmin)")
def list_orgs(
    q: str | None = Query(None),
    tier: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    query = db.query(Organization)
    if q:
        query = query.filter(Organization.name.ilike(f"%{q}%"))
    if tier:
        query = query.filter(Organization.tier == tier)
    total = query.count()
    orgs = (
        query.order_by(Organization.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    result = []
    for org in orgs:
        mc = db.query(func.count(User.id)).filter(User.org_id == org.id).scalar() or 0
        result.append(_org_dict(org, mc))
    return {"items": result, "total": total, "page": page, "page_size": page_size}


@router.patch("/orgs/{org_id}", summary="Update org name/tier (superadmin)")
def update_org(
    org_id: int,
    body: AdminOrgUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    if body.name is not None:
        org.name = body.name.strip()
    if body.tier is not None:
        if body.tier not in _VALID_TIERS:
            raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {sorted(_VALID_TIERS)}")
        org.tier = body.tier
    db.commit()
    db.refresh(org)
    mc = db.query(func.count(User.id)).filter(User.org_id == org.id).scalar() or 0
    return _org_dict(org, mc)


@router.delete("/orgs/{org_id}", status_code=204, summary="Delete org and kick all members (superadmin)")
def delete_org_admin(
    org_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_superadmin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    db.query(User).filter(User.org_id == org_id).update(
        {"org_id": None, "org_role": "member"}, synchronize_session=False
    )
    db.delete(org)
    db.commit()
