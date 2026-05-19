"""Org info, settings, and notification routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.api.deps import get_current_org, require_org_role
from app.database import get_db
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User
from app.services.tiers import has_feature, normalize_tier

from app.api.routes.workspace._shared import (
    BillingAddress,
    DefaultPaymentUserUpdate,
    NotificationPreferences,
    OrgOut,
    OrgSettingUpdate,
    OrgUpdate,
    OrgWorkspaceSettingsBatch,
    _ORG_ALLOWED_SETTING_KEYS,
    _SENSITIVE_ORG_KEYS,
    _resolve_notif,
    _set_user_org_setting,
    _validate_org_access,
)

router = APIRouter()


def _org_out(db: Session, org: Organization) -> OrgOut:
    member_count = db.query(OrgMember).filter(OrgMember.org_id == org.id).count()
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        tier=org.tier,
        credits_balance=org.credits_balance,
        verified_business=org.verified_business,
        verified_domain=org.verified_domain,
        billing_address_json=org.billing_address_json,
        default_payment_user_id=org.default_payment_user_id,
        custom_features=org.custom_features,
        member_count=member_count,
        vat_id=org.vat_id,
    )


@router.get("/", response_model=OrgOut, summary="Get org info")
def get_org(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    return _org_out(db, org)


@router.patch("/", response_model=OrgOut, summary="Update org name (admin+)")
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
    if body.billing_address is not None:
        org.billing_address_json = json.dumps(body.billing_address.model_dump())
    if body.vat_id is not None:
        org.vat_id = body.vat_id.strip() or None
    db.commit()
    db.refresh(org)
    return _org_out(db, org)


@router.put("/billing-address", response_model=OrgOut, summary="Update org billing address")
def update_billing_address(
    org_id: int,
    body: BillingAddress,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    org.billing_address_json = json.dumps(body.model_dump())
    db.commit()
    db.refresh(org)
    return _org_out(db, org)


@router.put("/default-payment-user", response_model=OrgOut, summary="Set the org's default saved payment method owner")
def update_default_payment_user(
    org_id: int,
    body: DefaultPaymentUserUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if body.user_id is None:
        org.default_payment_user_id = None
    else:
        member_row = (
            db.query(OrgMember, User)
            .join(User, OrgMember.user_id == User.id)
            .filter(OrgMember.org_id == org.id, OrgMember.user_id == body.user_id)
            .first()
        )
        if member_row is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected user is not a member of this org")
        _member, selected_user = member_row
        if not selected_user.payment_customer_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected user does not have a saved payment method")
        org.default_payment_user_id = selected_user.id
    db.commit()
    db.refresh(org)
    return _org_out(db, org)


@router.get("/jobs", summary="List jobs scoped to this org")
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


@router.get("/settings", summary="Get org-specific settings overrides")
def get_org_settings(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    user, org = user_org
    from app.models.org_setting import OrgSetting
    rows = db.query(OrgSetting).filter(OrgSetting.org_id == org.id).all()
    result = {}
    for r in rows:
        if r.key in _SENSITIVE_ORG_KEYS and not user.is_superadmin:
            result[r.key] = "***" if (r.value or "").strip() else ""
        else:
            result[r.key] = r.value
    return result


@router.get("/settings/effective", summary="Get effective settings for this org (global defaults merged with org overrides)")
def get_org_effective_settings(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(get_current_org),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app import crud as _crud
    result: dict[str, str | bool] = {}
    for key in _ORG_ALLOWED_SETTING_KEYS:
        val = _crud.get_effective_setting(db, key, org_id=org.id, default="")
        if key == "anthropic_api_key":
            result["anthropic_api_key_set"] = bool(val.strip())
        else:
            result[key] = val
    return result


@router.put("/settings", summary="Batch-save org workspace settings (admin+)")
def save_org_workspace_settings(
    org_id: int,
    body: OrgWorkspaceSettingsBatch,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    """Save multiple org-level scoring/AI settings in one request."""
    _validate_org_access(org_id, user_org)
    user, org = user_org
    from app.models.org_setting import OrgSetting

    updates = body.model_dump()
    for key, value in updates.items():
        if key not in _ORG_ALLOWED_SETTING_KEYS:
            continue
        if key == "anthropic_api_key" and not user.is_superadmin and not has_feature(org, "byo_llm_keys"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Bringing your own API key requires the Strategist tier or above (current: {normalize_tier(org.tier)})",
            )
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


@router.put("/settings/{key}", summary="Set an org-specific setting override (admin+)")
def set_org_setting(
    org_id: int,
    key: str,
    body: OrgSettingUpdate,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    user, org = user_org
    if key not in _ORG_ALLOWED_SETTING_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown setting key '{key}'")
    if key == "anthropic_api_key" and not user.is_superadmin and not has_feature(org, "byo_llm_keys"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Bringing your own API key requires the Strategist tier or above (current: {normalize_tier(org.tier)})",
        )
    from app.models.org_setting import OrgSetting
    row = db.query(OrgSetting).filter(OrgSetting.org_id == org.id, OrgSetting.key == key).first()
    if row is None:
        row = OrgSetting(org_id=org.id, key=key, value=body.value)
        db.add(row)
    else:
        row.value = body.value
    db.commit()
    return {"key": key, "value": body.value}


@router.delete("/settings/{key}", status_code=204, summary="Remove an org-specific setting override (admin+)")
def delete_org_setting(
    org_id: int,
    key: str,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    if key not in _ORG_ALLOWED_SETTING_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown setting key '{key}'")
    from app.models.org_setting import OrgSetting
    row = db.query(OrgSetting).filter(OrgSetting.org_id == org.id, OrgSetting.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail="Setting not found")
    db.delete(row)
    db.commit()


@router.get("/notifications", summary="Get email notification preferences for this org")
def get_notification_preferences(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("contributor", "admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app.crud.app_setting import get_effective_setting
    return {
        "email_notifications": get_effective_setting(db, "email_notifications", org_id=org.id, default="1") != "0",
        "notif_low_credit":    get_effective_setting(db, "notif_low_credit",    org_id=org.id, default="1") != "0",
        "notif_export_ready":  get_effective_setting(db, "notif_export_ready",  org_id=org.id, default="1") != "0",
        "notif_job_failed":    get_effective_setting(db, "notif_job_failed",    org_id=org.id, default="1") != "0",
        "notif_saved_view":    get_effective_setting(db, "notif_saved_view",    org_id=org.id, default="1") != "0",
    }


@router.patch("/notifications", summary="Update email notification preferences for this org (admin+)")
def update_notification_preferences(
    org_id: int,
    body: NotificationPreferences,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    _, org = user_org
    from app.crud.app_setting import set_org_setting as _set_org_setting

    def _v(flag: bool) -> str:
        return "1" if flag else "0"

    _set_org_setting(db, org_id=org.id, key="email_notifications", value=_v(body.email_notifications))
    _set_org_setting(db, org_id=org.id, key="notif_low_credit",    value=_v(body.notif_low_credit))
    _set_org_setting(db, org_id=org.id, key="notif_export_ready",  value=_v(body.notif_export_ready))
    _set_org_setting(db, org_id=org.id, key="notif_job_failed",    value=_v(body.notif_job_failed))
    _set_org_setting(db, org_id=org.id, key="notif_saved_view",    value=_v(body.notif_saved_view))
    return body.model_dump()


@router.get("/my-notifications", summary="Get my notification preferences for this org")
def get_my_notification_preferences(
    org_id: int,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("viewer", "contributor", "member", "admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    user, org = user_org
    return {
        "email_notifications": _resolve_notif(db, "email_notifications", user.id, org.id),
        "notif_low_credit":    _resolve_notif(db, "notif_low_credit",    user.id, org.id),
        "notif_export_ready":  _resolve_notif(db, "notif_export_ready",  user.id, org.id),
        "notif_job_failed":    _resolve_notif(db, "notif_job_failed",    user.id, org.id),
        "notif_saved_view":    _resolve_notif(db, "notif_saved_view",    user.id, org.id),
    }


@router.patch("/my-notifications", summary="Update my notification preferences for this org")
def update_my_notification_preferences(
    org_id: int,
    body: NotificationPreferences,
    db: Session = Depends(get_db),
    user_org: tuple[User, Organization] = Depends(require_org_role("viewer", "contributor", "member", "admin", "owner")),
):
    _validate_org_access(org_id, user_org)
    user, org = user_org

    def _v(flag: bool) -> str:
        return "1" if flag else "0"

    for key, val in {
        "email_notifications": body.email_notifications,
        "notif_low_credit":    body.notif_low_credit,
        "notif_export_ready":  body.notif_export_ready,
        "notif_job_failed":    body.notif_job_failed,
        "notif_saved_view":    body.notif_saved_view,
    }.items():
        _set_user_org_setting(db, user_id=user.id, org_id=org.id, key=key, value=_v(val))

    return body.model_dump()
