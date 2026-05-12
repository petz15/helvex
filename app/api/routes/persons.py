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
    def from_orm(cls, e, residence: str | None = None) -> "PersonEntityOut":
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
            active_company_count=e.active_company_count,
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
    pub_date: str | None
    change_type: str
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
    def from_orm(cls, a) -> "PersonAppearanceOut":
        return cls(
            id=a.id,
            person_entity_id=a.person_entity_id,
            entity_override_id=a.entity_override_id,
            sogc_change_id=a.sogc_change_id,
            sogc_publication_id=a.sogc_publication_id,
            company_uid=a.company_uid,
            pub_date=a.pub_date,
            change_type=a.change_type,
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
    def from_orm(cls, a) -> "AuditorOut":
        return cls(
            id=a.id,
            sogc_change_id=a.sogc_change_id,
            sogc_publication_id=a.sogc_publication_id,
            company_uid=a.company_uid,
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
    keywords_matched: str | None
    raw_excerpt: str | None

    @classmethod
    def from_orm(cls, c) -> "SogcChangeOut":
        return cls(
            id=c.id,
            change_type=c.change_type,
            keywords_matched=c.keywords_matched,
            raw_excerpt=c.raw_excerpt,
        )


class SogcPublicationOut(BaseModel):
    id: int
    sogc_id: str
    company_uid: str | None
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


# ── Person entity endpoints ────────────────────────────────────────────────────

@router.get("/sogc/persons/search", response_model=list[PersonEntityOut])
def search_persons(
    q: str | None = Query(None, description="Partial match on lastname or firstname"),
    hometown: str | None = Query(None),
    confidence_level: str | None = Query(None),
    is_verified: bool | None = Query(None),
    is_current: bool | None = Query(None, description="Filter entities with at least one current appearance"),
    sort_by: str | None = Query(None, description="Sort order: 'companies' (default) or 'confidence'"),
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
    if is_verified is not None:
        qry = qry.filter(SogcPersonEntity.is_verified == is_verified)
    if is_current is True:
        qry = qry.filter(SogcPersonEntity.active_company_count > 0)

    if sort_by == "confidence":
        conf_order = case(
            (SogcPersonEntity.confidence_level == "high", 0),
            (SogcPersonEntity.confidence_level == "medium", 1),
            (SogcPersonEntity.confidence_level == "low", 2),
            else_=3,
        )
        entities = qry.order_by(conf_order, SogcPersonEntity.active_company_count.desc()).offset(offset).limit(limit).all()
    else:
        entities = qry.order_by(SogcPersonEntity.active_company_count.desc(), SogcPersonEntity.id.desc()).offset(offset).limit(limit).all()

    # Batch-fetch most recent current residence for each entity (avoids N+1)
    entity_ids = [e.id for e in entities]
    residence_map: dict[int, str] = {}
    if entity_ids:
        rows = (
            db.query(SogcPersonAppearance.person_entity_id, SogcPersonAppearance.residence_municipality)
            .filter(
                SogcPersonAppearance.person_entity_id.in_(entity_ids),
                SogcPersonAppearance.is_current == True,
                SogcPersonAppearance.residence_municipality.isnot(None),
            )
            .order_by(SogcPersonAppearance.person_entity_id, SogcPersonAppearance.pub_date.desc())
            .all()
        )
        for pe_id, res in rows:
            if pe_id not in residence_map:
                residence_map[pe_id] = res

    return [PersonEntityOut.from_orm(e, residence_map.get(e.id)) for e in entities]


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

    entity = db.get(SogcPersonEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Person entity not found")
    # Follow merge chain
    if entity.merged_into_id:
        canonical = db.get(SogcPersonEntity, entity.merged_into_id)
        if canonical:
            entity = canonical
    return PersonEntityOut.from_orm(entity)


@router.get("/sogc/persons/{entity_id}/appearances", response_model=list[PersonAppearanceOut])
def get_person_appearances(
    entity_id: int,
    is_current: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_person_appearance import SogcPersonAppearance

    qry = db.query(SogcPersonAppearance).filter_by(person_entity_id=entity_id)
    if is_current is not None:
        qry = qry.filter(SogcPersonAppearance.is_current == is_current)
    appearances = qry.order_by(SogcPersonAppearance.pub_date.desc()).limit(limit).all()
    return [PersonAppearanceOut.from_orm(a) for a in appearances]


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
    is_current: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_auditor import SogcAuditor
    from sqlalchemy import func

    qry = db.query(SogcAuditor)
    if q:
        qry = qry.filter(SogcAuditor.auditor_name_normalized.like(f"%{q.lower()}%"))
    if is_current is not None:
        qry = qry.filter(SogcAuditor.is_current == is_current)
    auditors = qry.order_by(SogcAuditor.pub_date.desc()).offset(offset).limit(limit).all()
    return [AuditorOut.from_orm(a) for a in auditors]


@router.get("/sogc/auditors/by-uid/{auditor_uid}", response_model=list[AuditorOut])
def get_auditor_clients(
    auditor_uid: str,
    is_current: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_auditor import SogcAuditor

    qry = db.query(SogcAuditor).filter_by(auditor_uid=auditor_uid)
    if is_current is not None:
        qry = qry.filter(SogcAuditor.is_current == is_current)
    return [AuditorOut.from_orm(a) for a in qry.order_by(SogcAuditor.pub_date.desc()).all()]


# ── Company-scoped endpoints ───────────────────────────────────────────────────

@router.get("/companies/{company_uid}/persons", response_model=list[PersonAppearanceOut])
def get_company_persons(
    company_uid: str,
    is_current: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_person_appearance import SogcPersonAppearance

    qry = db.query(SogcPersonAppearance).filter_by(company_uid=company_uid)
    if is_current is not None:
        qry = qry.filter(SogcPersonAppearance.is_current == is_current)
    appearances = qry.order_by(SogcPersonAppearance.pub_date.desc()).all()
    return [PersonAppearanceOut.from_orm(a) for a in appearances]


@router.get("/companies/{company_uid}/auditors", response_model=list[AuditorOut])
def get_company_auditors(
    company_uid: str,
    is_current: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_auditor import SogcAuditor

    qry = db.query(SogcAuditor).filter_by(company_uid=company_uid)
    if is_current is not None:
        qry = qry.filter(SogcAuditor.is_current == is_current)
    return [AuditorOut.from_orm(a) for a in qry.order_by(SogcAuditor.pub_date.desc()).all()]


@router.get("/companies/{company_uid}/publications", response_model=list[SogcPublicationOut])
def get_company_publications(
    company_uid: str,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.sogc_publication import SogcPublication
    from sqlalchemy.orm import joinedload

    pubs = (
        db.query(SogcPublication)
        .filter_by(company_uid=company_uid)
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
