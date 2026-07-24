import logging
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import case, func, nullslast, or_, text as _text
from sqlalchemy.orm import Session

from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyUpdate

logger = logging.getLogger(__name__)

try:
    from app.models.company_tfidf_cluster import CompanyTfidfCluster
    from app.models.company_purpose_keyword import CompanyPurposeKeyword
    HAS_JUNCTION_TABLES = True
except ImportError:
    HAS_JUNCTION_TABLES = False

from app.services.ml.noga_lookup import load_noga_hierarchy as _load_noga_hierarchy  # noqa: E402

# Valid sort keys → (column_attr, ascending)
_SORT_MAP = {
    "name":             (Company.name,               True),
    "-name":            (Company.name,               False),
    "web_score":        (Company.web_score,          True),
    "-web_score":       (Company.web_score,          False),
    "flex_score":       (Company.flex_score,         True),
    "-flex_score":      (Company.flex_score,         False),
    "ai_score":         (Company.ai_score,           True),
    "-ai_score":        (Company.ai_score,           False),
    "tfidf_cluster":    (Company.tfidf_cluster,      True),
    "-tfidf_cluster":   (Company.tfidf_cluster,      False),
    "canton":           (Company.canton,             True),
    "-canton":          (Company.canton,             False),
    "status":           (Company.status,             True),
    "-status":          (Company.status,             False),
    "review_status":    (Company.review_status,      True),
    "-review_status":   (Company.review_status,      False),
    "contact_status":   (Company.contact_status,     True),
    "-contact_status":  (Company.contact_status,     False),
    "website":          (Company.website_url,        True),
    "-website":         (Company.website_url,        False),
    "updated":              (Company.updated_at,          True),
    "-updated":             (Company.updated_at,          False),
    "updated_at":           (Company.updated_at,          True),
    "-updated_at":          (Company.updated_at,          False),
    "created":              (Company.created_at,          True),
    "-created":             (Company.created_at,          False),
    # "website_checked_at" is special-cased in list_companies() — it now lives on
    # company_search_results, a joined table, not a plain Company column.
    "flex_scored_at":       (Company.flex_scored_at,      True),
    "-flex_scored_at":      (Company.flex_scored_at,      False),
    "ai_scored_at":         (Company.ai_scored_at,        True),
    "-ai_scored_at":        (Company.ai_scored_at,        False),
    "ai_category":          (Company.ai_category,         True),
    "-ai_category":         (Company.ai_category,         False),
    "sogc_date":            (Company.sogc_date,           True),
    "-sogc_date":           (Company.sogc_date,           False),
    "first_sogc_date":      (Company.first_sogc_date,     True),
    "-first_sogc_date":     (Company.first_sogc_date,     False),
    "combined_score":       (Company.combined_score,      True),
    "-combined_score":      (Company.combined_score,      False),
}
_DEFAULT_SORT = "-updated"


def get_company(db: Session, company_id: int) -> Company | None:
    return db.get(Company, company_id)


def get_company_by_uid(db: Session, uid: str) -> Company | None:
    return db.query(Company).filter(Company.uid == uid).first()


_DELETED_STATUSES = ("Gelöscht", "CANCELLED", "BEING_CANCELLED")


