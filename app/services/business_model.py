"""Rule-based business model classification from company purpose text.

Classifies companies as:
  'b2b'   — primarily serves other businesses / organisations
  'b2c'   — primarily serves end consumers
  'b2g'   — primarily serves government / public sector
  'mixed' — multiple significant signals across categories
  None    — insufficient data to classify

The classifier works on the German statutory purpose text (and optionally
purpose_keywords) by scoring term frequency across three signal lists.
"""
from __future__ import annotations

import re

# ── Signal term lists ─────────────────────────────────────────────────────────

_B2B_TERMS = frozenset({
    "unternehmen", "unternehmer", "unternehmens", "betrieb", "betriebe", "betrieben",
    "firmen", "firma", "firmenkundschaft", "geschäftskunden", "geschäftspartner",
    "industrie", "industriell", "industriellen", "gewerbe", "gewerblich",
    "handelsbetrieb", "händler", "lieferant", "lieferanten", "zulieferer",
    "b2b", "business-to-business", "grosshandel", "grosshändler",
    "fachhandel", "fachhändler", "werkzeughandel", "maschinenbau",
    "auftraggeber", "dienstleistungsunternehmen", "institutionelle",
    "corporate", "kommerziell", "gewerbliche", "fabrik", "produktion",
})

_B2C_TERMS = frozenset({
    "privatkunden", "privatpersonen", "privatkundschaft", "endkunden", "endverbraucher",
    "verbraucher", "konsumenten", "haushalte", "haushalt",
    "b2c", "business-to-consumer", "einzelhandel", "detailhandel",
    "ladenlokal", "laden", "boutique", "shop", "online-shop", "webshop",
    "retail", "detailhändler", "direktverkauf", "selbstbedienung",
    "laufkundschaft", "kundschaft", "publikum", "besucher",
})

_B2G_TERMS = frozenset({
    "gemeinde", "gemeinden", "gemeindeverwaltung", "kanton", "kantonal",
    "öffentlich", "öffentliche", "öffentlichen", "öffentlichkeit",
    "verwaltung", "behörden", "behörde", "staatlich", "staatliche",
    "bundesbehörden", "bundesbehörde", "bund", "bundesrat",
    "öv", "öffentlicher verkehr", "öffentlicher",
    "soziale einrichtung", "soziale einrichtungen", "sozialdienst",
    "schule", "schulen", "bildungseinrichtung", "hochschule",
    "spital", "spitäler", "krankenhaus", "klinik",
    "non-profit", "nonprofit", "stiftung", "verein", "verbände",
    "infrastruktur", "versorgung", "entsorgung",
})

# Threshold: a category must score at least this fraction of total signals
_DOMINANCE_THRESHOLD = 0.4
# If two categories are within this margin of each other, classify as 'mixed'
_MIXED_MARGIN = 0.15
# Minimum total signal count to classify at all
_MIN_SIGNALS = 2


def classify_business_model(purpose: str | None, purpose_keywords: str | None = None) -> str | None:
    """Return the business model category or None if insufficient data."""
    text_parts = []
    if purpose:
        text_parts.append(purpose.lower())
    if purpose_keywords:
        text_parts.append(purpose_keywords.lower())

    if not text_parts:
        return None

    text = " ".join(text_parts)
    # Tokenise on word boundaries
    words = frozenset(re.findall(r"\b\w+\b", text))

    b2b = len(words & _B2B_TERMS)
    b2c = len(words & _B2C_TERMS)
    b2g = len(words & _B2G_TERMS)
    total = b2b + b2c + b2g

    if total < _MIN_SIGNALS:
        return None

    scores = {"b2b": b2b / total, "b2c": b2c / total, "b2g": b2g / total}
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top_cat, top_score = ranked[0]
    second_score = ranked[1][1]

    if top_score < _DOMINANCE_THRESHOLD:
        return "mixed"
    if top_score - second_score < _MIXED_MARGIN and second_score >= 0.2:
        return "mixed"
    return top_cat


def run_classify_business_model(
    db,
    *,
    limit: int | None = None,
    batch_size: int = 1000,
    progress_cb=None,
) -> dict:
    """Batch-classify all companies and persist business_model field.

    Returns stats dict: {"updated": int, "classified": int, "skipped": int}
    """
    from app.models.company import Company
    from sqlalchemy import update

    stats = {"updated": 0, "classified": 0, "skipped": 0}

    query = db.query(Company.id, Company.purpose, Company.purpose_keywords)
    if limit:
        query = query.limit(limit)

    total = db.query(Company.id).count()
    processed = 0

    offset = 0
    while True:
        batch = (
            db.query(Company.id, Company.purpose, Company.purpose_keywords)
            .order_by(Company.id.asc())
            .offset(offset)
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        updates = []
        for company_id, purpose, purpose_keywords in batch:
            bm = classify_business_model(purpose, purpose_keywords)
            updates.append({"id": company_id, "business_model": bm})
            if bm:
                stats["classified"] += 1
            else:
                stats["skipped"] += 1

        if updates:
            db.bulk_update_mappings(Company, updates)
            db.commit()
            stats["updated"] += len(updates)

        processed += len(batch)
        offset += batch_size
        if progress_cb:
            progress_cb(min(processed, total), total, stats)

        if not batch or (limit and processed >= limit):
            break

    return stats
