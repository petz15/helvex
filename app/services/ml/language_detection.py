"""Lightweight language detection for Swiss company purpose texts.

Supports de/fr/it/en using lingua-language-detector, which handles short
texts more accurately than langdetect by using confidence thresholds and
alphabet-based pre-filtering.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

_MIN_TEXT_LEN = 15

_LANG_MAP: dict = {
    "GERMAN": "de",
    "FRENCH": "fr",
    "ITALIAN": "it",
    "ENGLISH": "en",
}


@lru_cache(maxsize=1)
def _get_detector():
    from lingua import Language, LanguageDetectorBuilder
    return (
        LanguageDetectorBuilder
        .from_languages(Language.GERMAN, Language.FRENCH, Language.ITALIAN, Language.ENGLISH)
        .with_minimum_relative_distance(0.1)
        .build()
    )


def detect_purpose_language(text: str | None) -> str | None:
    """Return 'de' | 'fr' | 'it' | 'en' | None.

    Returns None when text is too short, blank, or detection confidence
    falls below the minimum relative distance threshold.
    """
    if not text or len(text.strip()) < _MIN_TEXT_LEN:
        return None
    try:
        lang = _get_detector().detect_language_of(text.strip())
        if lang is None:
            return None
        return _LANG_MAP.get(lang.name)
    except Exception as exc:
        logger.warning("Language detection failed: %s", exc)
        return None


def detect_language_bulk(
    db,
    *,
    batch_size: int = 1000,
    only_missing: bool = True,
    progress_cb=None,
) -> dict:
    """Backfill purpose_language for all companies where it is NULL (or all).

    Safe to re-run — with only_missing=True skips companies that already have a language.
    """
    from sqlalchemy import func
    from app.models.company import Company

    stats = {
        "selected": 0,
        "updated": 0,
        "skipped_no_purpose": 0,
        "skipped_existing": 0,
        "errors": [],
    }

    query = db.query(Company).filter(Company.purpose.isnot(None))
    if only_missing:
        query = query.filter(Company.purpose_language.is_(None))

    total = query.with_entities(func.count(Company.id)).scalar() or 0
    stats["selected"] = total
    offset = 0

    while True:
        batch = query.order_by(Company.id.asc()).offset(offset).limit(batch_size).all()
        if not batch:
            break

        for company in batch:
            try:
                if only_missing and company.purpose_language:
                    stats["skipped_existing"] += 1
                    continue
                if not company.purpose:
                    stats["skipped_no_purpose"] += 1
                    continue
                lang = detect_purpose_language(company.purpose)
                if lang:
                    company.purpose_language = lang
                    stats["updated"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Language detection failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid}: {type(exc).__name__}: {exc}")

        db.commit()
        offset += len(batch)

        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    return stats
