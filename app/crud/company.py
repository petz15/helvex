import logging
import threading
import time
from collections import Counter
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import case, func, nullslast, or_
from sqlalchemy.orm import Session

from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyUpdate

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
    "website_checked_at":   (Company.website_checked_at,  True),
    "-website_checked_at":  (Company.website_checked_at,  False),
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
                   business_model=None):
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
    if name_filter:
        query = query.filter(Company.name.ilike(f"%{name_filter}%"))
    if uid_filter:
        query = query.filter(Company.uid.ilike(f"%{uid_filter}%"))
    if canton:
        query = query.filter(Company.canton == canton)
    if review_status == "_none":
        query = query.filter(Company.review_status.is_(None))
    elif review_status:
        query = query.filter(Company.review_status == review_status)
    if contact_status == "_none":
        query = query.filter(Company.contact_status.is_(None))
    elif contact_status:
        query = query.filter(Company.contact_status == contact_status)
    if google_searched == "yes":
        query = query.filter(Company.website_checked_at.isnot(None))
    elif google_searched == "no":
        query = query.filter(Company.website_checked_at.is_(None))
    elif google_searched == "no_result":
        query = query.filter(
            Company.website_checked_at.isnot(None),
            Company.website_url.is_(None),
        )
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
    elif tfidf_cluster:
        terms = [t.strip() for t in tfidf_cluster.split(",") if t.strip()]
        # Pipe-boundary matching: exact label or appears between | separators
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
        # Use GIN-indexed array column when available; fall back to text boundary match.
        from sqlalchemy import text as _sqlt
        query = query.filter(or_(
            Company.purpose_keywords_arr.overlap(terms),   # uses GIN index
            # Fallback for rows not yet backfilled (purpose_keywords_arr IS NULL)
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
        query = query.filter(or_(*[Company.noga_code.ilike(f"%{t}%") for t in terms]))
    if noga_label:
        query = query.filter(Company.noga_label.ilike(f"%{noga_label}%"))
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
    )
    return query.count()


def get_company_stats(db: Session) -> dict:
    total = db.query(Company).count()
    searched = db.query(Company).filter(Company.website_checked_at.isnot(None)).count()
    with_website = db.query(Company).filter(Company.website_url.isnot(None)).count()

    # Google searches used today (by website_checked_at date)
    searches_today = (
        db.query(Company)
        .filter(func.date(Company.website_checked_at) == date.today())
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
    from sqlalchemy import text as _stext
    dist_rows = db.execute(_stext(
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


def update_company(db: Session, db_company: Company, company_in: CompanyUpdate) -> Company:
    update_data = company_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_company, field, value)
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
    rows = db.query(Company).filter(Company.id.in_(company_ids)).all()
    updated = 0
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
    db.commit()
    return updated


def search_keywords(db: Session, q: str, limit: int = 20) -> list[tuple[str, int]]:
    """Return individual keywords from purpose_keywords matching q, sorted by frequency."""
    raw = (
        db.query(Company.purpose_keywords)
        .filter(Company.purpose_keywords.isnot(None))
        .filter(Company.purpose_keywords.ilike(f"%{q}%"))
        .all()
    )
    counter: Counter = Counter()
    q_lower = q.lower()
    for (val,) in raw:
        for kw in val.split(","):
            kw = kw.strip()
            if kw and q_lower in kw.lower():
                counter[kw] += 1
    return counter.most_common(limit)


def search_clusters(db: Session, q: str, limit: int = 20) -> list[tuple[str, int]]:
    """Return cluster labels matching q, sorted by frequency."""
    raw = (
        db.query(Company.tfidf_cluster)
        .filter(Company.tfidf_cluster.isnot(None))
        .filter(Company.tfidf_cluster.ilike(f"%{q}%"))
        .all()
    )
    counter: Counter = Counter()
    q_lower = q.lower()
    for (val,) in raw:
        for label in val.split("|"):
            label = label.strip()
            if label and q_lower in label.lower():
                counter[label] += 1
    return counter.most_common(limit)


def get_noga_hierarchy(db: Session, org_id: int | None = None) -> list[dict]:
    """Return NOGA codes as a tree with aggregated counts.

    Each node: {code, label, level, own_count, count (aggregated), children}.
    Top-level sections are returned as the root list.
    """
    from app.models.org_company_state import OrgCompanyState

    query = db.query(Company.noga_code, Company.noga_label, Company.noga_level, func.count(Company.id).label("cnt"))
    query = query.filter(Company.noga_code.isnot(None))
    if org_id:
        query = query.join(OrgCompanyState, (OrgCompanyState.company_id == Company.id) & (OrgCompanyState.org_id == org_id), isouter=False)
    rows = query.group_by(Company.noga_code, Company.noga_label, Company.noga_level).all()

    nodes: dict[str, dict] = {}
    for r in rows:
        code = r.noga_code or ""
        nodes[code] = {
            "code": code,
            "label": r.noga_label or "",
            "level": r.noga_level or "",
            "own_count": r.cnt,
            "count": r.cnt,
            "children": [],
        }

    def _parent_code(code: str) -> str | None:
        """Derive parent code: 68.20 → 68.2 → 68 → section letter → None."""
        if "." in code:
            parts = code.split(".")
            sub = parts[-1]
            if len(sub) > 1:
                return ".".join(parts[:-1] + [sub[:-1]])
            return parts[0]
        if len(code) == 2 and code.isdigit():
            # division code like "68" — no known parent in flat data, skip
            return None
        if len(code) > 1:
            return code[:-1]
        return None

    # Propagate counts upward and wire children
    for code in sorted(nodes.keys(), key=len, reverse=True):
        parent = _parent_code(code)
        while parent is not None:
            if parent in nodes:
                nodes[parent]["count"] += nodes[code]["count"]
                if nodes[code] not in nodes[parent]["children"]:
                    nodes[parent]["children"].append(nodes[code])
                break
            parent = _parent_code(parent)

    # Return only root nodes (those with no parent in our node set)
    roots = []
    all_child_codes: set[str] = set()
    for node in nodes.values():
        for child in node["children"]:
            all_child_codes.add(child["code"])
    for code, node in sorted(nodes.items()):
        if code not in all_child_codes:
            roots.append(node)

    # Sort children by count desc at each level
    def _sort_children(node: dict) -> None:
        node["children"].sort(key=lambda x: -x["count"])
        for child in node["children"]:
            _sort_children(child)

    for root in roots:
        _sort_children(root)
    roots.sort(key=lambda x: -x["count"])
    return roots


# ---------------------------------------------------------------------------
# Taxonomy stats cache — stale-while-revalidate
# Global data (clusters, keywords, categories, NOGA, cantons, legal forms)
# is cached process-wide. When the TTL expires, stale data is returned
# immediately and a background thread silently refreshes the cache so the
# page never goes blank. Tags are per-org/cheap and always queried live.
# ---------------------------------------------------------------------------
_TAX_CACHE_TTL = 7200  # seconds (2 hours — also busted explicitly on ML job completion)
_tax_cache_lock = threading.Lock()
_tax_cache_data: dict[str, Any] | None = None
_tax_cache_ts: float = 0.0
_tax_cache_refreshing: bool = False  # true while a background refresh is in flight


def invalidate_taxonomy_cache() -> None:
    """Force next get_taxonomy_stats() call to trigger a background refresh."""
    global _tax_cache_ts
    with _tax_cache_lock:
        _tax_cache_ts = 0.0


def _refresh_taxonomy_cache_bg() -> None:
    """Background thread: recompute and store taxonomy cache without blocking callers."""
    global _tax_cache_data, _tax_cache_ts, _tax_cache_refreshing
    try:
        from app.database import SessionLocal
        with SessionLocal() as db:
            data = _compute_global_taxonomy(db)
        with _tax_cache_lock:
            _tax_cache_data = data
            _tax_cache_ts = time.monotonic()
    except Exception:
        logger.exception("taxonomy cache background refresh failed")
    finally:
        with _tax_cache_lock:
            _tax_cache_refreshing = False


def _compute_global_taxonomy(db: Session) -> dict[str, Any]:
    """Run all expensive global taxonomy queries. Result is cached."""
    from sqlalchemy import text as _text

    base_q = db.query(Company)

    cluster_rows = db.execute(_text(
        "SELECT trim(unnest(string_to_array(tfidf_cluster, '|'))) AS label, COUNT(*) AS cnt"
        " FROM companies"
        " WHERE tfidf_cluster IS NOT NULL AND tfidf_cluster != 'Undefined'"
        " GROUP BY label ORDER BY cnt DESC"
    )).fetchall()
    clusters_list = [(r.label, r.cnt) for r in cluster_rows if r.label]

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

    Global fields are served from a process-wide cache using stale-while-revalidate:
    - Cache cold (startup or first call): blocks once, then warms.
    - Cache stale (TTL expired): returns old data immediately, kicks a background
      thread to refresh so the page never goes blank mid-session.
    - Cache warm: instant return, no DB hit.
    Tags are per-org and always queried live (fast indexed GROUP BY).
    """
    global _tax_cache_data, _tax_cache_ts, _tax_cache_refreshing

    now = time.monotonic()
    with _tax_cache_lock:
        stale = _tax_cache_data is None or (now - _tax_cache_ts) >= _TAX_CACHE_TTL
        cold = _tax_cache_data is None
        already_refreshing = _tax_cache_refreshing

    if cold:
        # First ever call — block until we have data (startup warmer prevents this in prod).
        data = _compute_global_taxonomy(db)
        with _tax_cache_lock:
            _tax_cache_data = data
            _tax_cache_ts = time.monotonic()
            _tax_cache_refreshing = False
    elif stale and not already_refreshing:
        # Stale but not cold — return existing data now, refresh in background.
        with _tax_cache_lock:
            _tax_cache_refreshing = True
        t = threading.Thread(target=_refresh_taxonomy_cache_bg, daemon=True)
        t.start()

    with _tax_cache_lock:
        global_data = _tax_cache_data

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
# Category stats cache — stale-while-revalidate (same pattern as taxonomy)
# Keyed by (category_type, value, org_id). TTL 2h; busted on ML job completion.
# ---------------------------------------------------------------------------
_CAT_CACHE_TTL = 7200
_cat_cache_lock = threading.Lock()
_cat_cache: dict[tuple, dict[str, Any]] = {}  # key → {"data": ..., "ts": float, "refreshing": bool}


def invalidate_category_stats_cache() -> None:
    """Bust all cached category stats (called after ML/classify jobs)."""
    with _cat_cache_lock:
        _cat_cache.clear()


def _refresh_cat_cache_bg(category_type: str, value: str, org_id: int | None) -> None:
    global _cat_cache
    key = (category_type, value, org_id)
    try:
        from app.database import SessionLocal
        with SessionLocal() as db:
            data = _compute_category_stats(db, category_type, value, org_id)
        with _cat_cache_lock:
            _cat_cache[key] = {"data": data, "ts": time.monotonic(), "refreshing": False}
    except Exception:
        logger.exception("category stats cache background refresh failed for %s=%s", category_type, value)
        with _cat_cache_lock:
            if key in _cat_cache:
                _cat_cache[key]["refreshing"] = False


def get_category_stats(
    db: Session,
    category_type: str,
    value: str,
    org_id: int | None = None,
) -> dict:
    """Stale-while-revalidate wrapper around _compute_category_stats."""
    key = (category_type, value, org_id)
    now = time.monotonic()
    with _cat_cache_lock:
        entry = _cat_cache.get(key)
        cold = entry is None
        stale = cold or (now - entry["ts"]) >= _CAT_CACHE_TTL
        already_refreshing = (not cold) and entry.get("refreshing", False)

    if cold:
        data = _compute_category_stats(db, category_type, value, org_id)
        with _cat_cache_lock:
            _cat_cache[key] = {"data": data, "ts": time.monotonic(), "refreshing": False}
        return data
    if stale and not already_refreshing:
        with _cat_cache_lock:
            _cat_cache[key]["refreshing"] = True
        t = threading.Thread(target=_refresh_cat_cache_bg, args=(category_type, value, org_id), daemon=True)
        t.start()
    with _cat_cache_lock:
        return _cat_cache[key]["data"]


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
    from sqlalchemy import text as _text

    _TYPE_TO_COL = {
        "ai_category": "ai_category",
        "tfidf_cluster": "tfidf_cluster",
        "keyword": "purpose_keywords",
        "noga_code": "noga_code",
    }
    if category_type not in _TYPE_TO_COL:
        return {"error": f"Unknown category_type: {category_type}"}

    col = _TYPE_TO_COL[category_type]
    sep = "|" if category_type == "tfidf_cluster" else ","
    if category_type in ("tfidf_cluster", "keyword"):
        # Boundary matching: exact value, or value between separators (no false positives
        # from substring overlap, e.g. "Beratung" matching "Rechtsberatung").
        where_clause = (
            f"({col} = :v"
            f" OR {col} LIKE :pat_prefix"
            f" OR {col} LIKE :pat_suffix"
            f" OR {col} LIKE :pat_mid)"
        )
        bind = {
            "v": value,
            "pat_prefix": f"{value}{sep}%",
            "pat_suffix": f"%{sep}{value}",
            "pat_mid": f"%{sep}{value}{sep}%",
        }
    else:
        where_clause = f"{col} = :value"
        bind = {"value": value}

    # All aggregates use the stored combined_score column — no complex CASE expressions.
    agg_sql = _text(f"""
        SELECT
            COUNT(*)                                                    AS total,
            AVG(ai_score)                                               AS avg_ai,
            AVG(flex_score)                                             AS avg_flex,
            AVG(web_score)                                              AS avg_web,
            AVG(combined_score)                                         AS avg_combined,
            COUNT(CASE WHEN combined_score IS NULL THEN 1 END)          AS unscored,
            COUNT(CASE WHEN combined_score >= 80   THEN 1 END)          AS band_80plus,
            COUNT(CASE WHEN combined_score >= 60
                        AND combined_score < 80    THEN 1 END)          AS band_60to80,
            COUNT(CASE WHEN combined_score >= 40
                        AND combined_score < 60    THEN 1 END)          AS band_40to60,
            COUNT(CASE WHEN combined_score < 40    THEN 1 END)          AS band_below40
        FROM companies
        WHERE {where_clause}
    """)

    canton_sql = _text(f"""
        SELECT canton, COUNT(*) AS cnt
        FROM companies
        WHERE {where_clause} AND canton IS NOT NULL
        GROUP BY canton
        ORDER BY cnt DESC
        LIMIT 10
    """)

    r = db.execute(agg_sql, bind).fetchone()
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