def _apply_filters(query, *, name_filter, uid_filter=None, canton, review_status, contact_status,
                   google_searched, min_web_score, min_flex_score, min_ai_score=None,
                   max_web_score=None, max_flex_score=None, max_ai_score=None,
                   min_combined_score=None, max_combined_score=None,
                   ai_category=None, tags, tfidf_cluster=None, purpose_keywords=None,
                   noga_code=None, noga_label=None, noga_level=None,
                   exclude_tags=None, exclude_review_status=None, exclude_canton=None,
                   exclude_contact_status=None, exclude_tfidf_cluster=None,
                   exclude_purpose_keywords=None, exclude_ai_category=None,
                   exclude_noga_code=None, exclude_noga_label=None, exclude_noga_level=None,
                   zefix_status=None, has_website=None,
                   legal_form=None, registered_after=None, registered_before=None,
                   sogc_after=None, sogc_before=None, shab_type=None,
                   business_model=None, purpose_language=None,
                   active_only: bool = True):
    if active_only and not zefix_status and not shab_type:
        # NULL status is not "deleted" — keep it. SQL `NULL NOT IN (...)` is NULL
        # (falsy), which would otherwise silently drop every company Zefix returned
        # without a status from the list/export/alert results.
        query = query.filter(
            (Company.status.is_(None)) | (Company.status.notin_(list(_DELETED_STATUSES)))
        )
    if zefix_status:
        query = query.filter(Company.status == zefix_status)
    if has_website is True:
        query = query.filter(Company.website_url.isnot(None))
    elif has_website is False:
        query = query.filter(Company.website_url.is_(None))
    if legal_form:
        query = query.filter(Company.legal_form == legal_form)
    if business_model:
        if business_model == "_none":
            query = query.filter(Company.business_model.is_(None))
        else:
            terms = [t.strip() for t in business_model.split(",") if t.strip()]
            query = query.filter(Company.business_model.in_(terms))
    if purpose_language:
        query = query.filter(Company.purpose_language == purpose_language)
    # registered_after/before filter by first_sogc_date (earliest SOGC appearance)
    if registered_after:
        query = query.filter(Company.first_sogc_date >= registered_after)
    if registered_before:
        query = query.filter(Company.first_sogc_date <= registered_before)
    # sogc_after/before filter by sogc_date (most recent SOGC entry)
    if sogc_after:
        query = query.filter(Company.sogc_date >= sogc_after)
    if sogc_before:
        query = query.filter(Company.sogc_date <= sogc_before)
    # shab_type: "new" (HR01), "mutation" (HR02), "deleted" (HR03)
    if shab_type == "new":
        query = query.filter(
            Company.first_sogc_date.isnot(None),
            Company.first_sogc_date == Company.sogc_date,
            Company.status.notin_(list(_DELETED_STATUSES)),
        )
    elif shab_type == "mutation":
        query = query.filter(
            Company.first_sogc_date.isnot(None),
            Company.first_sogc_date != Company.sogc_date,
            Company.status.notin_(list(_DELETED_STATUSES)),
        )
    elif shab_type == "deleted":
        query = query.filter(Company.status.in_(list(_DELETED_STATUSES)))
    if name_filter or uid_filter:
        import re as _re
    if name_filter:
        _uid_m = _re.match(r'^CHE(\d{9})$', name_filter.strip().upper())
        name_uid_norm = (
            f"CHE-{_uid_m.group(1)[:3]}.{_uid_m.group(1)[3:6]}.{_uid_m.group(1)[6:]}"
            if _uid_m else None
        )
        name_conditions = [
            Company.name.ilike(f"%{name_filter}%"),
            Company.old_names.ilike(f"%{name_filter}%"),
        ]
        if name_uid_norm:
            name_conditions.append(Company.uid.ilike(f"%{name_uid_norm}%"))
        query = query.filter(or_(*name_conditions))
    if uid_filter:
        # Normalize compact form (CHE442723833) → CHE-442.723.833 for ILIKE matching
        _m = _re.match(r'^CHE(\d{9})$', uid_filter.strip().upper())
        uid_filter_norm = f"CHE-{_m.group(1)[:3]}.{_m.group(1)[3:6]}.{_m.group(1)[6:]}" if _m else uid_filter
        query = query.filter(Company.uid.ilike(f"%{uid_filter_norm}%"))
    if canton:
        terms = [t.strip() for t in canton.split(",") if t.strip()]
        query = query.filter(Company.canton.in_(terms)) if len(terms) > 1 else query.filter(Company.canton == terms[0])
    if review_status == "_none":
        query = query.filter(Company.review_status.is_(None))
    elif review_status:
        query = query.filter(Company.review_status == review_status)
    if contact_status == "_none":
        query = query.filter(Company.contact_status.is_(None))
    elif contact_status:
        query = query.filter(Company.contact_status == contact_status)
    if google_searched in ("yes", "no", "no_result"):
        from sqlalchemy import exists
        from app.models.company_search_result import CompanySearchResult
        _searched_exists = exists().where(CompanySearchResult.company_id == Company.id)
        if google_searched == "yes":
            query = query.filter(_searched_exists)
        elif google_searched == "no":
            query = query.filter(~_searched_exists)
        elif google_searched == "no_result":
            query = query.filter(_searched_exists, Company.website_url.is_(None))
    if min_web_score is not None:
        query = query.filter(Company.web_score >= min_web_score)
    if max_web_score is not None:
        query = query.filter(Company.web_score <= max_web_score)
    if min_flex_score is not None:
        query = query.filter(Company.flex_score >= min_flex_score)
    if max_flex_score is not None:
        query = query.filter(Company.flex_score <= max_flex_score)
    if min_ai_score is not None:
        query = query.filter(Company.ai_score >= min_ai_score)
    if max_ai_score is not None:
        query = query.filter(Company.ai_score <= max_ai_score)
    if min_combined_score is not None:
        query = query.filter(Company.combined_score >= min_combined_score)
    if max_combined_score is not None:
        query = query.filter(Company.combined_score <= max_combined_score)
    if ai_category == "_none":
        query = query.filter(Company.ai_category.is_(None))
    elif ai_category:
        terms = [t.strip() for t in ai_category.split(",") if t.strip()]
        query = query.filter(or_(*[Company.ai_category.ilike(f"%{t}%") for t in terms]))
    if tags:
        query = query.filter(Company.tags.ilike(f"%{tags}%"))
    if tfidf_cluster == "_none":
        query = query.filter(Company.tfidf_cluster.is_(None))
    elif tfidf_cluster == "_any":
        query = query.filter(Company.tfidf_cluster.isnot(None))
    elif tfidf_cluster and HAS_JUNCTION_TABLES:
        terms = [t.strip() for t in tfidf_cluster.split(",") if t.strip()]
        query = query.join(CompanyTfidfCluster).filter(CompanyTfidfCluster.cluster.in_(terms))
    elif tfidf_cluster:
        terms = [t.strip() for t in tfidf_cluster.split(",") if t.strip()]
        def _cluster_match(t: str):
            return or_(
                Company.tfidf_cluster == t,
                Company.tfidf_cluster.ilike(f"{t}|%"),
                Company.tfidf_cluster.ilike(f"%|{t}"),
                Company.tfidf_cluster.ilike(f"%|{t}|%"),
            )
        query = query.filter(or_(*[_cluster_match(t) for t in terms]))
    if purpose_keywords:
        terms = [t.strip() for t in purpose_keywords.split(",") if t.strip()]
        if HAS_JUNCTION_TABLES:
            query = query.join(CompanyPurposeKeyword).filter(CompanyPurposeKeyword.keyword.in_(terms))
        else:
            query = query.filter(or_(
                Company.purpose_keywords_arr.overlap(terms),   # uses GIN index
                *[
                    or_(
                        Company.purpose_keywords == t,
                        Company.purpose_keywords.ilike(f"{t},%"),
                        Company.purpose_keywords.ilike(f"%, {t}"),
                        Company.purpose_keywords.ilike(f"%, {t},%"),
                    )
                    for t in terms
                ]
            ))
    if noga_code == "_none":
        query = query.filter(Company.noga_code.is_(None))
    elif noga_code == "_any":
        query = query.filter(Company.noga_code.isnot(None))
    elif noga_code:
        terms = [t.strip() for t in noga_code.split(",") if t.strip()]
        # Match exact code OR any ancestor/descendant via noga_path (pipe-delimited root→leaf)
        conditions = []
        for t in terms:
            conditions.append(or_(
                Company.noga_code == t,
                Company.noga_path == t,
                Company.noga_path.like(f"{t}|%"),
                Company.noga_path.like(f"%|{t}|%"),
                Company.noga_path.like(f"%|{t}"),
            ))
        query = query.filter(or_(*conditions))
    if noga_label:
        terms = [t.strip() for t in noga_label.split(",") if t.strip()]
        query = query.filter(or_(*[Company.noga_label.ilike(f"%{t}%") for t in terms]))
    if noga_level:
        terms = [t.strip() for t in noga_level.split(",") if t.strip()]
        query = query.filter(Company.noga_level.in_(terms))
    if exclude_tags:
        for term in [t.strip() for t in exclude_tags.split(",") if t.strip()]:
            query = query.filter(
                (Company.tags.is_(None)) | (Company.tags.notilike(f"%{term}%"))
            )
    if exclude_review_status == "_none":
        query = query.filter(Company.review_status.isnot(None))
    elif exclude_review_status:
        query = query.filter(
            (Company.review_status.is_(None)) | (Company.review_status != exclude_review_status)
        )
    if exclude_canton:
        query = query.filter(
            (Company.canton.is_(None)) | (Company.canton != exclude_canton)
        )
    if exclude_contact_status == "_none":
        query = query.filter(Company.contact_status.isnot(None))
    elif exclude_contact_status:
        query = query.filter(
            (Company.contact_status.is_(None)) | (Company.contact_status != exclude_contact_status)
        )
    if exclude_tfidf_cluster:
        for term in [t.strip() for t in exclude_tfidf_cluster.split(",") if t.strip()]:
            query = query.filter(
                Company.tfidf_cluster.is_(None)
                | (
                    (Company.tfidf_cluster != term)
                    & Company.tfidf_cluster.notilike(f"{term}|%")
                    & Company.tfidf_cluster.notilike(f"%|{term}")
                    & Company.tfidf_cluster.notilike(f"%|{term}|%")
                )
            )
    if exclude_purpose_keywords:
        for term in [t.strip() for t in exclude_purpose_keywords.split(",") if t.strip()]:
            query = query.filter(
                Company.purpose_keywords_arr.is_(None)
                | ~Company.purpose_keywords_arr.any(term)
            )
    if exclude_ai_category:
        for term in [t.strip() for t in exclude_ai_category.split(",") if t.strip()]:
            query = query.filter(
                (Company.ai_category.is_(None)) | (Company.ai_category.notilike(f"%{term}%"))
            )
    if exclude_noga_code:
        for term in [t.strip() for t in exclude_noga_code.split(",") if t.strip()]:
            query = query.filter(
                (Company.noga_code.is_(None)) | (Company.noga_code.notilike(f"%{term}%"))
            )
    if exclude_noga_label:
        for term in [t.strip() for t in exclude_noga_label.split(",") if t.strip()]:
            query = query.filter(
                (Company.noga_label.is_(None)) | (Company.noga_label.notilike(f"%{term}%"))
            )
    if exclude_noga_level:
        for term in [t.strip() for t in exclude_noga_level.split(",") if t.strip()]:
            query = query.filter(
                (Company.noga_level.is_(None)) | (Company.noga_level != term)
            )
    return query


