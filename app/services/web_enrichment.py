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
from app.crud import crawler as crawler_crud
from app.models.company import Company
from app.schemas.company import CompanyUpdate
from app.services._pipeline_utils import _is_control_signal_exception
from app.services.scoring import (
    compute_seo_visibility_score,
    distance_to_muri_km,
    extract_serp_features,
    fallback_result_score,
    find_organic_position,
    is_irrelevant_result,
    score_result,
)

logger = logging.getLogger(__name__)


def _active_provider(db: Session) -> str:
    """Return 'serper' or 'scrapingdog' based on the DB setting."""
    return (crud.get_setting(db, "google_search_provider", "serper") or "serper").strip().lower()


def _get_search_api_key(db: Session, provider: str) -> str:
    """Return the active API key for the given provider: DB value takes priority over env."""
    db_key = (crud.get_setting(db, f"{provider}_api_key", "") or "").strip()
    if db_key:
        return db_key
    if provider == "scrapingdog":
        return settings.scrapingdog_api_key or ""
    return settings.serper_api_key or ""


def _google_search_ready(db: Session) -> tuple[bool, str | None]:
    enabled = (crud.get_setting(db, "google_search_enabled", "true") or "").strip().lower() == "true"
    if not enabled:
        return False, "Google search is disabled in Settings"
    provider = _active_provider(db)
    if not _get_search_api_key(db, provider):
        key_name = "SCRAPINGDOG_API_KEY" if provider == "scrapingdog" else "SERPER_API_KEY"
        return False, f"{key_name} is not configured (website search cannot run)"
    return True, None


def _google_scoring_overrides(db: Session) -> tuple[set[str], set[str]]:
    """Load runtime Google scoring filters exclusively from DB tables."""
    stopwords = crud.get_active_google_stopwords(db)
    directory_domains = crud.get_active_google_directory_domains(db)
    return stopwords, directory_domains


def _enrich_organic_result(organic: dict) -> dict:
    """Return {title, link, snippet} with snippet enriched from all available provider fields.

    Both Serper and ScrapingDog return richer signals alongside the basic snippet:
    - ScrapingDog: inline_snippet (often the full visible page text), extended_sitelinks titles
    - Serper: sitelinks with per-link snippets

    Merging these into the scored snippet means municipality/name signals in inline
    text or navigation labels are picked up even when the main snippet is very short.
    """
    title = (organic.get("title") or "").strip()
    link = (organic.get("link") or "").strip()
    snippet = (organic.get("snippet") or "").strip()

    parts: list[str] = [snippet] if snippet else []

    # ScrapingDog: inline_snippet — often the best content signal
    inline = (organic.get("inline_snippet") or "").strip()
    if inline and inline != snippet:
        parts.append(inline)

    # ScrapingDog: extended_sitelinks [{title, link}, ...]
    for sl in (organic.get("extended_sitelinks") or [])[:5]:
        t = (sl.get("title") or "").strip()
        if t and t not in title:
            parts.append(t)

    # Serper: sitelinks [{title, link, snippet?}, ...]
    for sl in (organic.get("sitelinks") or [])[:5]:
        t = (sl.get("title") or "").strip()
        sl_snip = (sl.get("snippet") or "").strip()
        if t and t not in title:
            parts.append(t)
        if sl_snip:
            parts.append(sl_snip)

    return {"title": title, "link": link, "snippet": " ".join(parts)}


def _organic_results_from_full_raw(full_raw: dict) -> list[dict]:
    """Extract the organic results list from either a Serper or ScrapingDog full response."""
    # ScrapingDog uses organic_results; Serper uses organic
    return full_raw.get("organic_results") or full_raw.get("organic") or []


