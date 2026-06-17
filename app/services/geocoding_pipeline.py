"""Geocoding and flex-score recalculation pipeline."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import crud
from app.clients.geocoding_client import geocode_address
from app.models.company import Company
from app.schemas.company import CompanyUpdate
from app.services.scoring import compute_flex_score_breakdown, normalize_raw_scores

logger = logging.getLogger(__name__)


def _refresh_combined(company: Company) -> None:
    company.combined_score = Company.compute_combined_score(
        company.ai_score, company.noga_confidence, company.purpose_keywords,
        web_score=company.web_score,
    )


def _load_scoring_config(db: Session) -> dict[str, str]:
    from app.services.scoring import get_default_scoring_config
    defaults = get_default_scoring_config()
    return {key: crud.get_setting(db, key, val) for key, val in defaults.items()}


def _load_scoring_config_for_org(db: Session, org_id: int | None) -> dict[str, str]:
    from app.services.scoring import get_default_scoring_config
    defaults = get_default_scoring_config()
    return {key: crud.get_effective_setting(db, key, org_id=org_id, default=val) for key, val in defaults.items()}


def geocode_and_update_company(db: Session, company: Company) -> bool:
    """Geocode the company's address and persist lat/lon + recompute flex_score.

    Skipped when the company has no address or coordinates are already set.
    Returns True if coordinates were successfully obtained and saved.
    """
    if not company.address:
        return False
    if company.lat is not None and company.lon is not None:
        return False

    coords = geocode_address(company.address)
    if coords is None:
        return False

    lat, lon = coords
    scoring_config = _load_scoring_config(db)
    score_breakdown = compute_flex_score_breakdown(
        legal_form=company.legal_form,
        legal_form_short_name=company.legal_form_short_name,
        status=company.status,
        canton=company.canton,
        municipality=company.municipality,
        lat=lat,
        lon=lon,
        purpose_keywords=company.purpose_keywords,
        tfidf_cluster=company.tfidf_cluster,
        noga_path=company.noga_path,
        noga_code=company.noga_code,
        noga_level=company.noga_level,
        config=scoring_config,
    )
    new_flex = int(score_breakdown["final_score"])
    crud.update_company(
        db,
        company,
        CompanyUpdate(
            lat=lat,
            lon=lon,
            flex_score=new_flex,
            flex_score_breakdown=json.dumps(score_breakdown),
            flex_scored_at=datetime.now(tz=timezone.utc),
            combined_score=Company.compute_combined_score(company.ai_score, company.noga_confidence, company.purpose_keywords, web_score=company.web_score),
        ),
    )
    return True


def recalculate_flex_scores(
    db: Session,
    *,
    org_id: int | None = None,
    batch_size: int = 500,
    resume_from: int = 0,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Recompute and normalise flex_score for every company.

    Two-pass: (1) compute raw scores (geocoding if needed), (2) min-max normalise.
    Returns ``{"updated": int, "geocoded": int, "errors": list[str]}``.
    """
    stats: dict[str, Any] = {"updated": 0, "geocoded": 0, "errors": []}
    scoring_config = _load_scoring_config_for_org(db, org_id)
    cancelled_score = int(scoring_config.get("scoring_cancelled_score", "5"))

    raw_scores: dict[int, int | None] = {}
    breakdowns: dict[int, dict] = {}

    total = db.query(func.count(Company.id)).scalar() or 0
    offset = max(0, min(resume_from, total))

    while True:
        batch = db.query(Company).order_by(Company.id.asc()).offset(offset).limit(batch_size).all()
        if not batch:
            break
        for company in batch:
            try:
                if company.lat is None and company.lon is None and company.address:
                    coords = geocode_address(company.address)
                    if coords:
                        company.lat, company.lon = coords
                        stats["geocoded"] += 1
                bd = compute_flex_score_breakdown(
                    legal_form=company.legal_form,
                    legal_form_short_name=company.legal_form_short_name,
                    status=company.status,
                    canton=company.canton,
                    municipality=company.municipality,
                    lat=company.lat,
                    lon=company.lon,
                    purpose_keywords=company.purpose_keywords,
                    tfidf_cluster=company.tfidf_cluster,
                    noga_path=company.noga_path,
                    noga_code=company.noga_code,
                    noga_level=company.noga_level,
                    config=scoring_config,
                )
                breakdowns[company.id] = bd
                raw_scores[company.id] = None if bd.get("cancelled") else int(bd["raw_total"])
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")
        db.commit()
        offset += len(batch)
        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    normalised = normalize_raw_scores(raw_scores, cancelled_score=cancelled_score)

    write_total = len(normalised)
    write_done = 0
    offset = 0
    while True:
        batch = db.query(Company).order_by(Company.id.asc()).offset(offset).limit(batch_size).all()
        if not batch:
            break
        for company in batch:
            if company.id not in normalised:
                continue
            bd = breakdowns.get(company.id, {})
            bd["final_score"] = normalised[company.id]
            company.flex_score = normalised[company.id]
            company.flex_score_breakdown = json.dumps(bd)
            company.flex_scored_at = datetime.now(tz=timezone.utc)
            _refresh_combined(company)
            stats["updated"] += 1
            write_done += 1
        db.commit()
        offset += len(batch)
        if progress_cb:
            progress_cb(write_done, write_total, {**stats, "_phase": "writing"})

    return stats


def re_geocode_all_companies(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Re-geocode every company that has an address, overwriting existing lat/lon.

    Used for the one-time upgrade from PLZ-centroid to building-level coordinates.
    Returns ``{"geocoded": int, "failed": int, "skipped": int, "errors": list[str]}``.
    """
    stats: dict[str, Any] = {"geocoded": 0, "failed": 0, "skipped": 0, "errors": []}

    total = db.query(func.count(Company.id)).filter(Company.address.isnot(None)).scalar() or 0
    offset = max(0, min(resume_from, total))

    while True:
        batch = (
            db.query(Company)
            .filter(Company.address.isnot(None))
            .order_by(Company.id.asc())
            .offset(offset)
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        for company in batch:
            try:
                coords = geocode_address(company.address)
                if coords:
                    company.lat, company.lon = coords
                    stats["geocoded"] += 1
                else:
                    stats["failed"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Geocode failed for company %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")

        db.commit()
        offset += len(batch)

        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    return stats