def list_companies(
    db: Session,
    page: int = 1,
    page_size: int = 50,
    sort: str = _DEFAULT_SORT,
    name_filter: str | None = None,
    uid_filter: str | None = None,
    canton: str | None = None,
    review_status: str | None = None,
    contact_status: str | None = None,
    google_searched: str | None = None,
    min_web_score: int | None = None,
    max_web_score: int | None = None,
    min_flex_score: int | None = None,
    max_flex_score: int | None = None,
    min_ai_score: int | None = None,
    max_ai_score: int | None = None,
    min_combined_score: int | None = None,
    max_combined_score: int | None = None,
    ai_category: str | None = None,
    tags: str | None = None,
    tfidf_cluster: str | None = None,
    purpose_keywords: str | None = None,
    noga_code: str | None = None,
    noga_label: str | None = None,
    noga_level: str | None = None,
    exclude_tags: str | None = None,
    exclude_review_status: str | None = None,
    exclude_canton: str | None = None,
    exclude_contact_status: str | None = None,
    exclude_tfidf_cluster: str | None = None,
    exclude_purpose_keywords: str | None = None,
    exclude_ai_category: str | None = None,
    exclude_noga_code: str | None = None,
    exclude_noga_label: str | None = None,
    exclude_noga_level: str | None = None,
    zefix_status: str | None = None,
    has_website: bool | None = None,
    legal_form: str | None = None,
    registered_after: str | None = None,
    registered_before: str | None = None,
    sogc_after: str | None = None,
    sogc_before: str | None = None,
    shab_type: str | None = None,
    business_model: str | None = None,
    purpose_language: str | None = None,
    active_only: bool = True,
    # kept for backward-compat with collection.py batch query
    limit: int | None = None,
    skip: int = 0,
) -> list[Company]:
    query = db.query(Company)
    query = _apply_filters(
        query,
        name_filter=name_filter,
        uid_filter=uid_filter,
        canton=canton,
        review_status=review_status,
        contact_status=contact_status,
        google_searched=google_searched,
        min_web_score=min_web_score,
        max_web_score=max_web_score,
        min_flex_score=min_flex_score,
        max_flex_score=max_flex_score,
        min_ai_score=min_ai_score,
        max_ai_score=max_ai_score,
        min_combined_score=min_combined_score,
        max_combined_score=max_combined_score,
        ai_category=ai_category,
        tags=tags,
        tfidf_cluster=tfidf_cluster,
        purpose_keywords=purpose_keywords,
        noga_code=noga_code,
        noga_label=noga_label,
        noga_level=noga_level,
        exclude_tags=exclude_tags,
        exclude_review_status=exclude_review_status,
        exclude_canton=exclude_canton,
        exclude_contact_status=exclude_contact_status,
        exclude_tfidf_cluster=exclude_tfidf_cluster,
        exclude_purpose_keywords=exclude_purpose_keywords,
        exclude_ai_category=exclude_ai_category,
        exclude_noga_code=exclude_noga_code,
        exclude_noga_label=exclude_noga_label,
        exclude_noga_level=exclude_noga_level,
        zefix_status=zefix_status,
        has_website=has_website,
        legal_form=legal_form,
        registered_after=registered_after,
        registered_before=registered_before,
        sogc_after=sogc_after,
        sogc_before=sogc_before,
        shab_type=shab_type,
        business_model=business_model,
        purpose_language=purpose_language,
        active_only=active_only,
    )

    if sort in ("combined_score", "-combined_score"):
        # SQL expression: avg of non-null scores (flex, ai, web)
        _sum = (
            func.coalesce(Company.flex_score, 0)
            + func.coalesce(Company.ai_score, 0)
            + func.coalesce(Company.web_score, 0)
        )
        _count = (
            case((Company.flex_score.isnot(None), 1), else_=0)
            + case((Company.ai_score.isnot(None), 1), else_=0)
            + case((Company.web_score.isnot(None), 1), else_=0)
        )
        _expr = _sum / func.nullif(_count, 0)
        asc = sort == "combined_score"
        query = query.order_by(nullslast(_expr.asc() if asc else _expr.desc()))
    elif sort in ("website_checked_at", "-website_checked_at"):
        # Lives on company_search_results now — join only for this sort (not
        # unconditionally, to keep the common no-sort case join-free at 700k rows).
        from app.models.company_search_result import CompanySearchResult
        query = query.outerjoin(CompanySearchResult, CompanySearchResult.company_id == Company.id)
        asc = sort == "website_checked_at"
        col = CompanySearchResult.searched_at
        query = query.order_by(col.asc().nullslast() if asc else col.desc().nullslast())
    else:
        col, ascending = _SORT_MAP.get(sort, _SORT_MAP[_DEFAULT_SORT])
        query = query.order_by(col.asc() if ascending else col.desc())

    if limit is not None:
        # Legacy path used by batch collection
        return query.offset(skip).limit(limit).all()

    offset = (page - 1) * page_size
    return query.offset(offset).limit(page_size).all()


