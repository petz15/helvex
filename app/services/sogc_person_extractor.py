"""Extract structured person and auditor records from sogc_changes raw excerpts.

Person format (DE): Lastname, Firstname [Title], von [Hometown], in [Residence], [Role], [Signature] [bisher: ...]
Person format (FR): Nom, Prénom [Titre], de [Ville d'origine], à [Résidence], [Fonction], [Signature] [anciennement: ...]
Foreign national:   Lastname, Firstname, [adjective] Staatsangehöriger, in [Residence], [Role], [Signature]

Auditor format:     Firm Name AG, in [City], CHE-xxx.xxx.xxx, Legal Form
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Callable

from sqlalchemy import distinct
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Regex patterns ─────────────────────────────────────────────────────────────

_BISHER_RE = re.compile(r"\[bisher:\s*([^\]]+)\]", re.I)
_ANCIENNEMENT_RE = re.compile(r"\[anciennement:\s*([^\]]+)\]", re.I)
_PRECEDEMMENT_RE = re.compile(r"\[précédemment:\s*([^\]]+)\]", re.I)

_TITLE_RE = re.compile(r"\b(Dr\.|Prof\.|lic\.|dipl\.|Ing\.|MLaw|MAS|MBA|BSc|MSc)\b", re.I)

# German origin "von Zürich" / "von Eriswil und Roggwil BE"
_DE_VON_RE = re.compile(r"^von\s+(.+)$", re.I)
# French origin "de Genève" / "de la Chaux-de-Fonds"
_FR_DE_RE = re.compile(r"^de\s+(?:la\s+|l[e']\s+)?(.+)$", re.I)
# Italian origin "di Lugano"
_IT_DI_RE = re.compile(r"^di\s+(.+)$", re.I)
# Residence "in Bern" / "à Lausanne" / "a Lugano"
_RESIDENCE_DE_RE = re.compile(r"^in\s+(.+)$", re.I)
_RESIDENCE_FR_RE = re.compile(r"^[àa]\s+(.+)$", re.I)

# Foreign national: "deutscher Staatsangehöriger" / "französische Staatsangehörige"
_FOREIGN_DE_RE = re.compile(r"^([\w\-]+(?:er|e|ische?r?))\s+staatsangehörige?r?$", re.I)
_FOREIGN_FR_RE = re.compile(r"^ressortissant\s+(.+)$", re.I)
_FOREIGN_IT_RE = re.compile(r"^cittadin[oa]\s+(.+)$", re.I)

# Signature types
_SIGNATURE_RE = re.compile(r"^mit\s+.*(unterschrift|signatur)", re.I)
_SIGNATURE_FR_RE = re.compile(r"^avec\s+.*(signature)", re.I)
_SIGNATURE_IT_RE = re.compile(r"^con\s+.*(firma)", re.I)

# Auditor UID
_CHE_UID_RE = re.compile(r"CHE-\d{3}\.\d{3}\.\d{3}")

_LEGAL_FORMS = {
    "aktiengesellschaft", "gesellschaft mit beschränkter haftung", "gmbh",
    "genossenschaft", "verein", "stiftung", "kollektivgesellschaft",
    "kommanditgesellschaft", "einzelunternehmen",
    "société anonyme", "sa", "sàrl", "société à responsabilité limitée",
    "société coopérative", "association", "fondation",
    "società anonima", "società a garanzia limitata", "sagl",
    "società cooperativa", "associazione", "fondazione",
}

_ROLE_DIRECTOR_KEYWORDS = {
    "verwaltungsrat", "verwaltungsrätin", "mitglied des verwaltungsrates",
    "mitglied des vr", "präsident des verwaltungsrates", "vizepräsident",
    "administrateur", "administratrice", "président du conseil",
    "vice-président du conseil", "membre du conseil",
    "consigliere", "presidente del consiglio", "membro del consiglio",
    "vorsitzende", "vorsitzender",
}

_ROLE_OFFICER_KEYWORDS = {
    "geschäftsführer", "geschäftsführerin", "mitglied der geschäftsleitung",
    "direktor", "direktorin", "ceo", "prokura", "prokurist", "prokuristin",
    "directeur général", "directrice générale", "fondé de procuration",
    "fondée de procuration", "directeur", "directrice",
    "direttore", "direttrice", "gerente",
}

# Parts that signal start of non-name fields
_NON_NAME_PREFIXES = re.compile(
    r"^(von|de|di|in|[àa]|mit|avec|con|staatsangehörig|ressortissant|cittadin|"
    r"verwaltungsrat|mitglied|präsident|geschäftsführer|direktor|prokur|"
    r"administrateur|président|consigliere|direttore|"
    r"revisionsstelle|réviseur|organe de|organo di|société de révision|"
    r"società di|ufficio di)\b",
    re.I,
)


# ── Normalisation ──────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """NFKD-lowercase, strip combining chars, collapse whitespace."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def _normalize_key(lastname: str, firstname: str, hometown: str) -> str:
    return f"{_normalize(lastname)}|{_normalize(firstname)}|{_normalize(hometown)}"