def _enrich_stored_results(stored: list[dict], full_raw_json: str | None) -> list[dict]:
    """Re-enrich stored {title,link,snippet,score} rows from full_raw if available.

    Used by rescore paths so old stored results get the same enrichment as new ones.
    Falls back to stored results unchanged when full_raw is absent.
    """
    if not full_raw_json:
        return stored
    try:
        full_raw = json.loads(full_raw_json)
    except (ValueError, TypeError):
        return stored
    organics = _organic_results_from_full_raw(full_raw)
    if not organics:
        return stored
    enriched_by_link: dict[str, dict] = {
        (o.get("link") or "").rstrip("/"): _enrich_organic_result(o)
        for o in organics
    }
    result: list[dict] = []
    for row in stored:
        key = (row.get("link") or "").rstrip("/")
        if key in enriched_by_link:
            merged = {**row, "snippet": enriched_by_link[key]["snippet"]}
            result.append(merged)
        else:
            result.append(row)
    return result


def _search_verdict_fields(db: Session, scored: list[dict]) -> dict:
    """Compute gated website fields from scored search results (search-only verdict).

    Returns a dict of {website_url, web_score, social_media_only, website_status,
    website_count} suitable for a CompanyUpdate or direct attribute assignment.

    Replaces the old "force scored[0] into website_url" behaviour: website_url is
    only populated when the verdict is a genuine own-domain match (POSITIVE).
    """
    from app.services import website_status as ws

    _, directory_domains = _google_scoring_overrides(db)
    thr = ws.load_thresholds(db)
    verdict = ws.classify_search_results(scored, directory_domains, thr)
    # verdict.web_score: best search score for positive verdicts; floored (10/5/0) for
    # negative ones (social_only/directory_only/none) so combined_score reflects reality.
    return {
        "website_url": verdict.website_url,
        "web_score": verdict.web_score,
        "social_media_only": verdict.status == ws.SOCIAL_ONLY,
        "website_status": verdict.status,
        "website_count": verdict.website_count or None,
    }


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
        scored.append({**row, "score": s, "position": idx})

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

    # Build and store exactly what we send so bad results can be diagnosed later
    _lang_map = {"de": "de", "fr": "fr", "it": "it", "en": "en", "rm": "de"}
    _hl = _lang_map.get((company.purpose_language or "").lower(), "de")
    _loc_parts = []
    if company.address_zip:
        _loc_parts.append(company.address_zip.strip())
    if company.municipality:
        _loc_parts.append(company.municipality.strip())
    _loc_parts.append("Switzerland")
    _location = " ".join(_loc_parts) if provider == "scrapingdog" else f"{company.municipality or ''}, Switzerland".strip(", ")
    search_params = {
        "provider": provider,
        "q": company.name,
        "gl": "ch",
        "hl": _hl,
        "location": _location,
        "purpose_language_raw": company.purpose_language,
        "municipality": company.municipality,
        "address_zip": company.address_zip,
    }

    try:
        api_key = _get_search_api_key(db, provider)
        if provider == "scrapingdog":
            from app.clients.scrapingdog_search_client import search_website as sd_search
            results, full_raw = sd_search(
                company.name,
                num=num,
                zip_code=company.address_zip,
                municipality=company.municipality,
                purpose_language=company.purpose_language,
                api_key=api_key,
            )
        else:
            results, full_raw = search_website(
                company.name,
                num=num,
                zip_code=company.address_zip,
                municipality=company.municipality,
                purpose_language=company.purpose_language,
                api_key=api_key,
            )

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
                google_search_results_raw=json.dumps([], ensure_ascii=False),
                google_search_full_raw=json.dumps(full_raw, ensure_ascii=False) if full_raw is not None else None,
                google_search_params=search_params,
            ),
        )
        return False, None

    if full_raw is not None:
        raw_results = [_enrich_organic_result(o) for o in _organic_results_from_full_raw(full_raw)]
    else:
        raw_results = [{"title": r.title, "link": r.link, "snippet": r.snippet or ""} for r in results]
    scored = _score_google_results_for_company(db, company, raw_results)
    fields = _search_verdict_fields(db, scored)

    crud.update_company(
        db,
        company,
        CompanyUpdate(
            website_url=fields["website_url"],
            web_score=fields["web_score"],
            social_media_only=fields["social_media_only"],
            website_status=fields["website_status"],
            website_count=fields["website_count"],
            website_checked_at=now,
            google_search_results_raw=json.dumps(scored, ensure_ascii=False),
            google_search_full_raw=json.dumps(full_raw, ensure_ascii=False) if full_raw is not None else None,
            google_search_params=search_params,
            combined_score=Company.compute_combined_score(
                company.ai_score, company.noga_confidence, company.purpose_keywords,
                web_score=fields["web_score"],
            ),
        ),
    )
    # Returns the gated own-domain URL (None for social_only/directory_only/none).
    return fields["website_url"] is not None, fields["website_url"]


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

    stored = _enrich_stored_results(stored, getattr(company, "google_search_full_raw", None))
    rescored = _score_google_results_for_company(db, company, stored)
    fields = _search_verdict_fields(db, rescored)
    crud.update_company(
        db,
        company,
        CompanyUpdate(
            website_url=fields["website_url"],
            web_score=fields["web_score"],
            social_media_only=fields["social_media_only"],
            website_status=fields["website_status"],
            website_count=fields["website_count"],
            google_search_results_raw=json.dumps(rescored, ensure_ascii=False),
            combined_score=Company.compute_combined_score(
                company.ai_score, company.noga_confidence, company.purpose_keywords,
                web_score=fields["web_score"],
            ),
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

    total = db.query(func.count(Company.id)).scalar() or 0
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

                raw_results = _enrich_stored_results(
                    raw_results, getattr(company, "google_search_full_raw", None)
                )
                rescored = _score_google_results_for_company(db, company, raw_results)
                if not rescored:
                    stats["skipped"] += 1
                    continue

                fields = _search_verdict_fields(db, rescored)
                company.website_url = fields["website_url"]
                company.web_score = fields["web_score"]
                company.social_media_only = fields["social_media_only"]
                company.website_status = fields["website_status"]
                company.website_count = fields["website_count"]
                company.google_search_results_raw = json.dumps(rescored, ensure_ascii=False)
                company.combined_score = Company.compute_combined_score(
                    company.ai_score, company.noga_confidence, company.purpose_keywords,
                    web_score=company.web_score,
                )

                organic_position = find_organic_position(rescored, company.website_url)
                ads_count, has_local_pack, has_knowledge_graph = extract_serp_features(
                    getattr(company, "google_search_full_raw", None)
                )
                company.seo_visibility_score = compute_seo_visibility_score(
                    organic_position,
                    ads_count=ads_count,
                    has_local_pack=has_local_pack,
                    has_knowledge_graph=has_knowledge_graph,
                )
                company.seo_visibility_computed_at = datetime.now(timezone.utc)

                stats["updated"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google rescore failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")

        db.commit()
        offset += len(batch)

        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    return stats


_DELETED_STATUSES = ("Gelöscht", "CANCELLED", "BEING_CANCELLED")


def run_batch_collect(
    db: Session,
    *,
    limit: int = 200,
    only_missing_website: bool = True,
    refresh_zefix: bool = False,
    run_google: bool = True,
    resume_from: int = 0,
    progress_cb: Any = None,
    # ── Geography ─────────────────────────────────────────────────────────────
    canton: str | None = None,              # single-canton shorthand (backward compat)
    cantons: list[str] | None = None,       # include any of these cantons
    exclude_cantons: list[str] | None = None,
    # ── Status / registration ─────────────────────────────────────────────────
    active_only: bool = True,               # exclude CANCELLED/BEING_CANCELLED/Gelöscht
    skip_uid_only: bool = False,            # exclude registration_type='uid_only'
    skip_mwst_only: bool = False,           # exclude registration_type='mwst'
    # ── Language ──────────────────────────────────────────────────────────────
    purpose_language: str | None = None,
    # ── Scores ────────────────────────────────────────────────────────────────
    min_flex_score: int | None = None,
    max_flex_score: int | None = None,
    min_ai_score: int | None = None,
    min_combined_score: float | None = None,
    # ── Keywords / clusters ───────────────────────────────────────────────────
    purpose_keywords: str | None = None,
    exclude_purpose_keywords: str | None = None,
    tfidf_cluster: str | None = None,
    review_status: str | None = None,
    # ── Industry (NOGA) ───────────────────────────────────────────────────────
    noga_code: str | None = None,
    exclude_noga_code: str | None = None,
    # ── Company type ──────────────────────────────────────────────────────────
    legal_form: str | None = None,
    business_model: str | None = None,
    # ── Date ranges ───────────────────────────────────────────────────────────
    registered_after: str | None = None,   # first_sogc_date >=
    registered_before: str | None = None,  # first_sogc_date <=
    sogc_after: str | None = None,         # sogc_date >= (last Zefix publication)
    sogc_before: str | None = None,        # sogc_date <=
    # ── Ordering ──────────────────────────────────────────────────────────────
    order_by: str = "flex_score_desc",
    # ── Callbacks ─────────────────────────────────────────────────────────────
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

    # Branch offices (Zweigniederlassung) don't have independent websites;
    # skip them to avoid wasting Serper quota on sites that redirect to the parent.
    _BRANCH_LEGAL_FORM_UIDS = ("0108", "0111")
    query = db.query(Company)
    query = query.filter(
        or_(Company.legal_form_uid.is_(None), Company.legal_form_uid.notin_(_BRANCH_LEGAL_FORM_UIDS))
    ).filter(
        ~Company.name.ilike("%zweigniederlassung%"),
        ~Company.name.ilike("%succursale%"),
        ~Company.name.ilike("%filiale di%"),
    )

    if only_missing_website:
        # "Missing website" = never searched. Use website_checked_at (not website_url)
        # because the verdict now gates website_url to NULL for companies that were
        # checked but have no genuine site (social_only/directory_only/none) — keying
        # off website_url would re-enrich those forever.
        query = query.filter(Company.website_checked_at.is_(None))

    # ── Status / registration ────────────────────────────────────────────────
    if active_only:
        query = query.filter(Company.status.notin_(list(_DELETED_STATUSES)))
    _excl_reg: list[str] = []
    if skip_uid_only:
        _excl_reg.append("uid_only")
    if skip_mwst_only:
        _excl_reg.append("mwst")
    if _excl_reg:
        query = query.filter(
            or_(Company.registration_type.is_(None), Company.registration_type.notin_(_excl_reg))
        )

    # ── Geography ─────────────────────────────────────────────────────────────
    _canton_inc: list[str] = []
    if canton:
        _canton_inc.append(canton.strip().upper())
    if cantons:
        _canton_inc.extend(c.strip().upper() for c in cantons if c.strip())
    if _canton_inc:
        query = query.filter(Company.canton.in_(_canton_inc))
    if exclude_cantons:
        _excl_c = [c.strip().upper() for c in exclude_cantons if c.strip()]
        if _excl_c:
            query = query.filter(or_(Company.canton.is_(None), Company.canton.notin_(_excl_c)))

    # ── Language ──────────────────────────────────────────────────────────────
    if purpose_language:
        query = query.filter(Company.purpose_language == purpose_language)

    # ── Scores ────────────────────────────────────────────────────────────────
    if min_flex_score is not None:
        query = query.filter(Company.flex_score >= min_flex_score)
    if max_flex_score is not None:
        query = query.filter(or_(Company.flex_score.is_(None), Company.flex_score <= max_flex_score))
    if min_ai_score is not None:
        query = query.filter(Company.ai_score >= min_ai_score)
    if min_combined_score is not None:
        query = query.filter(Company.combined_score >= min_combined_score)

    # ── Keywords / clusters ───────────────────────────────────────────────────
    if purpose_keywords:
        kw_terms = [t.strip() for t in purpose_keywords.split(",") if t.strip()]
        if kw_terms:
            query = query.filter(
                or_(*[Company.purpose_keywords.ilike(f"%{kw}%") for kw in kw_terms])
            )
    if exclude_purpose_keywords:
        excl_kw = [t.strip() for t in exclude_purpose_keywords.split(",") if t.strip()]
        for kw in excl_kw:
            query = query.filter(~Company.purpose_keywords.ilike(f"%{kw}%"))
    if tfidf_cluster:
        query = query.filter(Company.tfidf_cluster.ilike(f"%{tfidf_cluster}%"))
    if review_status:
        if review_status == "pending":
            query = query.filter(Company.review_status.is_(None))
        else:
            query = query.filter(Company.review_status == review_status)

    # ── Industry (NOGA) ───────────────────────────────────────────────────────
    if noga_code:
        query = query.filter(Company.noga_code.like(f"{noga_code.strip()}%"))
    if exclude_noga_code:
        _excl_noga = exclude_noga_code.strip()
        query = query.filter(
            or_(Company.noga_code.is_(None), ~Company.noga_code.like(f"{_excl_noga}%"))
        )

    # ── Company type ──────────────────────────────────────────────────────────
    if legal_form:
        query = query.filter(Company.legal_form == legal_form)
    if business_model:
        if business_model == "_none":
            query = query.filter(Company.business_model.is_(None))
        else:
            _bm_terms = [t.strip() for t in business_model.split(",") if t.strip()]
            query = query.filter(Company.business_model.in_(_bm_terms))

    # ── Date ranges ───────────────────────────────────────────────────────────
    if registered_after:
        query = query.filter(Company.first_sogc_date >= registered_after)
    if registered_before:
        query = query.filter(Company.first_sogc_date <= registered_before)
    if sogc_after:
        query = query.filter(Company.sogc_date >= sogc_after)
    if sogc_before:
        query = query.filter(Company.sogc_date <= sogc_before)

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
    elif order_by == "ai_score_desc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.ai_score.desc().nulls_last(), Company.id.desc())
            .limit(keep_n).all()
        ]
    elif order_by == "web_score_desc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.web_score.desc().nulls_last(), Company.id.desc())
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
    elif order_by == "sogc_date_desc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.sogc_date.desc().nulls_last(), Company.id.desc())
            .limit(keep_n).all()
        ]
    elif order_by == "first_sogc_date_asc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.first_sogc_date.asc().nulls_last(), Company.id.asc())
            .limit(keep_n).all()
        ]
    elif order_by == "first_sogc_date_desc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.first_sogc_date.desc().nulls_last(), Company.id.desc())
            .limit(keep_n).all()
        ]
    elif order_by == "name_asc":
        planned_ids = [
            row[0] for row in
            query.with_entities(Company.id)
            .order_by(Company.name.asc())
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
                    # Auto-populate url_candidates so the crawler can pick up new
                    # URLs immediately without a separate web_url_populate job.
                    # upsert is safe on existing rows (only updates score/title/snippet).
                    # select_best_candidate only fires when no candidate is selected yet —
                    # preserves any manually-chosen or already-crawled selection.
                    try:
                        raw = current.google_search_results_raw
                        candidates = crawler_crud.parse_google_results_raw(raw) if raw else []
                        if candidates:
                            crawler_crud.upsert_url_candidates(db, current.id, candidates)
                            if not crawler_crud.get_selected_candidate(db, current.id):
                                best = crawler_crud.select_best_candidate(db, current.id)
                                crawler_crud.get_or_create_crawl_state(
                                    db, current.id,
                                    selected_url_id=best.id if best else None,
                                )
                    except Exception as exc_inner:  # noqa: BLE001
                        logger.warning(
                            "URL candidate sync failed for %s: %s", current.uid, exc_inner
                        )
                else:
                    stats["google_no_result"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google search failed for %s: %s", current.uid, exc)
                stats["errors"].append(f"Google search {current.uid} [{type(exc).__name__}]: {exc}")
                try:
                    from app.crud.company_error import log_error as _log_err
                    _log_err(db, company_id=current.id, source="web_enrichment",
                             error_type="enrich_failed", message=str(exc))
                    db.flush()
                except Exception:  # noqa: BLE001
                    pass

        if progress_cb:
            progress_cb(i, stats["selected"], stats)

    return stats
