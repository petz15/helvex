"""REST API for application settings and boilerplate patterns."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
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
    # "serper" | "scrapingdog"
    google_search_provider: str = "serper"
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
    truncate: bool

    model_config = {"from_attributes": True}


class BoilerplateCreate(BaseModel):
    pattern: str
    description: str | None = None
    example: str | None = None
    truncate: bool = False


class GoogleStopwordOut(BaseModel):
    id: int
    value: str
    description: str | None
    active: bool

    model_config = {"from_attributes": True}


class GoogleStopwordCreate(BaseModel):
    value: str
    description: str | None = None


class GoogleDirectoryDomainOut(BaseModel):
    id: int
    value: str
    description: str | None
    active: bool

    model_config = {"from_attributes": True}


class GoogleDirectoryDomainCreate(BaseModel):
    value: str
    description: str | None = None


class TfidfStopwordOut(BaseModel):
    id: int
    value: str
    description: str | None
    active: bool

    model_config = {"from_attributes": True}


class TfidfStopwordCreate(BaseModel):
    value: str
    description: str | None = None


# ── Settings ───────────────────────────────────────────────────────────────────

_SENSITIVE_KEYS = frozenset({"anthropic_api_key"})


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    current = crud.get_all_settings(db)
    defaults = get_default_scoring_config()
    merged = {**defaults, **current}
    # Never return sensitive credential values to non-superadmin callers.
    # Replace them with a sentinel so the frontend can show "••• (set)" vs "(not set)".
    if not current_user.is_superadmin:
        for key in _SENSITIVE_KEYS:
            if key in merged:
                merged[key] = "***" if merged[key].strip() else ""
    return merged


@router.put("/settings")
def save_settings(body: SettingsBody, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    crud.set_setting(db, "google_search_enabled", "true" if body.google_search_enabled else "false")
    try:
        crud.set_setting(db, "google_daily_quota", str(max(1, int(body.google_daily_quota))))
    except (ValueError, TypeError):
        crud.set_setting(db, "google_daily_quota", "100")
    provider = body.google_search_provider.strip().lower()
    if provider not in ("serper", "scrapingdog"):
        provider = "serper"
    crud.set_setting(db, "google_search_provider", provider)

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
        truncate=body.truncate,
    )
    return row


@router.patch("/boilerplate/{pattern_id}/toggle", response_model=BoilerplateOut)
def toggle_boilerplate(pattern_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_boilerplate_pattern(db, pattern_id)
    if not row:
        raise HTTPException(status_code=404, detail="Pattern not found")
    crud.update_boilerplate_pattern(db, row, active=not row.active)
    crud.invalidate_boilerplate_cache()
    return row


@router.patch("/boilerplate/{pattern_id}/toggle-truncate", response_model=BoilerplateOut)
def toggle_boilerplate_truncate(pattern_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_boilerplate_pattern(db, pattern_id)
    if not row:
        raise HTTPException(status_code=404, detail="Pattern not found")
    crud.update_boilerplate_pattern(db, row, truncate=not row.truncate)
    crud.invalidate_boilerplate_cache()
    return row


@router.delete("/boilerplate/{pattern_id}", status_code=204)
def delete_boilerplate(pattern_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_boilerplate_pattern(db, pattern_id)
    if not row:
        raise HTTPException(status_code=404, detail="Pattern not found")
    crud.delete_boilerplate_pattern(db, row)


# ── Google scoring filters ────────────────────────────────────────────────────

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")


def _normalize_stopword(value: str) -> str:
    return value.strip().lower()


def _normalize_domain(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = cleaned.removeprefix("http://").removeprefix("https://")
    cleaned = cleaned.split("/", 1)[0]
    cleaned = cleaned.removeprefix("www.")
    return cleaned


@router.get("/google-stopwords", response_model=list[GoogleStopwordOut])
def list_google_stopwords(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return crud.list_google_stopwords(db)


@router.post("/google-stopwords", response_model=GoogleStopwordOut, status_code=201)
def create_google_stopword(body: GoogleStopwordCreate, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    value = _normalize_stopword(body.value)
    if not value:
        raise HTTPException(status_code=400, detail="Stopword cannot be empty")
    try:
        return crud.create_google_stopword(db, value=value, description=body.description, active=True)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Stopword already exists")


@router.patch("/google-stopwords/{row_id}/toggle", response_model=GoogleStopwordOut)
def toggle_google_stopword(row_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_google_stopword(db, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Stopword not found")
    return crud.update_google_stopword(db, row, active=not row.active)


@router.delete("/google-stopwords/{row_id}", status_code=204)
def delete_google_stopword(row_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_google_stopword(db, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Stopword not found")
    crud.delete_google_stopword(db, row)


@router.get("/google-directory-domains", response_model=list[GoogleDirectoryDomainOut])
def list_google_directory_domains(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return crud.list_google_directory_domains(db)


@router.post("/google-directory-domains", response_model=GoogleDirectoryDomainOut, status_code=201)
def create_google_directory_domain(body: GoogleDirectoryDomainCreate, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    value = _normalize_domain(body.value)
    if not value:
        raise HTTPException(status_code=400, detail="Domain cannot be empty")
    if not _DOMAIN_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid domain format")
    try:
        return crud.create_google_directory_domain(db, value=value, description=body.description, active=True)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Domain already exists")


@router.patch("/google-directory-domains/{row_id}/toggle", response_model=GoogleDirectoryDomainOut)
def toggle_google_directory_domain(row_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_google_directory_domain(db, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Domain not found")
    return crud.update_google_directory_domain(db, row, active=not row.active)


@router.delete("/google-directory-domains/{row_id}", status_code=204)
def delete_google_directory_domain(row_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_google_directory_domain(db, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Domain not found")
    crud.delete_google_directory_domain(db, row)


@router.get("/tfidf-stopwords", response_model=list[TfidfStopwordOut])
def list_tfidf_stopwords(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return crud.list_tfidf_stopwords(db)


@router.post("/tfidf-stopwords", response_model=TfidfStopwordOut, status_code=201)
def create_tfidf_stopword(body: TfidfStopwordCreate, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    value = _normalize_stopword(body.value)
    if not value:
        raise HTTPException(status_code=400, detail="Stopword cannot be empty")
    try:
        return crud.create_tfidf_stopword(db, value=value, description=body.description, active=True)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Stopword already exists")


@router.patch("/tfidf-stopwords/{row_id}/toggle", response_model=TfidfStopwordOut)
def toggle_tfidf_stopword(row_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_tfidf_stopword(db, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Stopword not found")
    return crud.update_tfidf_stopword(db, row, active=not row.active)


@router.delete("/tfidf-stopwords/{row_id}", status_code=204)
def delete_tfidf_stopword(row_id: int, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    row = crud.get_tfidf_stopword(db, row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Stopword not found")
    crud.delete_tfidf_stopword(db, row)


@router.post("/settings/seed-defaults", status_code=200)
def seed_defaults(db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    """Seed the DB with all built-in default stopwords and directory domains.
    Safe to call multiple times — skips rows that already exist."""
    gs = crud.seed_default_google_stopwords(db)
    gd = crud.seed_default_directory_domains(db)
    ts = crud.seed_default_tfidf_stopwords(db)
    return {"google_stopwords": gs, "directory_domains": gd, "tfidf_stopwords": ts}
