"""REST endpoints for SOGC person entities, appearances, and auditors."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_superadmin
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["persons"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class PersonEntityOut(BaseModel):
    id: int
    normalized_key: str
    lastname: str | None
    firstname: str | None
    hometown_municipality: str | None
    current_residence_municipality: str | None
    is_foreign: bool
    nationality: str | None
    confidence_level: str
    is_verified: bool
    verified_at: str | None
    appearance_count: int
    active_company_count: int
    linkedin_url: str | None
    linkedin_verified_at: str | None
    merged_into_id: int | None
    identity_notes: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_orm(cls, e, residence: str | None = None, active_company_count: int | None = None) -> "PersonEntityOut":
        return cls(
            id=e.id,
            normalized_key=e.normalized_key,
            lastname=e.lastname,
            firstname=e.firstname,
            hometown_municipality=e.hometown_municipality,
            current_residence_municipality=residence,
            is_foreign=e.is_foreign,
            nationality=e.nationality,
            confidence_level=e.confidence_level,
            is_verified=e.is_verified,
            verified_at=e.verified_at.isoformat() if e.verified_at else None,
            appearance_count=e.appearance_count,
            active_company_count=active_company_count if active_company_count is not None else e.active_company_count,
            linkedin_url=e.linkedin_url,
            linkedin_verified_at=e.linkedin_verified_at.isoformat() if e.linkedin_verified_at else None,
            merged_into_id=e.merged_into_id,
            identity_notes=e.identity_notes,
            created_at=e.created_at.isoformat() if e.created_at else "",
            updated_at=e.updated_at.isoformat() if e.updated_at else "",
        )


class PersonAppearanceOut(BaseModel):
    id: int
    person_entity_id: int
    entity_override_id: int | None
    sogc_change_id: int | None
    sogc_publication_id: int | None
    company_uid: str | None
    company_id: int | None = None
    company_name: str | None = None
    pub_date: str | None
    change_type: str
    change_subtype: str | None = None
    role: str | None
    role_category: str | None
    signature_type: str | None
    bisher_role: str | None
    residence_municipality: str | None
    is_current: bool | None
    title: str | None
    raw_excerpt: str | None
    created_at: str

    @classmethod
    def from_orm(cls, a, company_id: int | None = None, company_name: str | None = None) -> "PersonAppearanceOut":
        return cls(
            id=a.id,
            person_entity_id=a.person_entity_id,
            entity_override_id=a.entity_override_id,
            sogc_change_id=a.sogc_change_id,
            sogc_publication_id=a.sogc_publication_id,
            company_uid=a.company_uid,
            company_id=company_id,
            company_name=company_name,
            pub_date=a.pub_date,
            change_type=a.change_type,
            change_subtype=a.change_subtype,
            role=a.role,
            role_category=a.role_category,
            signature_type=a.signature_type,
            bisher_role=a.bisher_role,
            residence_municipality=a.residence_municipality,
            is_current=a.is_current,
            title=a.title,
            raw_excerpt=a.raw_excerpt,
            created_at=a.created_at.isoformat() if a.created_at else "",
        )


class AuditorOut(BaseModel):
    id: int
    sogc_change_id: int | None
    sogc_publication_id: int | None
    company_uid: str | None
    company_id: int | None = None
    company_name: str | None = None
    pub_date: str | None
    change_type: str
    auditor_name: str | None
    auditor_uid: str | None
    auditor_legal_form: str | None
    auditor_location: str | None
    auditor_name_normalized: str | None
    is_current: bool | None
    created_at: str

    @classmethod
    def from_orm(cls, a, company_id: int | None = None, company_name: str | None = None) -> "AuditorOut":
        return cls(
            id=a.id,
            sogc_change_id=a.sogc_change_id,
            sogc_publication_id=a.sogc_publication_id,
            company_uid=a.company_uid,
            company_id=company_id,
            company_name=company_name,
            pub_date=a.pub_date,
            change_type=a.change_type,
            auditor_name=a.auditor_name,
            auditor_uid=a.auditor_uid,
            auditor_legal_form=a.auditor_legal_form,
            auditor_location=a.auditor_location,
            auditor_name_normalized=a.auditor_name_normalized,
            is_current=a.is_current,
            created_at=a.created_at.isoformat() if a.created_at else "",
        )


class SogcChangeOut(BaseModel):
    id: int
    change_type: str
    change_subtype: str | None = None
    keywords_matched: str | None
    raw_excerpt: str | None

    @classmethod
    def from_orm(cls, c) -> "SogcChangeOut":
        return cls(
            id=c.id,
            change_type=c.change_type,
            change_subtype=c.change_subtype,
            keywords_matched=c.keywords_matched,
            raw_excerpt=c.raw_excerpt,
        )


class SogcPublicationOut(BaseModel):
    id: int
    sogc_id: str
    company_uid: str | None
    company_id: int | None
    pub_date: str | None
    sub_rubric: str | None
    pub_number: str | None
    text_de: str | None
    text_fr: str | None
    text_it: str | None
    text_en: str | None
    detected_language: str | None
    preprocessed_at: str | None
    changes: list[SogcChangeOut] = []

    @classmethod
    def from_orm(cls, p) -> "SogcPublicationOut":
        return cls(
            id=p.id,
            sogc_id=p.sogc_id,
            company_uid=p.company_uid,
            company_id=p.company_id,
            pub_date=p.pub_date,
            sub_rubric=p.sub_rubric,
            pub_number=p.pub_number,
            text_de=p.text_de,
            text_fr=p.text_fr,
            text_it=p.text_it,
            text_en=p.text_en,
            detected_language=p.detected_language,
            preprocessed_at=p.preprocessed_at.isoformat() if p.preprocessed_at else None,
            changes=[SogcChangeOut.from_orm(c) for c in (p.changes or [])],
        )


class PersonFlagOut(BaseModel):
    id: int
    flag_type: str
    primary_entity_id: int
    secondary_entity_id: int | None
    appearance_id: int | None
    reason: str | None
    is_resolved: bool
    resolution_action: str | None
    resolved_at: str | None
    reported_by_user_id: int | None
    created_at: str

    @classmethod
    def from_orm(cls, f) -> "PersonFlagOut":
        return cls(
            id=f.id,
            flag_type=f.flag_type,
            primary_entity_id=f.primary_entity_id,
            secondary_entity_id=f.secondary_entity_id,
            appearance_id=f.appearance_id,
            reason=f.reason,
            is_resolved=f.is_resolved,
            resolution_action=f.resolution_action,
            resolved_at=f.resolved_at.isoformat() if f.resolved_at else None,
            reported_by_user_id=f.reported_by_user_id,
            created_at=f.created_at.isoformat() if f.created_at else "",
        )


class ReportFlagBody(BaseModel):
    flag_type: str           # "should_merge" | "should_split"
    secondary_entity_id: int | None = None
    appearance_id: int | None = None
    reason: str | None = None


class CorporateRoleOut(BaseModel):
    id: int
    sogc_change_id: int | None
    sogc_publication_id: int | None
    entity_name: str | None
    entity_name_normalized: str | None
    entity_che: str | None
    entity_location: str | None
    entity_legal_form: str | None
    company_uid: str | None
    company_id: int | None
    company_name: str | None
    role: str | None
    role_details: str | None
    raw_excerpt: str | None
    pub_date: str | None
    change_type: str | None
    change_subtype: str | None
    is_current: bool | None
    created_at: str

    @classmethod
    def from_orm(cls, r) -> "CorporateRoleOut":
        return cls(
            id=r.id,
            sogc_change_id=r.sogc_change_id,
            sogc_publication_id=r.sogc_publication_id,
            entity_name=r.entity_name,
            entity_name_normalized=r.entity_name_normalized,
            entity_che=r.entity_che,
            entity_location=r.entity_location,
            entity_legal_form=r.entity_legal_form,
            company_uid=r.company_uid,
            company_id=r.company_id,
            company_name=r.company_name,
            role=r.role,
            role_details=r.role_details,
            raw_excerpt=r.raw_excerpt,
            pub_date=r.pub_date,
            change_type=r.change_type,
            change_subtype=r.change_subtype,
            is_current=r.is_current,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )


# ── Person entity endpoints ────────────────────────────────────────────────────

@router.get("/sogc/persons/search", response_model=list[PersonEntityOut])
def search_persons(
    q: str | None = Query(None, description="Partial match on lastname or firstname"),
    hometown: str | None = Query(None),
    confidence_level: str | None = Query(None),
    nationality: str | None = Query(None),
    min_active_companies: int | None = Query(None, ge=0),
    is_verified: bool | None = Query(None),
    is_current: bool | None = Query(None, description="Filter entities with at least one current appearance"),
    sort_by: str | None = Query(None, description="'companies' (default), 'confidence', or 'appearances'"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from sqlalchemy import case, func

    qry = db.query(SogcPersonEntity).filter(SogcPersonEntity.merged_into_id.is_(None))

    if q:
        term = f"%{q.lower()}%"
        qry = qry.filter(
            (func.lower(SogcPersonEntity.lastname).like(term)) |
            (func.lower(SogcPersonEntity.firstname).like(term))
        )
    if hometown:
        qry = qry.filter(func.lower(SogcPersonEntity.hometown_municipality).like(f"%{hometown.lower()}%"))
    if confidence_level:
        qry = qry.filter(SogcPersonEntity.confidence_level == confidence_level)
    if nationality:
        qry = qry.filter(func.lower(SogcPersonEntity.nationality).like(f"%{nationality.lower()}%"))
    if min_active_companies is not None:
        qry = qry.filter(SogcPersonEntity.active_company_count >= min_active_companies)
    if is_verified is not None:
        qry = qry.filter(SogcPersonEntity.is_verified == is_verified)
    if is_current is True:
        from sqlalchemy import exists as sql_exists
        qry = qry.filter(
            sql_exists().where(
                SogcPersonAppearance.person_entity_id == SogcPersonEntity.id,
                SogcPersonAppearance.is_current.is_(True),
            )
        )

    if sort_by == "confidence":
        conf_order = case(
            (SogcPersonEntity.confidence_level == "high", 0),
            (SogcPersonEntity.confidence_level == "medium", 1),
            (SogcPersonEntity.confidence_level == "low", 2),
            else_=3,
        )
        entities = qry.order_by(conf_order, SogcPersonEntity.active_company_count.desc()).offset(offset).limit(limit).all()
    elif sort_by == "appearances":
        entities = qry.order_by(SogcPersonEntity.appearance_count.desc(), SogcPersonEntity.id.desc()).offset(offset).limit(limit).all()
    else:
        entities = qry.order_by(SogcPersonEntity.active_company_count.desc(), SogcPersonEntity.id.desc()).offset(offset).limit(limit).all()

    # Batch-fetch most recent current residence for each entity (avoids N+1)
    entity_ids = [e.id for e in entities]
    residence_map: dict[int, str] = {}
    active_count_map: dict[int, int] = {}
    if entity_ids:
        rows = (
            db.query(SogcPersonAppearance.person_entity_id, SogcPersonAppearance.residence_municipality)
            .filter(
                SogcPersonAppearance.person_entity_id.in_(entity_ids),
                SogcPersonAppearance.is_current.isnot(False),
                SogcPersonAppearance.residence_municipality.isnot(None),
            )
            .order_by(SogcPersonAppearance.person_entity_id, SogcPersonAppearance.pub_date.desc())
            .all()
        )
        for pe_id, res in rows:
            if pe_id not in residence_map:
                residence_map[pe_id] = res

        # Batch-compute real active company counts from appearances (cached value may be stale)
        active_rows = (
            db.query(SogcPersonAppearance.person_entity_id, func.count(SogcPersonAppearance.id))
            .filter(
                SogcPersonAppearance.person_entity_id.in_(entity_ids),
                SogcPersonAppearance.is_current.is_(True),
            )
            .group_by(SogcPersonAppearance.person_entity_id)
            .all()
        )
        active_count_map = {pe_id: cnt for pe_id, cnt in active_rows}

    return [PersonEntityOut.from_orm(e, residence_map.get(e.id), active_count_map.get(e.id, e.active_company_count)) for e in entities]


@router.get("/sogc/persons/flags", response_model=list[PersonFlagOut])
def list_person_flags(
    is_resolved: bool = Query(False),
    flag_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    from app.models.sogc_person_flag import SogcPersonFlag

    qry = db.query(SogcPersonFlag).filter_by(is_resolved=is_resolved)
    if flag_type:
        qry = qry.filter_by(flag_type=flag_type)
    flags = qry.order_by(SogcPersonFlag.created_at.desc()).limit(limit).all()
    return [PersonFlagOut.from_orm(f) for f in flags]


@router.get("/sogc/persons/{entity_id}", response_model=PersonEntityOut)
def get_person_entity(
    entity_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_person_entity import SogcPersonEntity

    from app.models.sogc_person_appearance import SogcPersonAppearance
    from sqlalchemy import func

    entity = db.get(SogcPersonEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Person entity not found")
    # Follow merge chain
    if entity.merged_into_id:
        canonical = db.get(SogcPersonEntity, entity.merged_into_id)
        if canonical:
            entity = canonical
    live_active_count = (
        db.query(func.count(SogcPersonAppearance.id))
        .filter(
            SogcPersonAppearance.person_entity_id == entity.id,
            SogcPersonAppearance.is_current.is_(True),
        )
        .scalar() or 0
    )
    return PersonEntityOut.from_orm(entity, active_company_count=live_active_count)


@router.get("/sogc/persons/{entity_id}/appearances", response_model=list[PersonAppearanceOut])
def get_person_appearances(
    entity_id: int,
    is_current: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from sqlalchemy import or_

    qry = db.query(SogcPersonAppearance).filter(
        or_(
            SogcPersonAppearance.person_entity_id == entity_id,
            SogcPersonAppearance.entity_override_id == entity_id,
        )
    )
    if is_current is not None:
        qry = qry.filter(SogcPersonAppearance.is_current == is_current)
    appearances = qry.order_by(SogcPersonAppearance.pub_date.desc()).limit(limit).all()

    # Batch-fetch company id+name to avoid N+1
    uids = [a.company_uid for a in appearances if a.company_uid]
    company_lookup: dict[str, tuple[int, str]] = {}
    if uids:
        from app.models.company import Company as CompanyModel
        for uid, cid, cname in (
            db.query(CompanyModel.uid, CompanyModel.id, CompanyModel.name)
            .filter(CompanyModel.uid.in_(uids))
            .all()
        ):
            company_lookup[uid] = (cid, cname)

    return [
        PersonAppearanceOut.from_orm(
            a,
            company_id=company_lookup.get(a.company_uid, (None, None))[0] if a.company_uid else None,
            company_name=company_lookup.get(a.company_uid, (None, None))[1] if a.company_uid else None,
        )
        for a in appearances
    ]


class CoDirectorOut(BaseModel):
    entity_id: int
    lastname: str | None
    firstname: str | None
    role: str | None
    role_category: str | None
    is_current: bool | None
    active_company_count: int


class MandateItem(BaseModel):
    company_uid: str
    company_id: int | None
    company_name: str | None
    role: str | None
    role_category: str | None
    signature_type: str | None
    date_from: str | None
    date_to: str | None
    is_current: bool | None
    co_directors: list[CoDirectorOut]


class PersonNetworkOut(BaseModel):
    entity: PersonEntityOut
    mandates: list[MandateItem]


@router.get("/sogc/persons/{entity_id}/network", response_model=PersonNetworkOut)
def get_person_network(
    entity_id: int,
    include_past: bool = Query(True, description="Include past (non-current) mandates"),
    co_director_limit: int = Query(8, ge=1, le=30),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return ego-network data for one person: their company mandates and
    co-directors at each company.  Used by the timeline and network graph views."""
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from app.models.company import Company as CompanyModel
    from sqlalchemy import func, or_

    entity = db.get(SogcPersonEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Person entity not found")
    if entity.merged_into_id:
        canonical = db.get(SogcPersonEntity, entity.merged_into_id)
        if canonical:
            entity = canonical

    # --- Step 1: load all appearances for this entity ---------------------------
    app_qry = db.query(SogcPersonAppearance).filter(
        or_(
            SogcPersonAppearance.person_entity_id == entity.id,
            SogcPersonAppearance.entity_override_id == entity.id,
        )
    )
    all_appearances = app_qry.order_by(SogcPersonAppearance.pub_date.asc()).all()

    # Group by company_uid: track date_from, date_to, latest role/sig/is_current
    from collections import defaultdict
    company_data: dict[str, dict] = defaultdict(lambda: {
        "date_from": None, "date_to": None,
        "role": None, "role_category": None,
        "signature_type": None, "is_current": None,
    })
    for a in all_appearances:
        if not a.company_uid:
            continue
        d = company_data[a.company_uid]
        if d["date_from"] is None or (a.pub_date and a.pub_date < d["date_from"]):
            d["date_from"] = a.pub_date
        if a.change_type == "person_removed":
            if d["date_to"] is None or (a.pub_date and a.pub_date > d["date_to"]):
                d["date_to"] = a.pub_date
        if a.pub_date and (d.get("_latest") is None or a.pub_date >= d["_latest"]):
            d["_latest"] = a.pub_date
            d["role"] = a.role
            d["role_category"] = a.role_category
            d["signature_type"] = a.signature_type
            d["is_current"] = a.is_current

    if not include_past:
        company_data = {uid: d for uid, d in company_data.items() if d["is_current"]}

    company_uids = list(company_data.keys())

    # --- Step 2: batch-fetch company names/ids ----------------------------------
    company_lookup: dict[str, tuple[int | None, str | None]] = {}
    if company_uids:
        for uid, cid, cname in (
            db.query(CompanyModel.uid, CompanyModel.id, CompanyModel.name)
            .filter(CompanyModel.uid.in_(company_uids))
            .all()
        ):
            company_lookup[uid] = (cid, cname)

    # --- Step 3: co-directors at those companies --------------------------------
    co_dir_rows: list[tuple] = []
    if company_uids:
        co_dir_rows = (
            db.query(
                SogcPersonAppearance.company_uid,
                SogcPersonAppearance.person_entity_id,
                SogcPersonEntity.lastname,
                SogcPersonEntity.firstname,
                SogcPersonAppearance.role,
                SogcPersonAppearance.role_category,
                SogcPersonAppearance.is_current,
                SogcPersonEntity.active_company_count,
            )
            .join(SogcPersonEntity, SogcPersonAppearance.person_entity_id == SogcPersonEntity.id)
            .filter(
                SogcPersonAppearance.company_uid.in_(company_uids),
                SogcPersonAppearance.person_entity_id != entity.id,
                SogcPersonEntity.merged_into_id.is_(None),
                SogcPersonAppearance.is_current.isnot(False),
            )
            .order_by(SogcPersonEntity.active_company_count.desc())
            .limit(co_director_limit * len(company_uids))
            .all()
        )

    # Group co-directors by company, capped at co_director_limit per company
    co_dirs_by_company: dict[str, list[CoDirectorOut]] = defaultdict(list)
    seen_per_company: dict[str, set] = defaultdict(set)
    for row in co_dir_rows:
        uid, eid, ln, fn, role, rc, ic, acc = row
        if eid in seen_per_company[uid]:
            continue
        if len(co_dirs_by_company[uid]) >= co_director_limit:
            continue
        seen_per_company[uid].add(eid)
        co_dirs_by_company[uid].append(CoDirectorOut(
            entity_id=eid,
            lastname=ln,
            firstname=fn,
            role=role,
            role_category=rc,
            is_current=ic,
            active_company_count=acc or 0,
        ))

    # --- Build output -----------------------------------------------------------
    live_active_count = (
        db.query(func.count(SogcPersonAppearance.id))
        .filter(
            SogcPersonAppearance.person_entity_id == entity.id,
            SogcPersonAppearance.is_current.is_(True),
        )
        .scalar() or 0
    )

    mandates = []
    for uid, d in sorted(company_data.items(), key=lambda x: x[1].get("date_from") or ""):
        cid, cname = company_lookup.get(uid, (None, None))
        mandates.append(MandateItem(
            company_uid=uid,
            company_id=cid,
            company_name=cname,
            role=d["role"],
            role_category=d["role_category"],
            signature_type=d["signature_type"],
            date_from=d["date_from"],
            date_to=d["date_to"],
            is_current=d["is_current"],
            co_directors=co_dirs_by_company.get(uid, []),
        ))

    return PersonNetworkOut(
        entity=PersonEntityOut.from_orm(entity, active_company_count=live_active_count),
        mandates=mandates,
    )


@router.post(
    "/sogc/persons/{entity_id}/flag",
    response_model=PersonFlagOut,
    status_code=status.HTTP_201_CREATED,
)
def report_person_flag(
    entity_id: int,
    body: ReportFlagBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_flag import SogcPersonFlag

    entity = db.get(SogcPersonEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Person entity not found")

    if body.flag_type not in ("should_merge", "should_split"):
        raise HTTPException(status_code=422, detail="flag_type must be 'should_merge' or 'should_split'")

    flag = SogcPersonFlag(
        flag_type=body.flag_type,
        primary_entity_id=entity_id,
        secondary_entity_id=body.secondary_entity_id,
        appearance_id=body.appearance_id,
        reason=body.reason,
        reported_by_user_id=current_user.id,
    )
    db.add(flag)
    db.commit()
    db.refresh(flag)
    return PersonFlagOut.from_orm(flag)


# ── Auditor endpoints ──────────────────────────────────────────────────────────

@router.get("/sogc/auditors/search", response_model=list[AuditorOut])
def search_auditors(
    q: str | None = Query(None),
    location: str | None = Query(None),
    legal_form: str | None = Query(None),
    is_current: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_auditor import SogcAuditor
    from app.models.company import Company as CompanyModel
    from sqlalchemy import or_

    conditions = []
    if q is not None:
        conditions.append(SogcAuditor.auditor_name_normalized.like(f"%{q.lower()}%"))
    if location is not None:
        conditions.append(SogcAuditor.auditor_location.ilike(f"%{location}%"))
    if legal_form is not None:
        conditions.append(SogcAuditor.auditor_legal_form.ilike(f"%{legal_form}%"))
    if is_current is True:
        # treat NULL as current (historical data predates the is_current field being set)
        conditions.append(or_(SogcAuditor.is_current == True, SogcAuditor.is_current.is_(None)))
    elif is_current is False:
        conditions.append(SogcAuditor.is_current == False)

    rows = (
        db.query(SogcAuditor, CompanyModel.id, CompanyModel.name)
        .outerjoin(CompanyModel, SogcAuditor.company_uid == CompanyModel.uid)
        .filter(*conditions)
        .order_by(SogcAuditor.pub_date.desc())
        .offset(offset).limit(limit)
        .all()
    )
    return [AuditorOut.from_orm(a, company_id=cid, company_name=cname) for a, cid, cname in rows]


@router.get("/sogc/auditors/by-uid/{auditor_uid}", response_model=list[AuditorOut])
def get_auditor_clients(
    auditor_uid: str,
    is_current: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_auditor import SogcAuditor
    from app.models.company import Company as CompanyModel

    from sqlalchemy import or_

    conditions = [SogcAuditor.auditor_uid == auditor_uid]
    if is_current is True:
        conditions.append(or_(SogcAuditor.is_current == True, SogcAuditor.is_current.is_(None)))
    elif is_current is False:
        conditions.append(SogcAuditor.is_current == False)

    rows = (
        db.query(SogcAuditor, CompanyModel.id, CompanyModel.name)
        .outerjoin(CompanyModel, SogcAuditor.company_uid == CompanyModel.uid)
        .filter(*conditions)
        .order_by(SogcAuditor.pub_date.desc())
        .all()
    )
    return [AuditorOut.from_orm(a, company_id=cid, company_name=cname) for a, cid, cname in rows]


# ── Company-scoped endpoints ───────────────────────────────────────────────────

@router.get("/companies/{company_id}/persons", response_model=list[PersonAppearanceOut])
def get_company_persons(
    company_id: int,
    is_current: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_person_appearance import SogcPersonAppearance

    qry = db.query(SogcPersonAppearance).filter(SogcPersonAppearance.company_id == company_id)
    if is_current is not None:
        qry = qry.filter(SogcPersonAppearance.is_current == is_current)
    appearances = qry.order_by(SogcPersonAppearance.pub_date.desc()).all()
    return [PersonAppearanceOut.from_orm(a) for a in appearances]


@router.get("/companies/{company_id}/auditors", response_model=list[AuditorOut])
def get_company_auditors(
    company_id: int,
    is_current: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_auditor import SogcAuditor

    qry = db.query(SogcAuditor).filter(SogcAuditor.company_id == company_id)
    if is_current is not None:
        qry = qry.filter(SogcAuditor.is_current == is_current)
    return [AuditorOut.from_orm(a) for a in qry.order_by(SogcAuditor.pub_date.desc()).all()]


@router.get("/companies/{company_id}/publications", response_model=list[SogcPublicationOut])
def get_company_publications(
    company_id: int,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_publication import SogcPublication
    from sqlalchemy.orm import joinedload

    pubs = (
        db.query(SogcPublication)
        .filter(SogcPublication.company_id == company_id)
        .options(joinedload(SogcPublication.changes))
        .order_by(SogcPublication.pub_date.desc())
        .limit(limit)
        .all()
    )
    return [SogcPublicationOut.from_orm(p) for p in pubs]


# ── SOGC global search ─────────────────────────────────────────────────────────

@router.get("/sogc/publications/search", response_model=list[SogcPublicationOut])
def search_publications(
    q: str | None = Query(None, description="Text search in pub content, sogc_id, or pub_number"),
    company_uid: str | None = Query(None),
    sub_rubric: str | None = Query(None),
    change_type: str | None = Query(None, description="Filter by change type in linked changes"),
    date_from: str | None = Query(None, description="YYYY-MM-DD inclusive lower bound"),
    date_to: str | None = Query(None, description="YYYY-MM-DD inclusive upper bound"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_publication import SogcPublication
    from app.models.sogc_change import SogcChange
    from sqlalchemy.orm import joinedload
    from sqlalchemy import func, or_

    qry = db.query(SogcPublication).options(joinedload(SogcPublication.changes))

    if q:
        term = f"%{q}%"
        qry = qry.filter(
            or_(
                func.lower(SogcPublication.text_de).like(term.lower()),
                func.lower(SogcPublication.text_fr).like(term.lower()),
                func.lower(SogcPublication.text_it).like(term.lower()),
                SogcPublication.sogc_id.like(term),
                SogcPublication.pub_number.like(term),
            )
        )
    if company_uid:
        qry = qry.filter(SogcPublication.company_uid == company_uid)
    if sub_rubric:
        qry = qry.filter(SogcPublication.sub_rubric == sub_rubric)
    if change_type:
        qry = qry.filter(
            SogcPublication.changes.any(SogcChange.change_type == change_type)
        )
    if date_from:
        qry = qry.filter(SogcPublication.pub_date >= date_from)
    if date_to:
        qry = qry.filter(SogcPublication.pub_date <= date_to)

    pubs = qry.order_by(SogcPublication.pub_date.desc()).offset(offset).limit(limit).all()
    return [SogcPublicationOut.from_orm(p) for p in pubs]


# ── Corporate entity endpoints ─────────────────────────────────────────────────

@router.get("/sogc/corporate-roles/search", response_model=list[CorporateRoleOut])
def search_corporate_roles(
    q: str | None = Query(None, description="Partial match on entity name"),
    che: str | None = Query(None, description="Exact CHE number, e.g. CHE-467.225.008"),
    company_uid: str | None = Query(None),
    is_current: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_corporate_role import SogcCorporateRole
    from app.models.company import Company as CompanyModel
    from sqlalchemy import func

    qry = db.query(SogcCorporateRole)

    if q:
        qry = qry.filter(SogcCorporateRole.entity_name_normalized.like(f"%{q.lower()}%"))
    if che:
        qry = qry.filter(SogcCorporateRole.entity_che == che)
    if company_uid:
        qry = qry.filter(SogcCorporateRole.company_uid == company_uid)
    if is_current is not None:
        qry = qry.filter(SogcCorporateRole.is_current == is_current)

    rows = (
        qry.outerjoin(CompanyModel, SogcCorporateRole.company_uid == CompanyModel.uid)
        .add_columns(CompanyModel.id, CompanyModel.name)
        .order_by(SogcCorporateRole.pub_date.desc())
        .offset(offset).limit(limit)
        .all()
    )
    result = []
    for row in rows:
        role, cid, cname = row
        out = CorporateRoleOut.from_orm(role)
        out = out.model_copy(update={"company_id": cid, "company_name": cname})
        result.append(out)
    return result


@router.get("/companies/{company_id}/corporate-roles", response_model=list[CorporateRoleOut])
def get_company_corporate_roles(
    company_id: int,
    is_current: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_corporate_role import SogcCorporateRole

    qry = db.query(SogcCorporateRole).filter(SogcCorporateRole.company_id == company_id)
    if is_current is not None:
        qry = qry.filter(SogcCorporateRole.is_current == is_current)
    roles = qry.order_by(SogcCorporateRole.pub_date.desc()).all()
    return [CorporateRoleOut.from_orm(r) for r in roles]