def count_companies(
    db: Session,
    name_filter: str | None = None,
    uid_filter: str | None = None,
    canton: str | None = None,
    review_status: str | None = None,
    contact_status: str | None = None,
    google_searched: str | None = None,
    min_web_score: int | None = None,
    max_web_score: int | None = None,
    min_flex_score: int | None = None,
    max_flex_score: int | None = None,
    min_ai_score: int | None = None,
    max_ai_score: int | None = None,
    min_combined_score: int | None = None,
    max_combined_score: int | None = None,
    ai_category: str | None = None,
    tags: str | None = None,
    tfidf_cluster: str | None = None,
    purpose_keywords: str | None = None,
    noga_code: str | None = None,
    noga_label: str | None = None,
    noga_level: str | None = None,
    exclude_tags: str | None = None,
    exclude_review_status: str | None = None,
    exclude_canton: str | None = None,
    exclude_contact_status: str | None = None,
    exclude_tfidf_cluster: str | None = None,
    exclude_purpose_keywords: str | None = None,
    exclude_ai_category: str | None = None,
    exclude_noga_code: str | None = None,
    exclude_noga_label: str | None = None,
    exclude_noga_level: str | None = None,
    zefix_status: str | None = None,
    has_website: bool | None = None,
    legal_form: str | None = None,
    registered_after: str | None = None,
    registered_before: str | None = None,
    sogc_after: str | None = None,
    sogc_before: str | None = None,
    shab_type: str | None = None,
    business_model: str | None = None,
    purpose_language: str | None = None,
    active_only: bool = True,
) -> int:
    query = db.query(Company)
    query = _apply_filters(
        query,
        name_filter=name_filter,
        uid_filter=uid_filter,
        canton=canton,
        review_status=review_status,
        contact_status=contact_status,
        google_searched=google_searched,
        min_web_score=min_web_score,
        max_web_score=max_web_score,
        min_flex_score=min_flex_score,
        max_flex_score=max_flex_score,
        min_ai_score=min_ai_score,
        max_ai_score=max_ai_score,
        min_combined_score=min_combined_score,
        max_combined_score=max_combined_score,
        ai_category=ai_category,
        tags=tags,
        tfidf_cluster=tfidf_cluster,
        purpose_keywords=purpose_keywords,
        noga_code=noga_code,
        noga_label=noga_label,
        noga_level=noga_level,
        exclude_tags=exclude_tags,
        exclude_review_status=exclude_review_status,
        exclude_canton=exclude_canton,
        exclude_contact_status=exclude_contact_status,
        exclude_tfidf_cluster=exclude_tfidf_cluster,
        exclude_purpose_keywords=exclude_purpose_keywords,
        exclude_ai_category=exclude_ai_category,
        exclude_noga_code=exclude_noga_code,
        exclude_noga_label=exclude_noga_label,
        exclude_noga_level=exclude_noga_level,
        zefix_status=zefix_status,
        has_website=has_website,
        legal_form=legal_form,
        registered_after=registered_after,
        registered_before=registered_before,
        sogc_after=sogc_after,
        sogc_before=sogc_before,
        shab_type=shab_type,
        business_model=business_model,
        purpose_language=purpose_language,
        active_only=active_only,
    )
    return query.count()