# ── Role classification ────────────────────────────────────────────────────────

def _classify_role(role: str | None) -> str:
    if not role:
        return "other"
    r = role.lower()
    for kw in _ROLE_DIRECTOR_KEYWORDS:
        if kw in r:
            return "director"
    for kw in _ROLE_OFFICER_KEYWORDS:
        if kw in r:
            return "officer"
    return "other"


# ── Person parsing ─────────────────────────────────────────────────────────────

def _parse_bisher_fields(bisher_text: str | None) -> dict:
    """Parse structured fields from a [bisher: ...] annotation fragment.

    Bisher text can be a partial person description, e.g.:
      "rumänische Staatsangehörige, in Ittigen"   → residence + foreign flag
      "Müller, Hans, in Zürich"                   → name + residence
      "in Bern"                                   → residence only
      "Prokurist, mit Einzelunterschrift"         → role only (yields nothing useful)

    Returns a dict with zero or more of:
      bisher_residence_municipality, bisher_lastname, bisher_firstname,
      bisher_is_foreign, bisher_nationality
    """
    if not bisher_text:
        return {}

    result: dict = {}
    parts = [p.strip() for p in bisher_text.split(",") if p.strip()]
    name_candidates: list[str] = []

    for p in parts:
        # Residence: "in X", "à X", "a X"
        m_res = _RESIDENCE_DE_RE.match(p) or _RESIDENCE_FR_RE.match(p)
        if m_res and "bisher_residence_municipality" not in result:
            result["bisher_residence_municipality"] = m_res.group(1).strip()[:256]
            continue

        # Foreign national
        m_foreign = (
            _FOREIGN_DE_RE.match(p)
            or _FOREIGN_FR_RE.match(p)
            or _FOREIGN_IT_RE.match(p)
        )
        if m_foreign:
            result["bisher_is_foreign"] = True
            nat = m_foreign.group(1)
            # Strip gender suffixes from DE adjective form (e.g. "rumänische" → "rumänisch")
            nat = re.sub(r"(ische[nr]?|ischen)$", "", nat, flags=re.I).strip()
            result["bisher_nationality"] = nat[:128]
            continue

        # Skip known non-name field starters (origin, role prefixes, etc.)
        if _NON_NAME_PREFIXES.match(p):
            continue

        # Also skip signature / title tokens
        if _SIGNATURE_RE.match(p) or _SIGNATURE_FR_RE.match(p) or _SIGNATURE_IT_RE.match(p):
            continue
        if _TITLE_RE.fullmatch(p):
            continue

        name_candidates.append(p)

    # Up to two leftover parts are treated as lastname [firstname]
    if name_candidates:
        result["bisher_lastname"] = name_candidates[0][:256]
        if len(name_candidates) >= 2:
            result["bisher_firstname"] = name_candidates[1][:256]

    return result


def _strip_bisher(text: str) -> tuple[str, str | None]:
    """Remove [bisher: ...] / [anciennement: ...] annotations, return (clean, bisher_text)."""
    bisher = None
    for pattern in (_BISHER_RE, _ANCIENNEMENT_RE, _PRECEDEMMENT_RE):
        m = pattern.search(text)
        if m:
            bisher = m.group(1).strip()
            text = pattern.sub("", text).strip()
            break
    return text, bisher


