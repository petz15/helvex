"""Shared schemas, helpers, and constants for workspace routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.user import User


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


class DefaultPaymentUserUpdate(BaseModel):
    user_id: int | None = None


class BillingAddress(BaseModel):
    first_name: str
    last_name: str
    street: str
    number: str
    postal_code: str
    city: str
    country: str
    company_name: str | None = None


class OrgOut(BaseModel):
    id: int
    name: str
    slug: str
    tier: str
    credits_balance: int = 0
    verified_business: bool = False
    verified_domain: str | None = None
    billing_address_json: str | None = None
    default_payment_user_id: int | None = None
    custom_features: dict | None = None
    member_count: int = 0

    model_config = {"from_attributes": True}


class OrgUpdate(BaseModel):
    name: str | None = None
    billing_address: BillingAddress | None = None


class MemberOut(BaseModel):
    id: int
    email: str
    org_role: str
    is_active: bool
    created_at: datetime
    has_saved_payment_method: bool = False

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    email: str
    password: str
    org_role: str = "viewer"

    @field_validator("password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class InviteMemberRequest(BaseModel):
    email: str
    role: str = "viewer"


class UpdateRoleRequest(BaseModel):
    org_role: str


class NotificationPreferences(BaseModel):
    email_notifications: bool
    notif_low_credit: bool = True
    notif_export_ready: bool = True
    notif_job_failed: bool = True
    notif_saved_view: bool = True


_VALID_ROLES = {"viewer", "contributor", "admin", "owner"}
_SENSITIVE_ORG_KEYS = frozenset({"anthropic_api_key"})
_NOTIF_KEYS = {
    "email_notifications",
    "notif_low_credit",
    "notif_export_ready",
    "notif_job_failed",
    "notif_saved_view",
}


def _get_user_org_setting(db: Session, user_id: int, org_id: int, key: str) -> str | None:
    from app.models.user_org_setting import UserOrgSetting
    row = db.query(UserOrgSetting).filter(
        UserOrgSetting.user_id == user_id,
        UserOrgSetting.org_id == org_id,
        UserOrgSetting.key == key,
    ).first()
    return row.value if row else None


def _set_user_org_setting(db: Session, user_id: int, org_id: int, key: str, value: str) -> None:
    from app.models.user_org_setting import UserOrgSetting
    row = db.query(UserOrgSetting).filter(
        UserOrgSetting.user_id == user_id,
        UserOrgSetting.org_id == org_id,
        UserOrgSetting.key == key,
    ).first()
    if row is None:
        db.add(UserOrgSetting(user_id=user_id, org_id=org_id, key=key, value=value))
    else:
        row.value = value
    db.commit()


def _resolve_notif(db: Session, key: str, user_id: int, org_id: int) -> bool:
    user_val = _get_user_org_setting(db, user_id, org_id, key)
    if user_val is not None:
        return user_val != "0"
    from app.crud.app_setting import get_effective_setting
    return get_effective_setting(db, key, org_id=org_id, default="1") != "0"
