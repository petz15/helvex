"""Saved dashboard views (per user)."""
import json
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.user_view import UserView

router = APIRouter(prefix="/views", tags=["views"])

class SaveViewRequest(BaseModel):
    name: str
    filters: dict  # the CompanyFilters object as a dict

class SavedViewOut(BaseModel):
    id: int
    name: str
    filters: dict
    created_at: str
    model_config = {"from_attributes": False}

@router.get("", response_model=list[SavedViewOut])
def list_views(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(UserView).filter(UserView.user_id == current_user.id).order_by(UserView.created_at.desc()).all()
    return [SavedViewOut(id=r.id, name=r.name, filters=json.loads(r.filters_json), created_at=r.created_at.isoformat()) for r in rows]

@router.post("", response_model=SavedViewOut, status_code=201)
def save_view(body: SaveViewRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    view = UserView(user_id=current_user.id, name=body.name.strip(), filters_json=json.dumps(body.filters))
    db.add(view)
    db.commit()
    db.refresh(view)
    return SavedViewOut(id=view.id, name=view.name, filters=body.filters, created_at=view.created_at.isoformat())

@router.delete("/{view_id}", status_code=204)
def delete_view(view_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    view = db.query(UserView).filter(UserView.id == view_id, UserView.user_id == current_user.id).first()
    if not view:
        raise HTTPException(status_code=404, detail="View not found")
    db.delete(view)
    db.commit()