def _parse_person(raw_excerpt: str, change_type: str) -> dict | None:
    """Parse a single-person raw_excerpt into structured fields.

    Returns None if the text is too short or clearly not a person entry.
    """
    if not raw_excerpt or len(raw_excerpt.strip()) < 4:
        return None

    text, bisher_role = _strip_bisher(raw_excerpt.strip().rstrip("."))
    bisher_parsed = _parse_bisher_fields(bisher_role)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None

    lastname = parts[0]

    # Extract inline title from lastname or check second part
    title: str | None = None
    title_m = _TITLE_RE.search(lastname)
    if title_m:
        title = title_m.group(0)
        lastname = _TITLE_RE.sub("", lastname).strip()

    # Second part: firstname if it doesn't look like origin/role/signature
    firstname = ""
    idx = 1
    if len(parts) > 1 and not _NON_NAME_PREFIXES.match(parts[1]):
        firstname = parts[1]
        title_m2 = _TITLE_RE.search(firstname)
        if title_m2:
            if not title:
                title = title_m2.group(0)
            firstname = _TITLE_RE.sub("", firstname).strip()
        idx = 2

    hometown: str = ""
    residence: str = ""
    is_foreign: bool = False
    nationality: str | None = None
    role_parts: list[str] = []
    signature_type: str | None = None

    for part in parts[idx:]:
        p = part.strip()
        if not p:
            continue

        # Origin: "von X", "de X", "di X"
        m_von = _DE_VON_RE.match(p)
        m_de = _FR_DE_RE.match(p)
        m_di = _IT_DI_RE.match(p)
        if m_von and not hometown:
            hometown = m_von.group(1).strip()
            continue
        if m_de and not hometown:
            hometown = m_de.group(1).strip()
            continue
        if m_di and not hometown:
            hometown = m_di.group(1).strip()
            continue

        # Residence: "in X", "à X", "a X"
        m_res_de = _RESIDENCE_DE_RE.match(p)
        m_res_fr = _RESIDENCE_FR_RE.match(p)
        if m_res_de and not residence:
            residence = m_res_de.group(1).strip()
            continue
        if m_res_fr and not residence:
            residence = m_res_fr.group(1).strip()
            continue

        # Foreign national
        m_foreign_de = _FOREIGN_DE_RE.match(p)
        m_foreign_fr = _FOREIGN_FR_RE.match(p)
        m_foreign_it = _FOREIGN_IT_RE.match(p)
        if m_foreign_de:
            is_foreign = True
            nationality = m_foreign_de.group(1).rstrip("er").rstrip("e").strip()
            continue
        if m_foreign_fr:
            is_foreign = True
            nationality = m_foreign_fr.group(1).strip()
            continue
        if m_foreign_it:
            is_foreign = True
            nationality = m_foreign_it.group(1).strip()
            continue

        # Signature
        if _SIGNATURE_RE.match(p) or _SIGNATURE_FR_RE.match(p) or _SIGNATURE_IT_RE.match(p):
            signature_type = p[:128]
            continue

        role_parts.append(p)

    role = ", ".join(role_parts)[:256] if role_parts else None

    is_current: bool | None
    if change_type == "person_removed":
        is_current = False
    else:
        # person_added and all other change types (aenderungorgane, etc.) → still present
        is_current = True

    normalized_key = _normalize_key(lastname, firstname, hometown)

    return {
        "lastname": lastname[:256] if lastname else None,
        "firstname": firstname[:256] if firstname else None,
        "title": title[:64] if title else None,
        "hometown_municipality": hometown[:256] if hometown else None,
        "residence_municipality": residence[:256] if residence else None,
        "is_foreign": is_foreign,
        "nationality": nationality[:128] if nationality else None,
        "role": role,
        "role_category": _classify_role(role),
        "signature_type": signature_type,
        "bisher_role": bisher_role[:256] if bisher_role else None,
        "bisher_residence_municipality": bisher_parsed.get("bisher_residence_municipality"),
        "bisher_lastname": bisher_parsed.get("bisher_lastname"),
        "bisher_firstname": bisher_parsed.get("bisher_firstname"),
        "bisher_is_foreign": bisher_parsed.get("bisher_is_foreign"),
        "bisher_nationality": bisher_parsed.get("bisher_nationality"),
        "is_current": is_current,
        "normalized_key": normalized_key,
    }


# ── Auditor parsing ────────────────────────────────────────────────────────────

