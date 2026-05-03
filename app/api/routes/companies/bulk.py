"""Bulk update and bulk tag routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.activity import log_activity

router = APIRouter()


class BulkUpdateBody(BaseModel):
    company_ids: list[int]
    field: str
    value: str | None


@router.post("/bulk-update", summary="Bulk update a status field on multiple companies")
def bulk_update_companies(
    body: BulkUpdateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        updated = crud.bulk_update_status(db, body.company_ids, body.field, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    log_activity(
        db, action="company_bulk_updated",
        user_id=current_user.id, org_id=current_user.org_id,
        meta={"field": body.field, "value": body.value, "count": updated},
    )
    db.commit()
    return {"updated": updated}


class BulkTagBody(BaseModel):
    company_ids: list[int]
    tag: str
    action: str  # "add" | "remove"


@router.post("/bulk-tag", summary="Add or remove a tag across multiple companies")
def bulk_tag_companies(
    body: BulkTagBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.action not in ("add", "remove"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action must be 'add' or 'remove'")
    updated = crud.bulk_update_tags(db, body.company_ids, body.tag, body.action)
    log_activity(
        db, action="company_bulk_tagged",
        user_id=current_user.id, org_id=current_user.org_id,
        meta={"tag": body.tag, "action": body.action, "count": updated},
    )
    db.commit()
    return {"updated": updated}
