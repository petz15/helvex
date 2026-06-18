from sqlalchemy.orm import Session

from app.models.google_directory_domain import GoogleDirectoryDomain
from app.models.google_stopword import GoogleStopword
from app.models.tfidf_stopword import TfidfStopword


def seed_default_google_stopwords(db: Session) -> int:
    """Insert all built-in Google stopwords into the DB, skipping existing ones.

    Returns number of rows actually inserted.
    """
    from app.services.scoring import _STOPWORDS  # import here to avoid circular
    inserted = 0
    for value in _STOPWORDS:
        v = value.strip().lower()
        if not v:
            continue
        exists = db.query(GoogleStopword).filter(GoogleStopword.value == v).first()
        if not exists:
            db.add(GoogleStopword(value=v, description="built-in default", active=True))
            inserted += 1
    if inserted:
        db.commit()
    return inserted


def seed_default_directory_domains(db: Session) -> int:
    """Insert all built-in directory domains into the DB, skipping existing ones.

    Returns number of rows actually inserted.
    """
    from app.services.scoring import _DIRECTORY_DOMAINS  # import here to avoid circular
    inserted = 0
    for value in _DIRECTORY_DOMAINS:
        v = value.strip().lower()
        if not v:
            continue
        exists = db.query(GoogleDirectoryDomain).filter(GoogleDirectoryDomain.value == v).first()
        if not exists:
            db.add(GoogleDirectoryDomain(value=v, description="built-in default", active=True))
            inserted += 1
    if inserted:
        db.commit()
    return inserted


def seed_default_tfidf_stopwords(db: Session) -> int:
    """Insert all built-in TF-IDF stopwords into the DB, skipping existing ones.

    Returns number of rows actually inserted.
    """
    from app.services.collection import _TFIDF_STOPWORDS  # import here to avoid circular
    inserted = 0
    for value in _TFIDF_STOPWORDS:
        v = value.strip().lower()
        if not v:
            continue
        exists = db.query(TfidfStopword).filter(TfidfStopword.value == v).first()
        if not exists:
            db.add(TfidfStopword(value=v, description="built-in default", active=True))
            inserted += 1
    if inserted:
        db.commit()
    return inserted


def list_google_stopwords(db: Session) -> list[GoogleStopword]:
    return db.query(GoogleStopword).order_by(GoogleStopword.id).all()


def get_google_stopword(db: Session, row_id: int) -> GoogleStopword | None:
    return db.query(GoogleStopword).filter(GoogleStopword.id == row_id).first()


def get_active_google_stopwords(db: Session) -> set[str]:
    rows = db.query(GoogleStopword).filter(GoogleStopword.active.is_(True)).all()
    return {r.value.strip().lower() for r in rows if r.value and r.value.strip()}


def create_google_stopword(db: Session, *, value: str, description: str | None = None, active: bool = True) -> GoogleStopword:
    row = GoogleStopword(value=value.strip().lower(), description=description, active=active)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_google_stopword(
    db: Session,
    row: GoogleStopword,
    *,
    value: str | None = None,
    description: str | None = None,
    active: bool | None = None,
) -> GoogleStopword:
    if value is not None:
        row.value = value.strip().lower()
    if description is not None:
        row.description = description
    if active is not None:
        row.active = active
    db.commit()
    db.refresh(row)
    return row


def delete_google_stopword(db: Session, row: GoogleStopword) -> None:
    db.delete(row)
    db.commit()


def list_google_directory_domains(db: Session) -> list[GoogleDirectoryDomain]:
    return db.query(GoogleDirectoryDomain).order_by(GoogleDirectoryDomain.id).all()


def get_google_directory_domain(db: Session, row_id: int) -> GoogleDirectoryDomain | None:
    return db.query(GoogleDirectoryDomain).filter(GoogleDirectoryDomain.id == row_id).first()


def get_active_google_directory_domains(db: Session) -> set[str]:
    rows = db.query(GoogleDirectoryDomain).filter(GoogleDirectoryDomain.active.is_(True)).all()
    return {r.value.strip().lower() for r in rows if r.value and r.value.strip()}


def create_google_directory_domain(
    db: Session,
    *,
    value: str,
    description: str | None = None,
    active: bool = True,
) -> GoogleDirectoryDomain:
    row = GoogleDirectoryDomain(value=value.strip().lower(), description=description, active=active)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_google_directory_domain(
    db: Session,
    row: GoogleDirectoryDomain,
    *,
    value: str | None = None,
    description: str | None = None,
    active: bool | None = None,
) -> GoogleDirectoryDomain:
    if value is not None:
        row.value = value.strip().lower()
    if description is not None:
        row.description = description
    if active is not None:
        row.active = active
    db.commit()
    db.refresh(row)
    return row


def delete_google_directory_domain(db: Session, row: GoogleDirectoryDomain) -> None:
    db.delete(row)
    db.commit()


def list_tfidf_stopwords(db: Session) -> list[TfidfStopword]:
    return db.query(TfidfStopword).order_by(TfidfStopword.id).all()


def get_tfidf_stopword(db: Session, row_id: int) -> TfidfStopword | None:
    return db.query(TfidfStopword).filter(TfidfStopword.id == row_id).first()


def get_active_tfidf_stopwords(db: Session) -> set[str]:
    rows = db.query(TfidfStopword).filter(TfidfStopword.active.is_(True)).all()
    return {r.value.strip().lower() for r in rows if r.value and r.value.strip()}


def create_tfidf_stopword(db: Session, *, value: str, description: str | None = None, active: bool = True) -> TfidfStopword:
    row = TfidfStopword(value=value.strip().lower(), description=description, active=active)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_tfidf_stopword(
    db: Session,
    row: TfidfStopword,
    *,
    value: str | None = None,
    description: str | None = None,
    active: bool | None = None,
) -> TfidfStopword:
    if value is not None:
        row.value = value.strip().lower()
    if description is not None:
        row.description = description
    if active is not None:
        row.active = active
    db.commit()
    db.refresh(row)
    return row


def delete_tfidf_stopword(db: Session, row: TfidfStopword) -> None:
    db.delete(row)
    db.commit()