def _parse_auditor(raw_excerpt: str, change_type: str) -> dict | None:
    """Parse an auditor (legal entity) raw_excerpt."""
    if not raw_excerpt or len(raw_excerpt.strip()) < 4:
        return None

    text = _BISHER_RE.sub("", raw_excerpt).strip().rstrip(".")
    # Strip SHAB reference "(SHAB Nr. X ...)"
    text = re.sub(r"\(SHAB[^)]+\)", "", text).strip()

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None

    auditor_name = parts[0]
    auditor_uid: str | None = None
    auditor_location: str | None = None
    auditor_legal_form: str | None = None

    for part in parts[1:]:
        p = part.strip()
        uid_m = _CHE_UID_RE.search(p)
        if uid_m:
            auditor_uid = uid_m.group(0)
            continue
        res_m = _RESIDENCE_DE_RE.match(p) or _RESIDENCE_FR_RE.match(p)
        if res_m and not auditor_location:
            auditor_location = res_m.group(1).strip()[:256]
            continue
        if p.lower() in _LEGAL_FORMS and not auditor_legal_form:
            auditor_legal_form = p[:128]
            continue
        # Fallback: check if part IS a legal form suffix on auditor_name (e.g. "AG" as standalone part)
        if p.upper() in {"AG", "SA", "SAGL", "SÀRL", "GMBH"} and not auditor_legal_form:
            auditor_legal_form = p[:128]
            continue

    is_current: bool | None
    if change_type in ("person_added", "auditor_change"):
        is_current = True
    elif change_type == "person_removed":
        is_current = False
    else:
        is_current = None

    return {
        "auditor_name": auditor_name[:512] if auditor_name else None,
        "auditor_uid": auditor_uid,
        "auditor_legal_form": auditor_legal_form,
        "auditor_location": auditor_location,
        "auditor_name_normalized": _normalize(auditor_name)[:512] if auditor_name else None,
        "is_current": is_current,
    }


# ── Entity upsert ──────────────────────────────────────────────────────────────

def _get_or_create_entity(db: Session, key: str, fields: dict):
    from app.models.sogc_person_entity import SogcPersonEntity

    entity = db.query(SogcPersonEntity).filter_by(normalized_key=key).first()
    if not entity:
        entity = SogcPersonEntity(
            normalized_key=key,
            lastname=fields.get("lastname"),
            firstname=fields.get("firstname"),
            hometown_municipality=fields.get("hometown_municipality"),
            is_foreign=fields.get("is_foreign", False),
            nationality=fields.get("nationality"),
            confidence_level="medium",
        )
        db.add(entity)
        db.flush()
    return entity


def _recompute_entity_confidence(db: Session, entity_id: int) -> None:
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from sqlalchemy import or_

    entity = db.get(SogcPersonEntity, entity_id)
    if entity is None:
        return

    # Any parsed bisher field is a hard link — confirms at least one mutation
    # was explicitly linked, so confidence is elevated.
    has_hard_link = (
        db.query(SogcPersonAppearance.id)
        .filter(
            SogcPersonAppearance.person_entity_id == entity_id,
            or_(
                SogcPersonAppearance.bisher_residence_municipality.isnot(None),
                SogcPersonAppearance.bisher_lastname.isnot(None),
            ),
        )
        .first() is not None
    )

    if has_hard_link:
        entity.confidence_level = "high"
    elif entity.is_foreign or not entity.hometown_municipality:
        entity.confidence_level = "low"
    elif entity.appearance_count <= 1:
        entity.confidence_level = "medium"
    else:
        residences = (
            db.query(distinct(SogcPersonAppearance.residence_municipality))
            .filter(
                SogcPersonAppearance.person_entity_id == entity_id,
                SogcPersonAppearance.residence_municipality.isnot(None),
                SogcPersonAppearance.residence_municipality != "",
            )
            .all()
        )
        entity.confidence_level = "high" if len(residences) <= 1 else "medium"
    db.flush()


def _update_entity_counts(db: Session, entity_ids: set[int]) -> None:
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from sqlalchemy import func

    for eid in entity_ids:
        total = (
            db.query(func.count(SogcPersonAppearance.id))
            .filter_by(person_entity_id=eid)
            .scalar() or 0
        )
        active = (
            db.query(func.count(SogcPersonAppearance.id))
            .filter(
                SogcPersonAppearance.person_entity_id == eid,
                SogcPersonAppearance.is_current.is_(True),
            )
            .scalar() or 0
        )
        entity = db.get(SogcPersonEntity, eid)
        if entity:
            entity.appearance_count = total
            entity.active_company_count = active
    db.flush()


# ── Per-publication incremental extraction ────────────────────────────────────

