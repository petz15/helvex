"""Saved dashboard views (per user)."""
import json
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.user_view import UserView
from app.services.activity import log_activity

router = APIRouter(prefix="/views", tags=["views"])


class SaveViewRequest(BaseModel):
    name: str
    filters: dict  # the CompanyFilters object as a dict


class AlertToggleRequest(BaseModel):
    enabled: bool


class SavedViewOut(BaseModel):
    id: int
    name: str
    filters: dict
    created_at: str
    alert_enabled: bool = False
    alert_last_checked_at: str | None = None
    model_config = {"from_attributes": False}


def _view_to_out(view: UserView) -> SavedViewOut:
    return SavedViewOut(
        id=view.id,
        name=view.name,
        filters=json.loads(view.filters_json),
        created_at=view.created_at.isoformat(),
        alert_enabled=bool(view.alert_enabled),
        alert_last_checked_at=view.alert_last_checked_at.isoformat() if view.alert_last_checked_at else None,
    )


@router.get("", response_model=list[SavedViewOut])
def list_views(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Return views for this user scoped to their current org, plus any legacy
    # views without an org (org_id IS NULL) created before migration 0072.
    rows = (
        db.query(UserView)
        .filter(
            UserView.user_id == current_user.id,
            (UserView.org_id == current_user.org_id) | (UserView.org_id.is_(None)),
        )
        .order_by(UserView.created_at.desc())
        .all()
    )
    return [_view_to_out(r) for r in rows]


_VIEW_NAME_MAX_LEN = 120
_VIEW_FILTERS_MAX_BYTES = 65_536  # 64 KiB — prevents DB bloat from crafted payloads


@router.post("", response_model=SavedViewOut, status_code=201)
def save_view(body: SaveViewRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    if len(body.name) > _VIEW_NAME_MAX_LEN:
        raise HTTPException(status_code=422, detail=f"View name must be at most {_VIEW_NAME_MAX_LEN} characters")
    filters_json = json.dumps(body.filters, separators=(",", ":"))
    if len(filters_json.encode()) > _VIEW_FILTERS_MAX_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"Filter payload too large (max {_VIEW_FILTERS_MAX_BYTES // 1024} KiB)",
        )
    view = UserView(user_id=current_user.id, org_id=current_user.org_id, name=body.name.strip(), filters_json=filters_json)
    db.add(view)
    db.flush()
    log_activity(
        db, action="view_created",
        user_id=current_user.id, org_id=current_user.org_id,
        resource_type="view", resource_id=view.id,
        meta={"name": view.name},
    )
    db.commit()
    db.refresh(view)
    return _view_to_out(view)


@router.patch("/{view_id}/alert", response_model=SavedViewOut)
def toggle_view_alert(
    view_id: int,
    body: AlertToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enable or disable the daily new-company alert for a saved view."""
    view = db.query(UserView).filter(UserView.id == view_id, UserView.user_id == current_user.id).first()
    if not view:
        raise HTTPException(status_code=404, detail="View not found")
    view.alert_enabled = body.enabled
    # Reset baseline so the first check after enabling doesn't spam
    if body.enabled and view.alert_last_count is None:
        view.alert_last_count = None  # will be set on first check run
    log_activity(
        db, action="view_alert_toggled",
        user_id=current_user.id, org_id=current_user.org_id,
        resource_type="view", resource_id=view_id,
        meta={"enabled": body.enabled, "view_name": view.name},
    )
    db.commit()
    db.refresh(view)
    return _view_to_out(view)


@router.delete("/{view_id}", status_code=204)
def delete_view(view_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    view = db.query(UserView).filter(UserView.id == view_id, UserView.user_id == current_user.id).first()
    if not view:
        raise HTTPException(status_code=404, detail="View not found")
    log_activity(
        db, action="view_deleted",
        user_id=current_user.id, org_id=current_user.org_id,
        resource_type="view", resource_id=view_id,
        meta={"name": view.name},
    )
    db.delete(view)
    db.commit()