def get_company_stats(db: Session) -> dict:
    from app.models.company_search_result import CompanySearchResult

    total = db.query(Company).count()
    searched = db.query(CompanySearchResult).count()
    with_website = db.query(Company).filter(Company.website_url.isnot(None)).count()

    # Google searches used today (by searched_at date)
    from datetime import datetime
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    searches_today = (
        db.query(CompanySearchResult)
        .filter(CompanySearchResult.searched_at >= today_start, CompanySearchResult.searched_at <= today_end)
        .count()
    )

    review_counts: dict[str, int] = {}
    for label in ("interesting", "rejected", "potential_proposal", "confirmed_proposal", "potential_generic", "confirmed_generic"):
        review_counts[label] = db.query(Company).filter(Company.review_status == label).count()
    review_counts["pending"] = db.query(Company).filter(Company.review_status.is_(None)).count()

    contact_counts: dict[str, int] = {}
    for label in ("sent", "responded", "converted", "rejected"):
        contact_counts[label] = db.query(Company).filter(Company.contact_status == label).count()

    # Score distribution: bucket stored combined_score into 10-point bands
    dist_rows = db.execute(_text(
        "SELECT LEAST(FLOOR(combined_score / 10)::int, 9) AS bucket, COUNT(*) AS cnt"
        " FROM companies WHERE combined_score IS NOT NULL GROUP BY bucket"
    )).fetchall()
    score_dist = {f"{i*10}-{i*10+9}": 0 for i in range(10)}
    score_dist["100"] = 0
    for row in dist_rows:
        key = f"{row.bucket*10}-{row.bucket*10+9}"
        score_dist[key] = row.cnt

    return {
        "total": total,
        "searched": searched,
        "with_website": with_website,
        "searches_today": searches_today,
        "review": review_counts,
        "contact": contact_counts,
        "score_distribution": score_dist,
    }


def create_company(db: Session, company_in: CompanyCreate) -> Company:
    db_company = Company(**company_in.model_dump())
    db.add(db_company)
    db.commit()
    db.refresh(db_company)
    return db_company


