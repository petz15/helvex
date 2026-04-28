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


def detect_and_update_language(company) -> str | None:
    """Detect language from company purpose and return the code.

    Does not persist — callers are responsible for writing to DB.
    Falls back to company.purpose_language if already set.
    """
    if company.purpose_language:
        return company.purpose_language
    return detect_purpose_language(company.purpose)