def extract_persons_for_publication(db: Session, publication) -> dict:
    """Delete and re-insert sogc_person_appearances and sogc_auditors for one publication.

    Entity rows (sogc_person_entities) are never deleted — they persist across runs.
    Updates entity counts and confidence after insertion.
    Returns {"persons_written": int, "auditors_written": int}.
    """
    from app.models.sogc_change import SogcChange
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from app.models.sogc_auditor import SogcAuditor

    PERSON_TYPES = {"person_added", "person_removed", "person_changed"}
    AUDITOR_TYPE = "auditor_change"

    # Delete existing rows for this publication (appearances cascade-deleted when
    # sogc_changes are wiped, but we also delete explicitly for the publications mode)
    db.query(SogcPersonAppearance).filter_by(sogc_publication_id=publication.id).delete()
    db.query(SogcAuditor).filter_by(sogc_publication_id=publication.id).delete()

    changes = (
        db.query(SogcChange)
        .filter(
            SogcChange.sogc_publication_id == publication.id,
            SogcChange.change_type.in_(PERSON_TYPES | {AUDITOR_TYPE}),
        )
        .all()
    )

    persons_written = 0
    auditors_written = 0
    touched_entity_ids: set[int] = set()

    for change in changes:
        if not change.raw_excerpt:
            continue

        try:
            if change.change_type in PERSON_TYPES:
                fields = _parse_person(change.raw_excerpt, change.change_type)
                if fields is None:
                    continue
                entity = _get_or_create_entity(db, fields["normalized_key"], fields)
                touched_entity_ids.add(entity.id)
                db.add(SogcPersonAppearance(
                    person_entity_id=entity.id,
                    sogc_change_id=change.id,
                    sogc_publication_id=publication.id,
                    company_uid=publication.company_uid,
                    pub_date=publication.pub_date,
                    change_type=change.change_type,
                    role=fields["role"],
                    role_category=fields["role_category"],
                    signature_type=fields["signature_type"],
                    bisher_role=fields["bisher_role"],
                    bisher_residence_municipality=fields.get("bisher_residence_municipality"),
                    bisher_lastname=fields.get("bisher_lastname"),
                    bisher_firstname=fields.get("bisher_firstname"),
                    bisher_is_foreign=fields.get("bisher_is_foreign"),
                    bisher_nationality=fields.get("bisher_nationality"),
                    residence_municipality=fields["residence_municipality"],
                    is_current=fields["is_current"],
                    title=fields["title"],
                    raw_excerpt=change.raw_excerpt,
                ))
                persons_written += 1

            elif change.change_type == AUDITOR_TYPE:
                fields = _parse_auditor(change.raw_excerpt, change.change_type)
                if fields is None:
                    continue
                db.add(SogcAuditor(
                    sogc_change_id=change.id,
                    sogc_publication_id=publication.id,
                    company_uid=publication.company_uid,
                    pub_date=publication.pub_date,
                    change_type=change.change_type,
                    **fields,
                ))
                auditors_written += 1

        except Exception:
            logger.warning(
                "Person extraction failed for change_id=%s pub_id=%s",
                change.id, publication.id, exc_info=True,
            )

    if touched_entity_ids:
        _update_entity_counts(db, touched_entity_ids)
        for eid in touched_entity_ids:
            _recompute_entity_confidence(db, eid)

    return {"persons_written": persons_written, "auditors_written": auditors_written}


# ── Bulk backfill ──────────────────────────────────────────────────────────────