def _sync_junction_tables(db: Session, company_id: int, update_data: dict) -> None:
    """Keep company_tfidf_clusters and company_purpose_keywords in sync when their source fields change."""
    if not HAS_JUNCTION_TABLES:
        return
    if "tfidf_cluster" in update_data:
        db.execute(
            _text("DELETE FROM company_tfidf_clusters WHERE company_id = :cid"),
            {"cid": company_id},
        )
        raw = update_data["tfidf_cluster"] or ""
        terms = [t.strip() for t in raw.split("|") if t.strip()]
        if terms:
            db.execute(
                _text(
                    "INSERT INTO company_tfidf_clusters (company_id, cluster) "
                    "SELECT :cid, UNNEST(:terms::text[]) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"cid": company_id, "terms": terms},
            )
    if "purpose_keywords" in update_data:
        db.execute(
            _text("DELETE FROM company_purpose_keywords WHERE company_id = :cid"),
            {"cid": company_id},
        )
        raw = update_data["purpose_keywords"] or ""
        terms = [t.strip() for t in raw.split(",") if t.strip()]
        if terms:
            db.execute(
                _text(
                    "INSERT INTO company_purpose_keywords (company_id, keyword) "
                    "SELECT :cid, UNNEST(:terms::text[]) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"cid": company_id, "terms": terms},
            )


def update_company(db: Session, db_company: Company, company_in: CompanyUpdate) -> Company:
    update_data = company_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_company, field, value)
    _sync_junction_tables(db, db_company.id, update_data)
    db.commit()
    db.refresh(db_company)
    return db_company


def bulk_update_status(
    db: Session,
    company_ids: list[int],
    field: str,
    value: str | None,
) -> int:
    """Update a single status field on multiple companies at once. Returns updated count."""
    if field not in ("review_status", "contact_status"):
        raise ValueError(f"bulk_update_status: unsupported field '{field}'")
    count = (
        db.query(Company)
        .filter(Company.id.in_(company_ids))
        .update({field: value}, synchronize_session=False)
    )
    db.commit()
    return count


def delete_company(db: Session, db_company: Company) -> None:
    db.delete(db_company)
    db.commit()


def bulk_update_tags(
    db: Session,
    company_ids: list[int],
    tag: str,
    action: str,  # "add" | "remove"
) -> int:
    """Add or remove a tag from multiple companies. Returns updated count."""
    tag = tag.strip()
    if not tag or not company_ids:
        return 0

    batch_size = 500
    updated = 0
    for i in range(0, len(company_ids), batch_size):
        batch = company_ids[i : i + batch_size]
        rows = db.query(Company).filter(Company.id.in_(batch)).all()
        for company in rows:
            current_tags = [t.strip() for t in (company.tags or "").split(",") if t.strip()]
            if action == "add" and tag not in current_tags:
                current_tags.append(tag)
                company.tags = ", ".join(current_tags)
                updated += 1
            elif action == "remove" and tag in current_tags:
                current_tags.remove(tag)
                company.tags = ", ".join(current_tags) or None
                updated += 1
        if rows:
            db.commit()

    return updated


def search_keywords(db: Session, q: str, limit: int = 20) -> list[tuple[str, int]]:
    """Return individual keywords from purpose_keywords matching q, sorted by frequency."""
    q_lower = q.lower()
    raw = (
        db.query(Company.purpose_keywords)
        .filter(Company.purpose_keywords.isnot(None))
        .filter(Company.purpose_keywords.ilike(f"%{q}%"))
        .limit(1000)
        .all()
    )
    counter: Counter = Counter()
    for (val,) in raw:
        for kw in val.split(","):
            kw = kw.strip()
            if kw and q_lower in kw.lower():
                counter[kw] += 1
    return counter.most_common(limit)


def search_clusters(db: Session, q: str, limit: int = 20) -> list[tuple[str, int]]:
    """Return cluster labels matching q, sorted by frequency."""
    q_lower = q.lower()
    raw = (
        db.query(Company.tfidf_cluster)
        .filter(Company.tfidf_cluster.isnot(None))
        .filter(Company.tfidf_cluster.ilike(f"%{q}%"))
        .limit(1000)
        .all()
    )
    counter: Counter = Counter()
    for (val,) in raw:
        for label in val.split("|"):
            label = label.strip()
            if label and q_lower in label.lower():
                counter[label] += 1
    return counter.most_common(limit)


def get_noga_hierarchy(db: Session, org_id: int | None = None) -> list[dict]:
    """Return NOGA codes as a tree with aggregated counts using the real NOGA hierarchy.

    Each node: {code, label, level, own_count, count (aggregated), children}.
    Top-level sections are returned as the root list.
    """
    noga_cache = _load_noga_hierarchy()
    if not noga_cache:
        return []

    noga_data = noga_cache["noga_data"]
    parent_map = noga_cache["parent_map"]

    # Always count from the global companies table — NOGA is a taxonomy browser,
    # not an org-specific pipeline view. OrgCompanyState is intentionally excluded.
    query = (
        db.query(Company.noga_code, func.count(Company.id).label("cnt"))
        .filter(Company.noga_code.isnot(None))
        .group_by(Company.noga_code)
    )
    rows = query.all()

    code_counts = {r.noga_code: r.cnt for r in rows}

    nodes: dict[str, dict] = {}
    for code, node in noga_data.items():
        code_str = str(code)
        if isinstance(node, dict):
            count = code_counts.get(code_str, 0)
            name = node.get("name", {})
            label = ""
            labels_dict: dict[str, str] = {}
            if isinstance(name, dict):
                for _lang in ("de", "fr", "it", "en"):
                    _v = name.get(_lang)
                    if isinstance(_v, str) and _v.strip():
                        labels_dict[_lang] = _v.strip()
                label = labels_dict.get("de") or labels_dict.get("fr") or labels_dict.get("it") or labels_dict.get("en") or ""
            elif isinstance(name, str):
                label = name
                labels_dict = {"de": name}
            level = node.get("level", "")
            nodes[code_str] = {
                "code": code_str,
                "label": label,
                "labels": labels_dict,
                "level": level,
                "own_count": count,
                "count": count,
                "children": [],
            }

    # Pass 1: build parent→child links only, no count mutation
    for code in nodes:
        parent = parent_map.get(code)
        while parent and parent != code:
            if parent in nodes:
                if nodes[code] not in nodes[parent]["children"]:
                    nodes[parent]["children"].append(nodes[code])
                break
            parent = parent_map.get(parent)

    # Pass 2: find root nodes (not a child of any other node)
    all_child_codes: set[str] = set()
    for node in nodes.values():
        for child in node["children"]:
            all_child_codes.add(child["code"])
    roots = [node for code, node in sorted(nodes.items()) if code not in all_child_codes]

    # Pass 3: sort children by code (hierarchy order)
    def _sort_children(node: dict) -> None:
        node["children"].sort(key=lambda x: x["code"])
        for child in node["children"]:
            _sort_children(child)

    for root in roots:
        _sort_children(root)
    roots.sort(key=lambda x: x["code"])

    # Pass 4: aggregate counts bottom-up so each node = own + all descendants
    def _aggregate(node: dict) -> int:
        total = node["own_count"]
        for child in node["children"]:
            total += _aggregate(child)
        node["count"] = total
        return total

    for root in roots:
        _aggregate(root)

    return roots


# ---------------------------------------------------------------------------
# Taxonomy stats — global aggregates (clusters, keywords, categories, NOGA,
# cantons, legal forms) computed live per request. Tags are per-org and also
# queried live. Consumers: GET /companies/analytics/taxonomy and the
# semantic-search fuzzy fallback.
# ---------------------------------------------------------------------------

def _compute_global_taxonomy(db: Session) -> dict[str, Any]:
    """Run all expensive global taxonomy queries. Result is cached."""
    base_q = db.query(Company)

    try:
        cluster_rows = db.execute(_text(
            "SELECT cluster AS label, COUNT(DISTINCT company_id) AS cnt"
            " FROM company_tfidf_clusters"
            " WHERE cluster != 'Undefined'"
            " GROUP BY cluster ORDER BY cnt DESC"
        )).fetchall()
        clusters_list = [(r.label, r.cnt) for r in cluster_rows if r.label]
    except Exception:
        logger.warning("company_tfidf_clusters not yet available, falling back to UNNEST")
        cluster_rows = db.execute(_text(
            "SELECT trim(unnest(string_to_array(tfidf_cluster, '|'))) AS label, COUNT(*) AS cnt"
            " FROM companies"
            " WHERE tfidf_cluster IS NOT NULL AND tfidf_cluster != 'Undefined'"
            " GROUP BY label ORDER BY cnt DESC"
        )).fetchall()
        clusters_list = [(r.label, r.cnt) for r in cluster_rows if r.label]

    try:
        kw_rows = db.execute(_text(
            "SELECT keyword AS kw, COUNT(DISTINCT company_id) AS cnt"
            " FROM company_purpose_keywords"
            " GROUP BY keyword ORDER BY cnt DESC LIMIT 100"
        )).fetchall()
        keywords_list = [(r.kw, r.cnt) for r in kw_rows if r.kw]
    except Exception:
        logger.warning("company_purpose_keywords not yet available, falling back to UNNEST")
        kw_rows = db.execute(_text(
            "SELECT trim(unnest(string_to_array(purpose_keywords, ','))) AS kw, COUNT(*) AS cnt"
            " FROM companies"
            " WHERE purpose_keywords IS NOT NULL"
            " GROUP BY kw ORDER BY cnt DESC LIMIT 100"
        )).fetchall()
        keywords_list = [(r.kw, r.cnt) for r in kw_rows if r.kw]

    categories = (
        base_q.with_entities(Company.ai_category, func.count(Company.id).label("cnt"))
        .filter(Company.ai_category.isnot(None))
        .group_by(Company.ai_category)
        .order_by(func.count(Company.id).desc())
        .all()
    )

    cat_scores = (
        base_q.with_entities(
            Company.ai_category,
            func.avg(Company.ai_score).label("avg_ai"),
            func.avg(Company.flex_score).label("avg_flex"),
        )
        .filter(Company.ai_category.isnot(None))
        .group_by(Company.ai_category)
        .all()
    )

    noga_codes = (
        base_q.with_entities(Company.noga_code, Company.noga_label, func.count(Company.id).label("cnt"))
        .filter(Company.noga_code.isnot(None))
        .group_by(Company.noga_code, Company.noga_label)
        .order_by(func.count(Company.id).desc())
        .all()
    )
    noga_levels = (
        base_q.with_entities(Company.noga_level, func.count(Company.id).label("cnt"))
        .filter(Company.noga_level.isnot(None))
        .group_by(Company.noga_level)
        .order_by(func.count(Company.id).desc())
        .all()
    )
    legal_forms = (
        base_q.with_entities(Company.legal_form, func.count(Company.id).label("cnt"))
        .filter(Company.legal_form.isnot(None))
        .group_by(Company.legal_form)
        .order_by(func.count(Company.id).desc())
        .all()
    )
    cantons = (
        base_q.with_entities(Company.canton, func.count(Company.id).label("cnt"))
        .filter(Company.canton.isnot(None))
        .group_by(Company.canton)
        .order_by(func.count(Company.id).desc())
        .all()
    )
    cat_score_map = {
        r.ai_category: {
            "avg_ai_score": round(r.avg_ai) if r.avg_ai is not None else None,
            "avg_flex_score": round(r.avg_flex) if r.avg_flex is not None else None,
        }
        for r in cat_scores
    }
    categories_enriched = [
        (r.ai_category, r.cnt, cat_score_map.get(r.ai_category, {}))
        for r in categories
    ]

    return {
        "clusters": clusters_list,
        "keywords": keywords_list,
        "categories": [(r.ai_category, r.cnt) for r in categories],
        "categories_enriched": categories_enriched,
        "noga_codes": [(((r.noga_code or "") + " — " + (r.noga_label or "")).strip(" —"), r.cnt) for r in noga_codes],
        "noga_levels": [(r.noga_level, r.cnt) for r in noga_levels],
        "legal_forms": [(r.legal_form, r.cnt) for r in legal_forms],
        "cantons": [(r.canton, r.cnt) for r in cantons],
    }


def get_taxonomy_stats(db: Session, org_id: int | None = None) -> dict:
    """Return taxonomy counts for the explorer Layer 1 grid.

    Global fields and per-org tags are computed live on each call.
    """
    global_data = _compute_global_taxonomy(db)

    # Tags are per-org workflow data stored in OrgCompanyState — always live.
    from app.models.org_company_state import OrgCompanyState
    org_q = db.query(Company)
    if org_id:
        org_q = org_q.join(
            OrgCompanyState,
            (OrgCompanyState.company_id == Company.id) & (OrgCompanyState.org_id == org_id),
        )
    tags = (
        org_q.with_entities(Company.tags, func.count(Company.id).label("cnt"))
        .filter(Company.tags.isnot(None))
        .group_by(Company.tags)
        .order_by(func.count(Company.id).desc())
        .all()
    )

    return {
        **global_data,
        "tags": [(r.tags, r.cnt) for r in tags],
    }


# ---------------------------------------------------------------------------
# Category stats — score-landscape aggregates for a single category value,
# computed live per request. Consumer: GET /companies/analytics/category-stats.
# ---------------------------------------------------------------------------

def get_category_stats(
    db: Session,
    category_type: str,
    value: str,
    org_id: int | None = None,
) -> dict:
    """Return score landscape stats for a category value (computed live)."""
    return _compute_category_stats(db, category_type, value, org_id)


def _compute_category_stats(
    db: Session,
    category_type: str,
    value: str,
    org_id: int | None = None,
) -> dict:
    """Return score landscape stats for a specific category value.

    All aggregation is done in a single SQL query — no rows fetched into Python.
    Uses the stored combined_score column for band counts and averages.
    """
    _TYPE_TO_COL = {
        "ai_category": "ai_category",
        "tfidf_cluster": "tfidf_cluster",
        "keyword": "purpose_keywords",
        "noga_code": "noga_code",
    }
    if category_type not in _TYPE_TO_COL:
        return {"error": f"Unknown category_type: {category_type}"}

    col = _TYPE_TO_COL[category_type]

    if category_type == "tfidf_cluster":
        junction_table = "company_tfidf_clusters"
        junction_col = "cluster"
    elif category_type == "keyword":
        junction_table = "company_purpose_keywords"
        junction_col = "keyword"
    else:
        junction_table = None

    # For NOGA: build path-aware WHERE clause to match hierarchy (sections, divisions, groups)
    noga_where_extra = ""
    if category_type == "noga_code":
        noga_where_extra = (
            " OR c.noga_path = :value"
            " OR c.noga_path LIKE :noga_pfx"
            " OR c.noga_path LIKE :noga_mid"
            " OR c.noga_path LIKE :noga_sfx"
        )

    computed_score_expr = (
        "CASE "
        "WHEN c.ai_score IS NOT NULL AND c.web_score IS NOT NULL AND c.flex_score IS NOT NULL "
        "THEN (0.70 * c.ai_score + 0.20 * c.web_score + 0.10 * c.flex_score) "
        "WHEN c.ai_score IS NOT NULL AND c.web_score IS NOT NULL "
        "THEN (0.70 * c.ai_score + 0.20 * c.web_score) / 0.90 "
        "WHEN c.ai_score IS NOT NULL AND c.flex_score IS NOT NULL "
        "THEN (0.70 * c.ai_score + 0.10 * c.flex_score) / 0.80 "
        "WHEN c.web_score IS NOT NULL AND c.flex_score IS NOT NULL "
        "THEN (0.20 * c.web_score + 0.10 * c.flex_score) / 0.30 "
        "WHEN c.ai_score IS NOT NULL THEN c.ai_score::float "
        "WHEN c.web_score IS NOT NULL THEN c.web_score::float "
        "WHEN c.flex_score IS NOT NULL THEN c.flex_score::float "
        "ELSE NULL END"
    )

    org_join = ""
    if org_id:
        org_join = "INNER JOIN org_company_state ocs ON ocs.company_id = c.id AND ocs.org_id = :org_id"
        bind_org: dict = {"value": value, "org_id": org_id}
    else:
        bind_org = {"value": value}

    if category_type == "noga_code":
        bind_org = {**bind_org, "noga_pfx": f"{value}|%", "noga_mid": f"%|{value}|%", "noga_sfx": f"%|{value}"}

    def _build_agg_sql(score_expr: str, *, fallback_string_match: bool = False):
        if junction_table and not fallback_string_match:
            from_clause = f"""
                companies c
                INNER JOIN {junction_table} j ON c.id = j.company_id
                {org_join}
            """
            where_cond = f"j.{junction_col} = :value"
        else:
            from_clause = f"companies c {org_join}"
            if category_type == "noga_code":
                where_cond = f"(c.{col} = :value{noga_where_extra})"
            elif junction_table and fallback_string_match:
                # Cluster/keyword fallback: match denormalized pipe/comma delimited column
                if category_type == "tfidf_cluster":
                    where_cond = (
                        f"(c.{col} = :value OR c.{col} LIKE :pfx OR c.{col} LIKE :mid OR c.{col} LIKE :sfx)"
                    )
                else:
                    where_cond = f"c.{col} LIKE :mid"
            else:
                where_cond = f"c.{col} = :value"

        return _text(f"""
        SELECT
            COUNT(DISTINCT c.id)                                         AS total,
            AVG(c.ai_score)                                              AS avg_ai,
            AVG(c.flex_score)                                            AS avg_flex,
            AVG(c.web_score)                                             AS avg_web,
            AVG({score_expr})                                            AS avg_combined,
            COUNT(CASE WHEN {score_expr} IS NULL THEN 1 END)             AS unscored,
            COUNT(CASE WHEN {score_expr} >= 80   THEN 1 END)             AS band_80plus,
            COUNT(CASE WHEN {score_expr} >= 60
                        AND {score_expr} < 80    THEN 1 END)             AS band_60to80,
            COUNT(CASE WHEN {score_expr} >= 40
                        AND {score_expr} < 60    THEN 1 END)             AS band_40to60,
            COUNT(CASE WHEN {score_expr} < 40    THEN 1 END)             AS band_below40
        FROM {from_clause}
        WHERE {where_cond}
    """)

    agg_sql = _build_agg_sql("c.combined_score")
    bind = bind_org

    def _build_canton_sql(*, fallback_string_match: bool = False):
        if junction_table and not fallback_string_match:
            from_clause = f"""
                companies c
                INNER JOIN {junction_table} j ON c.id = j.company_id
                {org_join}
            """
            where_cond = f"j.{junction_col} = :value AND c.canton IS NOT NULL"
        else:
            from_clause = f"companies c {org_join}"
            if category_type == "noga_code":
                where_cond = f"(c.{col} = :value{noga_where_extra}) AND c.canton IS NOT NULL"
            elif junction_table and fallback_string_match and category_type == "tfidf_cluster":
                where_cond = (
                    "(c.tfidf_cluster = :value OR c.tfidf_cluster LIKE :pfx"
                    " OR c.tfidf_cluster LIKE :mid OR c.tfidf_cluster LIKE :sfx)"
                    " AND c.canton IS NOT NULL"
                )
            else:
                where_cond = f"c.{col} = :value AND c.canton IS NOT NULL"

        return _text(f"""
            SELECT c.canton, COUNT(DISTINCT c.id) AS cnt
            FROM {from_clause}
            WHERE {where_cond}
            GROUP BY c.canton
            ORDER BY cnt DESC
            LIMIT 10
        """)

    # For cluster/keyword fallback: provide pipe/comma match bind params
    fallback_bind = {
        **bind_org,
        "pfx": f"{value}|%",
        "mid": f"%|{value}|%",
        "sfx": f"%|{value}",
    }

    agg_sql = _build_agg_sql("c.combined_score")
    canton_sql = _build_canton_sql()
    bind = bind_org
    _use_fallback = False

    try:
        db.execute(_text("SET LOCAL work_mem = '256MB'"))
        r = db.execute(agg_sql, bind).fetchone()

        # If junction table returned 0 but the string column may have data, use fallback
        if junction_table and (r is None or r.total == 0):
            logger.debug(
                "category_stats junction table returned 0 for %s=%s; checking string fallback",
                category_type, value,
            )
            _use_fallback = True
            agg_sql_fb = _build_agg_sql("c.combined_score", fallback_string_match=True)
            r_fb = db.execute(agg_sql_fb, fallback_bind).fetchone()
            if r_fb and r_fb.total > 0:
                logger.warning(
                    "Junction table stale for %s=%s: 0 rows but string column has %d; using string fallback",
                    category_type, value, r_fb.total,
                )
                r = r_fb
                canton_sql = _build_canton_sql(fallback_string_match=True)
                bind = fallback_bind

    except Exception as exc:
        msg = str(exc).lower()
        pgcode = getattr(getattr(exc, "orig", None), "pgcode", None)
        if pgcode != "42703" and "combined_score" not in msg:
            raise
        logger.warning("combined_score missing in category stats query; using computed fallback")
        db.rollback()
        r = db.execute(_build_agg_sql(computed_score_expr), bind_org).fetchone()

    cantons = db.execute(canton_sql, bind).fetchall()

    if not r or r.total == 0:
        return {
            "count": 0,
            "avg_ai_score": None, "avg_flex_score": None,
            "avg_web_score": None, "avg_combined_score": None,
            "bands": {"80plus": 0, "60to80": 0, "40to60": 0, "below40": 0, "unscored": 0},
            "canton_breakdown": [],
            "unscored_count": 0,
        }

    def _rnd(v):
        return round(float(v)) if v is not None else None

    return {
        "count": r.total,
        "avg_ai_score": _rnd(r.avg_ai),
        "avg_flex_score": _rnd(r.avg_flex),
        "avg_web_score": _rnd(r.avg_web),
        "avg_combined_score": _rnd(r.avg_combined),
        "bands": {
            "80plus": r.band_80plus,
            "60to80": r.band_60to80,
            "40to60": r.band_40to60,
            "below40": r.band_below40,
            "unscored": r.unscored,
        },
        "canton_breakdown": [(c.canton, c.cnt) for c in cantons],
        "unscored_count": r.unscored,
    }
