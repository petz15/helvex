"""REST API for application settings and boilerplate patterns."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.auth import get_current_user, require_superadmin
from app.database import get_db
from app.models.user import User
from app.services.scoring import get_default_scoring_config

router = APIRouter(tags=["settings"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class SettingsBody(BaseModel):
    google_search_enabled: bool = True
    google_daily_quota: str = "100"
    # Scoring
    scoring_target_clusters: str = ""
    scoring_cluster_hit_points: str = "10"
    scoring_exclude_clusters: str = ""
    scoring_cluster_exclude_points: str = "10"
    scoring_target_keywords: str = ""
    scoring_keyword_hit_points: str = "10"
    scoring_exclude_keywords: str = ""
    scoring_keyword_exclude_points: str = "10"
    scoring_origin_lat: str = "46.9266"
    scoring_origin_lon: str = "7.4817"
    scoring_dist_15km: str = "20"
    scoring_dist_40km: str = "10"
    scoring_dist_80km: str = "5"
    scoring_dist_130km: str = "0"
    scoring_dist_far: str = "-5"
    scoring_legal_form_scores: str = "gmbh:20,sarl:20,sàrl:20,einzelfirma:15,eg:15,kg:10,og:8,ag:8,sa:8,stiftung:3,verein:2"
    scoring_legal_form_default: str = "5"
    scoring_cancelled_score: str = "5"
    scoring_claude_max_purpose_chars: str = "800"
    # Claude
    anthropic_api_key: str = ""
    claude_target_description: str = ""
    claude_classify_prompt: str = ""
    claude_classify_categories: str = ""


class BoilerplateOut(BaseModel):
    id: int
    pattern: str
    description: str | None
    example: str | None
    active: bool

    model_config = {"from_attributes": True}


class BoilerplateCreate(BaseModel):
    pattern: str
    description: str | None = None
    example: str | None = None


# ── Settings ───────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    current = crud.get_all_settings(db)
    defaults = get_default_scoring_config()
    # Merge defaults for any missing keys
    return {**defaults, **current}


@router.put("/settings")
def save_settings(body: SettingsBody, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    crud.set_setting(db, "google_search_enabled", "true" if body.google_search_enabled else "false")
    try:
        crud.set_setting(db, "google_daily_quota", str(max(1, int(body.google_daily_quota))))
    except (ValueError, TypeError):
        crud.set_setting(db, "google_daily_quota", "100")

    defaults = get_default_scoring_config()
    text_fields = {
        "scoring_target_clusters": body.scoring_target_clusters,
        "scoring_exclude_clusters": body.scoring_exclude_clusters,
        "scoring_target_keywords": body.scoring_target_keywords,
        "scoring_exclude_keywords": body.scoring_exclude_keywords,
        "scoring_legal_form_scores": body.scoring_legal_form_scores,
    }
    for key, value in text_fields.items():
        crud.set_setting(db, key, value.strip())

    numeric_fields = {
        "scoring_cluster_hit_points": body.scoring_cluster_hit_points,
        "scoring_cluster_exclude_points": body.scoring_cluster_exclude_points,
        "scoring_keyword_hit_points": body.scoring_keyword_hit_points,
        "scoring_keyword_exclude_points": body.scoring_keyword_exclude_points,
        "scoring_origin_lat": body.scoring_origin_lat,
        "scoring_origin_lon": body.scoring_origin_lon,
        "scoring_dist_15km": body.scoring_dist_15km,
        "scoring_dist_40km": body.scoring_dist_40km,
        "scoring_dist_80km": body.scoring_dist_80km,
        "scoring_dist_130km": body.scoring_dist_130km,
        "scoring_dist_far": body.scoring_dist_far,
        "scoring_legal_form_default": body.scoring_legal_form_default,
        "scoring_cancelled_score": body.scoring_cancelled_score,
    }
    for key, value in numeric_fields.items():
        v = value.strip()
        try:
            float(v)
            crud.set_setting(db, key, v)
        except ValueError:
            crud.set_setting(db, key, defaults.get(key, v))

    crud.set_setting(db, "anthropic_api_key", body.anthropic_api_key.strip())
    crud.set_setting(db, "claude_target_description", body.claude_target_description.strip())
    crud.set_setting(db, "claude_classify_prompt", body.claude_classify_prompt.strip())
    crud.set_setting(db, "claude_classify_categories", body.claude_classify_categories.strip())
    try:
        crud.set_setting(db, "scoring_claude_max_purpose_chars", str(int(body.scoring_claude_max_purpose_chars.strip())))
    except (ValueError, AttributeError):
        crud.set_setting(db, "scoring_claude_max_purpose_chars", "800")

    return {"status": "saved"}


# ── Boilerplate patterns ───────────────────────────────────────────────────────

@router.get("/boilerplate", response_model=list[BoilerplateOut])
def list_boilerplate(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return crud.list_boilerplate_patterns(db)


@router.post("/boilerplate", response_model=BoilerplateOut, status_code=201)
def create_boilerplate(body: BoilerplateCreate, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    import re as _re
    if not body.pattern.strip():
        raise HTTPException(status_code=400, detail="Pattern cannot be empty")
    try:
        _re.compile(body.pattern, _re.IGNORECASE)
    except _re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}")
    row = crud.create_boilerplate_pattern(
        db,
        pattern=body.pattern.strip(),
        description=body.description,
        example=body.example,
        active=True,
    )
    return row


@router.patch("/boilerplate/{pattern_id}/toggle", response_model=BoilerplateOut)
def toggle_boilerplate(pattern_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_boilerplate_pattern(db, pattern_id)
    if not row:
        raise HTTPException(status_code=404, detail="Pattern not found")
    crud.update_boilerplate_pattern(db, row, active=not row.active)
    return row


@router.delete("/boilerplate/{pattern_id}", status_code=204)
def delete_boilerplate(pattern_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_boilerplate_pattern(db, pattern_id)
    if not row:
        raise HTTPException(status_code=404, detail="Pattern not found")
    crud.delete_boilerplate_pattern(db, row)