def run_extract_sogc_persons_batch(
    db: Session,
    *,
    mode: str = "missing",
    batch_size: int = 1000,
    resume_from: int = 0,
    progress_cb: Callable | None = None,
    status_cb: Callable | None = None,
    abort_cb: Callable | None = None,
) -> dict:
    """Bulk extraction of persons/auditors from sogc_changes.

    mode='missing': only changes not yet in sogc_person_appearances or sogc_auditors.
    mode='all':     reprocess all person/auditor type changes.
    Cursor-based pagination on sogc_change.id for resume support.
    """
    from sqlalchemy import exists
    from app.models.sogc_change import SogcChange
    from app.models.sogc_publication import SogcPublication
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from app.models.sogc_auditor import SogcAuditor

    PERSON_TYPES = {"person_added", "person_removed", "person_changed"}
    ALL_TYPES = list(PERSON_TYPES | {"auditor_change"})

    stats: dict = {
        "selected": 0,
        "processed": 0,
        "persons_written": 0,
        "auditors_written": 0,
        "skipped_no_excerpt": 0,
        "errors": [],
    }

    q = (
        db.query(SogcChange)
        .filter(SogcChange.change_type.in_(ALL_TYPES))
        .order_by(SogcChange.id.asc())
    )

    if mode == "missing":
        already_person = db.query(SogcPersonAppearance.sogc_change_id).distinct().subquery()
        already_auditor = db.query(SogcAuditor.sogc_change_id).distinct().subquery()
        q = q.filter(
            ~exists().where(already_person.c.sogc_change_id == SogcChange.id),
            ~exists().where(already_auditor.c.sogc_change_id == SogcChange.id),
        )

    total = q.count()
    stats["selected"] = total

    if status_cb:
        status_cb(f"Extracting persons/auditors: {total} changes to process (mode={mode})")

    last_id = resume_from
    done = 0

    while True:
        if abort_cb:
            abort_cb()

        batch: list[SogcChange] = q.filter(SogcChange.id > last_id).limit(batch_size).all()
        if not batch:
            break

        # Pre-fetch publications in bulk
        pub_ids = list({c.sogc_publication_id for c in batch})
        pubs = {
            p.id: p
            for p in db.query(SogcPublication).filter(SogcPublication.id.in_(pub_ids)).all()
        }

        touched_entity_ids: set[int] = set()

        for change in batch:
            if abort_cb:
                abort_cb()

            if not change.raw_excerpt:
                stats["skipped_no_excerpt"] += 1
                continue

            pub = pubs.get(change.sogc_publication_id)

            try:
                if mode == "all":
                    from app.models.sogc_person_appearance import SogcPersonAppearance as SPA
                    from app.models.sogc_auditor import SogcAuditor as SA
                    db.query(SPA).filter_by(sogc_change_id=change.id).delete()
                    db.query(SA).filter_by(sogc_change_id=change.id).delete()

                if change.change_type in PERSON_TYPES:
                    fields = _parse_person(change.raw_excerpt, change.change_type)
                    if fields is None:
                        stats["skipped_no_excerpt"] += 1
                        continue
                    entity = _get_or_create_entity(db, fields["normalized_key"], fields)
                    touched_entity_ids.add(entity.id)
                    from app.models.sogc_person_appearance import SogcPersonAppearance
                    db.add(SogcPersonAppearance(
                        person_entity_id=entity.id,
                        sogc_change_id=change.id,
                        sogc_publication_id=change.sogc_publication_id,
                        company_uid=pub.company_uid if pub else None,
                        pub_date=pub.pub_date if pub else None,
                        change_type=change.change_type,
                        role=fields["role"],
                        role_category=fields["role_category"],
                        signature_type=fields["signature_type"],
                        bisher_role=fields["bisher_role"],
                        bisher_residence_municipality=fields.get("bisher_residence_municipality"),
                        bisher_lastname=fields.get("bisher_lastname"),
                        bisher_firstname=fields.get("bisher_firstname"),
                        bisher_is_foreign=fields.get("bisher_is_foreign"),
                        bisher_nationality=fields.get("bisher_nationality"),
                        residence_municipality=fields["residence_municipality"],
                        is_current=fields["is_current"],
                        title=fields["title"],
                        raw_excerpt=change.raw_excerpt,
                    ))
                    stats["persons_written"] += 1

                elif change.change_type == "auditor_change":
                    fields = _parse_auditor(change.raw_excerpt, change.change_type)
                    if fields is None:
                        stats["skipped_no_excerpt"] += 1
                        continue
                    from app.models.sogc_auditor import SogcAuditor
                    db.add(SogcAuditor(
                        sogc_change_id=change.id,
                        sogc_publication_id=change.sogc_publication_id,
                        company_uid=pub.company_uid if pub else None,
                        pub_date=pub.pub_date if pub else None,
                        change_type=change.change_type,
                        **fields,
                    ))
                    stats["auditors_written"] += 1

                stats["processed"] += 1

            except Exception as exc:
                stats["errors"].append(f"change_id={change.id}: {type(exc).__name__}: {exc}")
                try:
                    db.rollback()
                except Exception:
                    pass

        # Update entity counts for this batch
        if touched_entity_ids:
            _update_entity_counts(db, touched_entity_ids)
            for eid in touched_entity_ids:
                _recompute_entity_confidence(db, eid)

        db.commit()
        last_id = batch[-1].id
        done += len(batch)

        if progress_cb:
            progress_cb(done, total, stats)

    return stats
