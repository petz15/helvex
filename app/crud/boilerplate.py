import re
import time

from sqlalchemy.orm import Session

from app.models.boilerplate import BoilerplatePattern

_boilerplate_cache: dict = {}
_BOILERPLATE_CACHE_TTL = 3600  # 1 hour


def list_boilerplate_patterns(db: Session) -> list[BoilerplatePattern]:
    return db.query(BoilerplatePattern).order_by(BoilerplatePattern.id).all()


def get_active_boilerplate_patterns(db: Session) -> list[tuple[re.Pattern, bool]]:
    """Return (compiled_pattern, truncate) pairs for all active global boilerplate entries.

    Deprecated in favour of get_effective_boilerplate_patterns() for org-aware callers.
    Results are cached for 1 hour to avoid recompiling regex patterns.
    """
    cache_key = "global_patterns"
    now = time.monotonic()

    if cache_key in _boilerplate_cache:
        cached_data = _boilerplate_cache[cache_key]
        if (now - cached_data["ts"]) < _BOILERPLATE_CACHE_TTL:
            return cached_data["patterns"]

    rows = db.query(BoilerplatePattern).filter(BoilerplatePattern.active.is_(True)).all()
    compiled: list[tuple[re.Pattern, bool]] = []
    for row in rows:
        try:
            compiled.append((re.compile(row.pattern, re.IGNORECASE), bool(row.truncate)))
        except re.error:
            pass  # skip invalid patterns silently

    _boilerplate_cache[cache_key] = {"patterns": compiled, "ts": now}
    return compiled


def get_effective_boilerplate_patterns(db: Session, *, org_id: int | None = None) -> list[tuple[re.Pattern, bool]]:
    """Return (compiled_pattern, truncate) pairs for global patterns PLUS org-specific overrides.

    - Global patterns (org_id IS NULL) are always included.
    - When org_id is provided, org-specific patterns for that org are merged in.
    - Only active patterns are compiled.
    - Results are cached for 1 hour.
    """
    cache_key = f"org_patterns_{org_id}"
    now = time.monotonic()

    if cache_key in _boilerplate_cache:
        cached_data = _boilerplate_cache[cache_key]
        if (now - cached_data["ts"]) < _BOILERPLATE_CACHE_TTL:
            return cached_data["patterns"]

    q = db.query(BoilerplatePattern).filter(BoilerplatePattern.active.is_(True))
    if org_id is not None:
        from sqlalchemy import or_
        q = q.filter(
            or_(
                BoilerplatePattern.org_id.is_(None),
                BoilerplatePattern.org_id == org_id,
            )
        )
    else:
        q = q.filter(BoilerplatePattern.org_id.is_(None))
    rows = q.all()
    compiled: list[tuple[re.Pattern, bool]] = []
    for row in rows:
        try:
            compiled.append((re.compile(row.pattern, re.IGNORECASE), bool(row.truncate)))
        except re.error:
            pass

    _boilerplate_cache[cache_key] = {"patterns": compiled, "ts": now}
    return compiled


def create_boilerplate_pattern(
    db: Session,
    *,
    pattern: str,
    description: str | None = None,
    example: str | None = None,
    match_count: int | None = None,
    active: bool = True,
    truncate: bool = False,
) -> BoilerplatePattern:
    row = BoilerplatePattern(
        pattern=pattern,
        description=description,
        example=example,
        match_count=match_count,
        active=active,
        truncate=truncate,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_boilerplate_pattern(
    db: Session,
    row: BoilerplatePattern,
    *,
    pattern: str | None = None,
    description: str | None = None,
    example: str | None = None,
    active: bool | None = None,
    truncate: bool | None = None,
) -> BoilerplatePattern:
    if pattern is not None:
        row.pattern = pattern
    if description is not None:
        row.description = description
    if example is not None:
        row.example = example
    if active is not None:
        row.active = active
    if truncate is not None:
        row.truncate = truncate
    db.commit()
    db.refresh(row)
    return row


def delete_boilerplate_pattern(db: Session, row: BoilerplatePattern) -> None:
    db.delete(row)
    db.commit()


def get_boilerplate_pattern(db: Session, pattern_id: int) -> BoilerplatePattern | None:
    return db.query(BoilerplatePattern).filter(BoilerplatePattern.id == pattern_id).first()


# ── Boilerplate candidates (auto-mined) ───────────────────────────────────────

from app.models.boilerplate_candidate import BoilerplateCandidate  # noqa: E402


def upsert_boilerplate_candidates(
    db: Session,
    candidates: list[dict],
) -> int:
    """Upsert a list of candidate dicts. Returns the number of rows written.

    Each dict may have: term, doc_frequency, cluster_entropy,
    cluster_label_frequency, confidence, source_type, language.
    Existing rows are updated if the new confidence is higher.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not candidates:
        return 0

    stmt = pg_insert(BoilerplateCandidate).values(candidates)
    stmt = stmt.on_conflict_do_update(
        index_elements=["term"],
        set_={
            "doc_frequency": stmt.excluded.doc_frequency,
            "cluster_entropy": stmt.excluded.cluster_entropy,
            "cluster_label_frequency": stmt.excluded.cluster_label_frequency,
            "confidence": stmt.excluded.confidence,
            "source_type": stmt.excluded.source_type,
            "language": stmt.excluded.language,
            "updated_at": sa.func.now(),
        },
        where=stmt.excluded.confidence > BoilerplateCandidate.confidence,
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount


def promote_boilerplate_candidate(
    db: Session,
    candidate: BoilerplateCandidate,
    *,
    confidence_threshold: float = 0.85,
) -> BoilerplatePattern | None:
    """Convert a high-confidence candidate into an active BoilerplatePattern.

    Skips if already promoted or confidence below threshold.
    """
    if candidate.promoted:
        return None
    if candidate.confidence is not None and candidate.confidence < confidence_threshold:
        return None

    # Build a safe word-boundary regex from the term
    escaped = re.escape(candidate.term.strip())
    pattern_str = r"\b" + escaped + r"\b"

    existing = db.query(BoilerplatePattern).filter(BoilerplatePattern.pattern == pattern_str).first()
    if existing:
        candidate.promoted = True
        candidate.promoted_pattern_id = existing.id
        db.commit()
        return existing

    row = BoilerplatePattern(
        pattern=pattern_str,
        description=f"Auto-promoted from candidate (confidence={candidate.confidence:.2f}, source={candidate.source_type})",
        active=True,
    )
    db.add(row)
    db.flush()
    candidate.promoted = True
    candidate.promoted_pattern_id = row.id
    db.commit()
    db.refresh(row)
    invalidate_boilerplate_cache()
    return row


def auto_promote_candidates(db: Session, *, threshold: float = 0.85) -> int:
    """Promote all un-promoted candidates above threshold. Returns count promoted."""
    candidates = (
        db.query(BoilerplateCandidate)
        .filter(
            BoilerplateCandidate.promoted.is_(False),
            BoilerplateCandidate.confidence >= threshold,
        )
        .all()
    )
    promoted = 0
    for c in candidates:
        if promote_boilerplate_candidate(db, c, confidence_threshold=threshold):
            promoted += 1
    return promoted


def invalidate_boilerplate_cache() -> None:
    _boilerplate_cache.clear()


import sqlalchemy as sa  # noqa: E402  (already imported above, but needed for upsert)
