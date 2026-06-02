"""Google search enrichment pipeline for company websites."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import crud
from app.clients.google_search_client import search_website
from app.config import settings
from app.models.company import Company
from app.schemas.company import CompanyUpdate
from app.services._pipeline_utils import _is_control_signal_exception
from app.services.scoring import (
    distance_to_muri_km,
    fallback_result_score,
    is_irrelevant_result,
    is_social_lead_domain,
    score_result,
)

logger = logging.getLogger(__name__)


def _active_provider(db: Session) -> str:
    """Return 'serper' or 'scrapingdog' based on the DB setting."""
    return (crud.get_setting(db, "google_search_provider", "serper") or "serper").strip().lower()


def _google_search_ready(db: Session) -> tuple[bool, str | None]:
    enabled = (crud.get_setting(db, "google_search_enabled", "true") or "").strip().lower() == "true"
    if not enabled:
        return False, "Google search is disabled in Settings"
    provider = _active_provider(db)
    if provider == "scrapingdog":
        if not settings.scrapingdog_api_key:
            return False, "SCRAPINGDOG_API_KEY is not configured (website search cannot run)"
    else:
        if not settings.serper_api_key:
            return False, "SERPER_API_KEY is not configured (website search cannot run)"
    return True, None


def _google_scoring_overrides(db: Session) -> tuple[set[str], set[str]]:
    """Load runtime Google scoring filters exclusively from DB tables."""
    stopwords = crud.get_active_google_stopwords(db)
    directory_domains = crud.get_active_google_directory_domains(db)
    return stopwords, directory_domains


def _score_google_results_for_company(db: Session, company: Company, raw_results: list[dict]) -> list[dict]:
    """Score and sort Google results for one company using current scoring rules."""
    if not raw_results:
        return []

    purpose_stopwords, directory_domains = _google_scoring_overrides(db)

    top_window = raw_results[: min(3, len(raw_results))]
    irrelevant_count = sum(
        1 for rr in top_window
        if is_irrelevant_result(rr, company_name=company.name, directory_domains=directory_domains)
    )
    use_fallback = bool(top_window) and irrelevant_count >= ((len(top_window) + 1) // 2)

    scored: list[dict] = []
    for idx, rr in enumerate(raw_results):
        row = {
            "title": rr.get("title", "") or "",
            "link": rr.get("link", "") or "",
            "snippet": rr.get("snippet", "") or "",
        }
        if use_fallback:
            s = fallback_result_score(
                row,
                municipality=company.municipality,
                canton=company.canton,
                legal_form=company.legal_form,
                address=company.address,
                directory_domains=directory_domains,
            )
        else:
            s = score_result(
                row,
                company_name=company.name,
                municipality=company.municipality,
                canton=company.canton,
                purpose=company.purpose,
                legal_form=company.legal_form,
                address=company.address,
                directory_domains=directory_domains,
                purpose_stopwords=purpose_stopwords,
                position=idx,
            )
        scored.append({**row, "score": s})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def enrich_company_website(db: Session, company: Company, *, num: int = 10) -> tuple[bool, str | None]:
    """Fetch top-N Google results, score each against the company profile, and persist.

    Stores all scored results in google_search_results_raw (JSON).
    For ScrapingDog, also stores the complete provider JSON in google_search_full_raw.
    Sets website_url and web_score to the best-scoring result.
    Always sets website_checked_at so callers know a search was attempted.
    """
    from app.metrics import record_api_call, record_api_error

    provider = _active_provider(db)
    now = datetime.now(tz=timezone.utc)
    _t0 = time.monotonic()
    full_raw: dict | None = None

    try:
        if provider == "scrapingdog":
            from app.clients.scrapingdog_search_client import search_website as sd_search
            results, full_raw = sd_search(
                company.name,
                num=num,
                zip_code=company.address_zip,
                municipality=company.municipality,
                purpose_language=company.purpose_language,
            )
        else:
            results = search_website(company.name, num=num)

        duration = time.monotonic() - _t0
        record_api_call(provider, duration, 200)
    except Exception as exc:
        duration = time.monotonic() - _t0
        record_api_error(provider, "unknown")
        logger.error("Google search failed for company_id=%d provider=%s: %s", company.id, provider, exc, exc_info=True)
        return False, None

    logger.debug(
        "google_search provider=%s company_id=%d name=%r results=%d latency_ms=%.0f",
        provider, company.id, company.name, len(results), duration * 1000,
    )

    if not results:
        crud.update_company(
            db,
            company,
            CompanyUpdate(
                website_checked_at=now,
                google_search_results_raw=json.dumps([]),
                google_search_full_raw=json.dumps(full_raw) if full_raw is not None else None,
            ),
        )
        return False, None

    raw_results = [{"title": r.title, "link": r.link, "snippet": r.snippet or ""} for r in results]
    scored = _score_google_results_for_company(db, company, raw_results)
    best = scored[0]
    social_media_only = is_social_lead_domain(best["link"])

    crud.update_company(
        db,
        company,
        CompanyUpdate(
            website_url=best["link"],
            web_score=best["score"],
            social_media_only=social_media_only,
            website_checked_at=now,
            google_search_results_raw=json.dumps(scored),
            google_search_full_raw=json.dumps(full_raw) if full_raw is not None else None,
        ),
    )
    return True, best["link"]


def rescore_from_stored_results(db: Session, company: Company) -> bool:
    """Re-score web_score from already-stored google_search_results_raw.

    Called after a Zefix detail refresh so freshly fetched purpose / municipality /
    canton data is applied to the existing Google results without a new API call.
    Returns True if scoring was applied and saved, False otherwise.
    """
    if not company.google_search_results_raw:
        return False
    try:
        stored: list[dict] = json.loads(company.google_search_results_raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if not stored:
        return False

    rescored = _score_google_results_for_company(db, company, stored)
    best = rescored[0]
    crud.update_company(
        db,
        company,
        CompanyUpdate(
            website_url=best["link"],
            web_score=best["score"],
            social_media_only=is_social_lead_domain(best["link"]),
            google_search_results_raw=json.dumps(rescored),
        ),
    )
    return True


def recalculate_google_scores(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Recompute web_score from stored Google results for all companies."""
    stats: dict[str, Any] = {"updated": 0, "skipped": 0, "errors": []}

    total = db.query(Company).count()
    offset = max(0, min(resume_from, total))

    while True:
        batch = (
            db.query(Company)
            .order_by(
                (
                    func.coalesce(Company.ai_score * 0.70, 0)
                    + func.coalesce(Company.web_score * 0.20, 0)
                    + func.coalesce(Company.flex_score * 0.10, 0)
                ).desc(),
                Company.id.asc(),
            )
            .offset(offset)
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        for company in batch:
            try:
                if not company.google_search_results_raw:
                    stats["skipped"] += 1
                    continue

                raw_results = json.loads(company.google_search_results_raw)
                if not isinstance(raw_results, list) or not raw_results:
                    stats["skipped"] += 1
                    continue

                rescored = _score_google_results_for_company(db, company, raw_results)
                if not rescored:
                    stats["skipped"] += 1
                    continue

                best = rescored[0]
                company.website_url = best["link"]
                company.web_score = best["score"]
                company.social_media_only = is_social_lead_domain(best["link"])
                company.google_search_results_raw = json.dumps(rescored)
                company.combined_score = Company.compute_combined_score(
                    company.ai_score, company.noga_confidence, company.purpose_keywords
                )
                stats["updated"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google rescore failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")

        db.commit()
        offset += len(batch)

        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    return stats


def run_batch_collect(
    db: Session,
    *,
    limit: int = 200,
    only_missing_website: bool = True,
    refresh_zefix: bool = False,
    run_google: bool = True,
    resume_from: int = 0,
    progress_cb: Any = None,
    canton: str | None = None,
    min_flex_score: int | None = None,
    min_ai_score: int | None = None,
    purpose_keywords: str | None = None,
    tfidf_cluster: str | None = None,
    review_status: str | None = None,
    order_by: str = "flex_score_desc",
    noga_code: str | None = None,
    status_cb: Any = None,
    abort_cb: Any = None,
) -> dict[str, Any]:
    """Run a recurring batch process over companies already in the DB."""
    import heapq

    stats: dict[str, Any] = {
        "selected": 0,
        "zefix_refreshed": 0,
        "google_enriched": 0,
        "google_no_result": 0,
        "errors": [],
        "warnings": [],
    }

    if run_google:
        ok, reason = _google_search_ready(db)
        if not ok:
            stats["warnings"].append(f"Google enrichment skipped: {reason}.")
            run_google = False

    if run_google:
        quota = int(crud.get_setting(db, "google_daily_quota", str(settings.google_daily_quota)))
        searches_today = crud.get_company_stats(db)["searches_today"]
        available = max(0, quota - searches_today)
        if available == 0:
            stats["warnings"].append(
                f"Daily Google quota reached: {searches_today}/{quota} searches used today. "
                f"Google enrichment skipped. Reset at midnight UTC or raise quota in settings."
            )
            run_google = False
        elif limit > available:
            stats["warnings"].append(
                f"Batch limited to {available} companies (quota: {searches_today}/{quota} searches used today)."
            )
            limit = available

    query = db.query(Company)
    if only_missing_website:
        query = query.filter(or_(Company.website_url.is_(None), Company.website_url == ""))
    if canton:
        query = query.filter(Company.canton == canton.strip().upper())
    if min_flex_score is not None:
        query = query.filter(Company.flex_score >= min_flex_score)
    if min_ai_score is not None:
        query = query.filter(Company.ai_score >= min_ai_score)
    if purpose_keywords:
        kw_terms = [t.strip() for t in purpose_keywords.split(",") if t.strip()]
        if kw_terms:
            query = query.filter(
                or_(*[Company.purpose_keywords.ilike(f"%{kw}%") for kw in kw_terms])
            )
    if tfidf_cluster:
        query = query.filter(Company.tfidf_cluster.ilike(f"%{tfidf_cluster}%"))
    if review_status:
        if review_status == "pending":
            query = query.filter(Company.review_status.is_(None))
        else:
            query = query.filter(Company.review_status == review_status)
    if noga_code:
        query = query.filter(Company.noga_code.like(f"{noga_code.strip()}%"))

    keep_n = max(0, int(limit)) + max(0, int(resume_from))
    if keep_n <= 0:
        return stats

    def _combined_score_values(ai_score, web_score, flex_score) -> float:
        _w = [(ai_score, 0.70), (web_score, 0.20), (flex_score, 0.10)]
        present = [(s, w) for s, w in _w if s is not None]
        if not present:
            return 0.0
        total_w = sum(w for _, w in present)
        return float(sum(float(s) * w for s, w in present) / total_w)

    # Select and order companies based on order_by param.
    # combined_score_desc uses a heap over all rows (handles NULL scores gracefully).
    # Other orderings use a direct DB query with ORDER BY + LIMIT for efficiency.
    if order_by == "flex_score_desc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.flex_score.desc().nulls_last(), Company.id.desc())
            .limit(keep_n).all()
        ]
    elif order_by == "last_enriched_asc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.website_checked_at.asc().nulls_first(), Company.id.desc())
            .limit(keep_n).all()
        ]
    elif order_by == "created_asc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.id.asc())
            .limit(keep_n).all()
        ]
    else:
        # combined_score_desc (default): heap selection matching original behaviour
        heap: list[tuple[tuple[float, float, int], int]] = []

        row_query = query.with_entities(
            Company.id,
            Company.ai_score,
            Company.web_score,
            Company.flex_score,
            Company.canton,
            Company.municipality,
            Company.lat,
            Company.lon,
        )

        for row in row_query.yield_per(1000):
            company_id = int(row.id)
            score = _combined_score_values(row.ai_score, row.web_score, row.flex_score)
            distance = distance_to_muri_km(
                canton=row.canton,
                municipality=row.municipality,
                lat=row.lat,
                lon=row.lon,
            )
            dist_val = float(distance) if distance is not None else float("inf")
            nkey = (score, -dist_val, -company_id)

            if len(heap) < keep_n:
                heapq.heappush(heap, (nkey, company_id))
            else:
                if nkey > heap[0][0]:
                    heapq.heapreplace(heap, (nkey, company_id))

        planned_ids = [cid for _, cid in sorted(heap, key=lambda x: x[0], reverse=True)]
    stats["selected"] = min(len(planned_ids), max(0, int(limit)) + max(0, int(resume_from)))

    start_idx = max(0, min(resume_from, len(planned_ids)))
    company_ids = planned_ids[start_idx: start_idx + max(0, int(limit))]
    companies = [db.get(Company, cid) for cid in company_ids]
    companies = [c for c in companies if c is not None]

    for i, company in enumerate(companies, start=start_idx + 1):
        current = company
        if refresh_zefix:
            try:
                from app.services.zefix_import import import_company_from_zefix_uid
                refreshed, _ = import_company_from_zefix_uid(
                    db,
                    company.uid,
                    pause_on_zefix_500=True,
                    status_cb=status_cb,
                    abort_cb=abort_cb,
                )
                current = refreshed
                stats["zefix_refreshed"] += 1
            except Exception as exc:  # noqa: BLE001
                if _is_control_signal_exception(exc):
                    raise
                logger.warning("Zefix refresh failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"Zefix refresh {company.uid} [{type(exc).__name__}]: {exc}")

        if run_google:
            try:
                enriched, _ = enrich_company_website(db, current)
                if enriched:
                    stats["google_enriched"] += 1
                else:
                    stats["google_no_result"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google search failed for %s: %s", current.uid, exc)
                stats["errors"].append(f"Google search {current.uid} [{type(exc).__name__}]: {exc}")

        if progress_cb:
            progress_cb(i, stats["selected"], stats)

    return stats
