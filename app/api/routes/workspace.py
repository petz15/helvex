"""Org-scoped workspace routes.

These routes operate on overlay data (org_company_state, user_company_state)
on top of the global catalog. All endpoints require an authenticated user
who belongs to an org.

Route structure:
  /orgs/{org_id}                                 — org info + update
  /orgs/{org_id}/members                         — member management (admin+)
  /orgs/{org_id}/companies/{company_id}/state    — org-shared overlay (member+)
  /orgs/{org_id}/companies/{company_id}/my-state — private user overlay (any member)
  /orgs/{org_id}/jobs                            — org-scoped job list
  /orgs/{org_id}/settings                        — org settings overrides (admin+)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app import crud
from app.api.deps import get_current_org, require_org_role
from app.database import get_db
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User
from app.services.tiers import has_feature

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
    contact_status: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class OrgStateOut(BaseModel):
    org_id: int
    company_id: int
    tags: str | None
    review_status: str | None
    contact_status: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    website_url: str | None
    web_score: float | None
    social_media_only: bool | None
    website_checked_at: str | None

    model_config = {"from_attributes": True}


class UserStateUpdate(BaseModel):
    personal_score_override: float | None = None


class UserStateOut(BaseModel):
    user_id: int
    company_id: int
    ai_score: float | None
    ai_category: str | None
    ai_freeform: str | None
    personal_score_override: float | None

    model_config = {"from_attributes": True}


class OrgSettingUpdate(BaseModel):
    key: str
    value: str | None


# Keys that org admins are allowed to set at org level
_ORG_ALLOWED_SETTING_KEYS: frozenset[str] = frozenset({
    "anthropic_api_key",
    "claude_target_description",
    "claude_classify_prompt",
    "claude_classify_categories",
    "scoring_target_clusters",
    "scoring_exclude_clusters",
    "scoring_cluster_hit_points",
    "scoring_cluster_exclude_points",
    "scoring_target_keywords",
    "scoring_exclude_keywords",
    "scoring_keyword_hit_points",
    "scoring_keyword_exclude_points",
    "scoring_origin_lat",
    "scoring_origin_lon",
    "scoring_dist_15km",
    "scoring_dist_40km",
    "scoring_dist_80km",
    "scoring_dist_130km",
    "scoring_dist_far",
    "scoring_legal_form_scores",
    "scoring_legal_form_default",
    "scoring_cancelled_score",
    "scoring_weight_ai",
    "scoring_weight_web",
    "scoring_weight_flex",
})


class OrgWorkspaceSettingsBatch(BaseModel):
    """Batch of org-level scoring/AI setting overrides."""
    anthropic_api_key: str | None = None
    claude_target_description: str | None = None
    claude_classify_prompt: str | None = None
    claude_classify_categories: str | None = None
    scoring_target_clusters: str | None = None
    scoring_exclude_clusters: str | None = None
    scoring_cluster_hit_points: str | None = None
    scoring_cluster_exclude_points: str | None = None
    scoring_target_keywords: str | None = None
    scoring_exclude_keywords: str | None = None
    scoring_keyword_hit_points: str | None = None
    scoring_keyword_exclude_points: str | None = None
    scoring_origin_lat: str | None = None
    scoring_origin_lon: str | None = None
    scoring_dist_15km: str | None = None
    scoring_dist_40km: str | None = None
    scoring_dist_80km: str | None = None
    scoring_dist_130km: str | None = None
    scoring_dist_far: str | None = None
    scoring_legal_form_scores: str | None = None
    scoring_legal_form_default: str | None = None
    scoring_cancelled_score: str | None = None
    scoring_weight_ai: str | None = None
    scoring_weight_web: str | None = None
    scoring_weight_flex: str | None = None


class OrgOut(BaseModel):
    id: int
    name: str
    slug: str
    tier: str
    credits_balance: int = 0
    verified_business: bool = False
    verified_domain: str | None = None
    custom_features: dict | None = None
    member_count: int = 0

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: str | None = None


class MemberOut(BaseModel):
    id: int
    email: str
    org_role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    email: str
    password: str
    org_role: str = "member"

    @field_validator("password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class InviteMemberRequest(BaseModel):
    email: str


class UpdateRoleRequest(BaseModel):
    org_role: str


_VALID_ROLES = {"viewer", "member", "admin", "owner"}


# ── Org info & update ──────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=OrgOut,
    summary="Get org info",
)
def get_org(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    member_count = db.query(OrgMember).filter(OrgMember.org_id == org.id).count()
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        tier=org.tier,
        credits_balance=org.credits_balance,
        verified_business=org.verified_business,
        verified_domain=org.verified_domain,
        custom_features=org.custom_features,
        member_count=member_count,
    )


@router.patch(
    "",
    response_model=OrgOut,
    summary="Update org name (admin+)",
)
def update_org(
    org_id: int,
    body: OrgUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if body.name is not None:
        org.name = body.name.strip()
    db.commit()
    db.refresh(org)
    member_count = db.query(OrgMember).filter(OrgMember.org_id == org.id).count()
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        tier=org.tier,
        credits_balance=org.credits_balance,
        verified_business=org.verified_business,
        verified_domain=org.verified_domain,
        custom_features=org.custom_features,
        member_count=member_count,
    )


# ── Member management ──────────────────────────────────────────────────────────

@router.get(
    "/members",
    response_model=list[MemberOut],
    summary="List org members (admin+)",
)
def list_members(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    # Use org_members as the authoritative source for membership
    user_ids = [m.user_id for m in db.query(OrgMember).filter(OrgMember.org_id == org.id).all()]
    members = db.query(User).filter(User.id.in_(user_ids)).order_by(User.created_at).all()
    return members


@router.post(
    "/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user and add them to the org (owner only)",
)
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
    new_user = crud.create_user(
        db,
        email=body.email,
        password=body.password,
    )
    new_user.org_id = org.id
    new_user.org_role = body.org_role
    new_user.email_verified = True  # admin-created users skip email verification
    # Sync org_members for the target org
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


@router.patch(
    "/members/{user_id}",
    response_model=MemberOut,
    summary="Update a member's role (owner only)",
)
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
    # Prevent demoting self if last owner
    if target.id == actor.id and body.org_role != "owner":
        owner_count = db.query(OrgMember).filter(
            OrgMember.org_id == org.id, OrgMember.role == "owner"
        ).count()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last owner")
    target_member.role = body.org_role
    target.org_role = body.org_role  # keep legacy field in sync
    db.commit()
    db.refresh(target)
    return target


@router.delete(
    "/members/{user_id}",
    status_code=204,
    summary="Remove a member from the org (owner only)",
)
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
    # Check: don't leave org with no owner
    if target_member.role == "owner":
        owner_count = db.query(OrgMember).filter(
            OrgMember.org_id == org.id, OrgMember.role == "owner"
        ).count()
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")
    db.delete(target_member)
    # If this org was the target user's active org, fall back to another membership
    if target.org_id == org.id:
        fallback = db.query(OrgMember).filter(
            OrgMember.user_id == target.id, OrgMember.org_id != org.id
        ).first()
        target.org_id = fallback.org_id if fallback else None
        target.org_role = fallback.role if fallback else "member"
    db.commit()


# ── Org invites ───────────────────────────────────────────────────────────────

@router.post(
    "/invites",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Send an invite email to join this org (admin+)",
)
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
    # Don't invite someone already in this org
    existing = crud.get_user_by_email(db, body.email)
    existing_member = db.query(OrgMember).filter(
        OrgMember.org_id == org.id, OrgMember.user_id == existing.id
    ).first() if existing else None
    if existing_member:
        raise HTTPException(status_code=409, detail="User is already a member of this org")
    from app.auth import create_invite_token
    from app.services.email import send_invite_email
    token = create_invite_token(org.id, body.email)
    send_invite_email(to=body.email, org_name=org.name, invited_by_email=actor.email, token=token)


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


@router.get(
    "/settings/effective",
    summary="Get effective settings for this org (global defaults merged with org overrides)",
)
def get_org_effective_settings(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    """Returns the subset of workspace-relevant settings with org overrides applied.

    Useful for the explorer setup gate: check if api key and target description are configured.
    Does NOT expose the actual anthropic_api_key value — only whether it is set.
    """
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app import crud as _crud
    result: dict[str, str | bool] = {}
    for key in _ORG_ALLOWED_SETTING_KEYS:
        val = _crud.get_effective_setting(db, key, org_id=org.id, default="")
        if key == "anthropic_api_key":
            # Never expose the actual key value — just whether it is set
            result["anthropic_api_key_set"] = bool(val.strip())
        else:
            result[key] = val
    return result


@router.put(
    "/settings",
    summary="Batch-save org workspace settings (admin+)",
)
def save_org_workspace_settings(
    org_id: int,
    body: OrgWorkspaceSettingsBatch,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    """Save multiple org-level scoring/AI settings in one request.

    Fields set to null or empty string are deleted (reset to global default).
    """
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app.models.org_setting import OrgSetting

    updates = body.model_dump()
    for key, value in updates.items():
        if key not in _ORG_ALLOWED_SETTING_KEYS:
            continue
        row = db.query(OrgSetting).filter(OrgSetting.org_id == org.id, OrgSetting.key == key).first()
        if value is None or value == "":
            if row is not None:
                db.delete(row)
        else:
            if row is None:
                db.add(OrgSetting(org_id=org.id, key=key, value=value))
            else:
                row.value = value
    db.commit()
    return {"ok": True}


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
