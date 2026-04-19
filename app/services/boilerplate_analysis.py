"""Boilerplate pattern management: FR/IT seed patterns and auto-expansion analysis.

Two capabilities:
  1. seed_multilang_boilerplate(db) — insert the standard Swiss registry boilerplate
     phrases for French and Italian that are NOT yet in the DB.  Safe to run
     repeatedly (skips patterns that already exist).

  2. run_boilerplate_analysis(db, ...) — corpus-frequency analysis that finds
     high-frequency sentence templates across all company purpose texts.  Results
     are written to the boilerplate_candidates table as suggestions for the admin
     to review and promote to active patterns.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Seed patterns ─────────────────────────────────────────────────────────────
#
# These are standard phrases that appear verbatim (or nearly so) in Swiss
# commercial register purpose texts for FR (Romandy) and IT (Ticino / Graubünden).
# Each tuple: (regex_pattern, human description, example sentence)

_FR_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"la soci[eé]t[eé] a pour but",
        "FR: standard purpose opening (La société a pour but...)",
        "La société a pour but de...",
    ),
    (
        r"la soci[eé]t[eé] a pour objet",
        "FR: alternative purpose opening (a pour objet...)",
        "La société a pour objet...",
    ),
    (
        r"notamment\s+(?:mais\s+pas\s+exclusivement\s+)?(?:de\s+|d')",
        "FR: boilerplate qualifier (notamment de / notamment d')",
        "notamment de fournir...",
    ),
    (
        r"elle peut[\s,]+(?:en\s+outre[\s,]+)?(?:notamment[\s,]+)?(?:acqu[eé]rir|cr[eé]er|d[eé]tenir|prendrepar|prendre\s+des\s+participations)",
        "FR: scope extension clause (elle peut notamment acquérir/détenir...)",
        "Elle peut, en outre, acquérir des participations...",
    ),
    (
        r"en\s+(?:suisse\s+et\s+)?[àa]\s+l['']?[eé]tranger",
        "FR: geographic scope (en Suisse et à l'étranger)",
        "en Suisse et à l'étranger",
    ),
    (
        r"(?:la\s+)?soci[eé]t[eé]\s+peut\s+(?:accomplir|effectuer|r[eé]aliser|entreprendre)",
        "FR: ancillary activities clause",
        "La société peut accomplir toutes opérations...",
    ),
    (
        r"toutes\s+op[eé]rations\s+(?:commerciales|financi[eè]res|immobili[eè]res|industrielles|civiles)",
        "FR: catch-all operations clause",
        "toutes opérations commerciales, financières, industrielles...",
    ),
    (
        r"l['']?(?:achat|acquisition|la\s+vente|la\s+location|la\s+gestion)\s+(?:et\s+la\s+)?(?:de\s+)?(?:biens\s+immobiliers|immeubles|terrains)",
        "FR: real estate boilerplate (achat/vente de biens immobiliers)",
        "l'achat, la vente et la gestion de biens immobiliers",
    ),
    (
        r"la\s+(?:gestion|administration|repr[eé]sentation)\s+de\s+(?:soci[eé]t[eé]s|filiales|participations)",
        "FR: holding/management boilerplate",
        "la gestion de sociétés et de participations",
    ),
    (
        r"ainsi\s+que\s+toutes\s+(?:autres\s+)?(?:activit[eé]s|op[eé]rations)\s+(?:connexes|accessoires|similaires)",
        "FR: catch-all suffix (ainsi que toutes activités connexes)",
        "ainsi que toutes activités connexes",
    ),
]

_IT_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"la\s+societ[àa]\s+ha\s+per\s+(?:scopo|oggetto)",
        "IT: standard purpose opening (La società ha per scopo/oggetto...)",
        "La società ha per scopo...",
    ),
    (
        r"(?:la\s+)?societ[àa]\s+pu[oò]\s+(?:altres[ìi]\s+)?(?:acquistare|detenere|creare|assumere\s+partecipazioni)",
        "IT: scope extension clause (può altresì acquistare...)",
        "La società può altresì acquistare partecipazioni...",
    ),
    (
        r"in\s+svizzera\s+e\s+all['']estero",
        "IT: geographic scope (in Svizzera e all'estero)",
        "in Svizzera e all'estero",
    ),
    (
        r"tutte\s+le\s+operazioni\s+(?:commerciali|finanziarie|immobiliari|industriali)",
        "IT: catch-all operations clause",
        "tutte le operazioni commerciali, finanziarie...",
    ),
    (
        r"nonch[eé]\s+tutte\s+le\s+(?:altre\s+)?(?:attivit[àa]|operazioni)\s+(?:connesse|accessorie|complementari)",
        "IT: catch-all suffix (nonché tutte le attività connesse)",
        "nonché tutte le attività connesse",
    ),
    (
        r"l['']?(?:acquisto|la\s+vendita|la\s+gestione|l['']amministrazione)\s+(?:di\s+)?(?:immobili|beni\s+immobili)",
        "IT: real estate boilerplate",
        "l'acquisto, la vendita e la gestione di immobili",
    ),
    (
        r"la\s+(?:gestione|amministrazione|rappresentanza)\s+di\s+(?:societ[àa]|filiali|partecipazioni)",
        "IT: holding/management boilerplate",
        "la gestione di società e partecipazioni",
    ),
    (
        r"in\s+particolare\s+(?:ma\s+non\s+esclusivamente\s+)?(?:la\s+|lo\s+|l[''])",
        "IT: qualifier (in particolare...)",
        "in particolare la fornitura di...",
    ),
]


def seed_multilang_boilerplate(db: Session) -> dict[str, int]:
    """Insert FR and IT boilerplate patterns that are not yet in the DB.

    Returns {"inserted": N, "skipped": M}.
    """
    from app.models.boilerplate import BoilerplatePattern

    existing = {r.pattern for r in db.query(BoilerplatePattern.pattern).all()}
    inserted = 0
    skipped = 0

    all_patterns = [("FR", p) for p in _FR_PATTERNS] + [("IT", p) for p in _IT_PATTERNS]
    for lang, (pattern, description, example) in all_patterns:
        if pattern in existing:
            skipped += 1
            continue
        db.add(BoilerplatePattern(
            pattern=pattern,
            description=f"[{lang}] {description}",
            example=example,
            active=True,
        ))
        inserted += 1

    if inserted:
        db.commit()
    logger.info("seed_multilang_boilerplate: inserted=%d skipped=%d", inserted, skipped)
    return {"inserted": inserted, "skipped": skipped}


# ── Auto-expansion analysis ────────────────────────────────────────────────────

def run_boilerplate_analysis(
    db: Session,
    *,
    min_match_count: int = 500,
    max_candidates: int = 200,
    min_sentence_len: int = 10,
    max_sentence_len: int = 120,
    sample_limit: int = 200_000,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict:
    """Analyse purpose text corpus to surface new boilerplate candidates.

    Algorithm:
      1. Load purpose texts (up to sample_limit rows).
      2. Split into sentences on common punctuation.
      3. Normalize each sentence (lowercase, collapse whitespace, strip leading articles).
      4. Count sentence frequencies.
      5. Sentences appearing >= min_match_count times that are NOT already covered
         by an existing pattern are written to the boilerplate_candidates table.

    Returns stats dict with candidate count, top candidates, etc.
    """
    from app.models.company import Company
    from app.models.boilerplate import BoilerplatePattern

    # Load existing active patterns for dedup
    existing_patterns = [
        re.compile(r.pattern, re.IGNORECASE)
        for r in db.query(BoilerplatePattern).filter(BoilerplatePattern.active.is_(True)).all()
        if _safe_compile(r.pattern)
    ]

    # Stream purpose texts in batches
    logger.info("boilerplate_analysis: loading purpose texts (limit=%d)", sample_limit)
    sentence_counter: Counter = Counter()
    total = db.query(Company).filter(Company.purpose.isnot(None)).count()
    total = min(total, sample_limit)
    batch_size = 5000
    offset = 0
    loaded = 0

    while loaded < total:
        batch = (
            db.query(Company.purpose)
            .filter(Company.purpose.isnot(None))
            .order_by(Company.id)
            .offset(offset)
            .limit(batch_size)
            .all()
        )
        if not batch:
            break
        for (purpose,) in batch:
            for sent in _split_sentences(purpose):
                norm = _normalize(sent)
                if min_sentence_len <= len(norm) <= max_sentence_len:
                    sentence_counter[norm] += 1
        loaded += len(batch)
        offset += len(batch)
        if progress_cb:
            progress_cb(loaded, total, {"loaded": loaded, "unique_sentences": len(sentence_counter)})

    # Filter: high-frequency, not already covered
    candidates = []
    for sent, count in sentence_counter.most_common(max_candidates * 5):
        if count < min_match_count:
            break
        if _already_covered(sent, existing_patterns):
            continue
        # Convert the normalized sentence to a safe regex pattern
        pattern = _sentence_to_regex(sent)
        if not _safe_compile(pattern):
            continue
        candidates.append({
            "sentence": sent,
            "count": count,
            "pattern": pattern,
        })
        if len(candidates) >= max_candidates:
            break

    # Upsert candidates into boilerplate_patterns as inactive (for admin review)
    upserted = 0
    for c in candidates:
        existing = db.query(BoilerplatePattern).filter(
            BoilerplatePattern.pattern == c["pattern"]
        ).first()
        if existing is None:
            db.add(BoilerplatePattern(
                pattern=c["pattern"],
                description=f"[AUTO] Appears {c['count']}x in corpus",
                example=c["sentence"][:512],
                match_count=c["count"],
                active=False,  # admin must review before activating
            ))
            upserted += 1
        else:
            existing.match_count = c["count"]

    if upserted:
        db.commit()

    logger.info(
        "boilerplate_analysis: %d candidates found, %d new patterns saved (inactive, pending review)",
        len(candidates), upserted,
    )
    return {
        "total_purposes_scanned": loaded,
        "unique_sentences": len(sentence_counter),
        "candidates_found": len(candidates),
        "new_patterns_saved": upserted,
        "top_candidates": candidates[:20],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

_SENT_SPLIT = re.compile(r"[.;]\s+")
_WHITESPACE = re.compile(r"\s+")
_LEADING_ART = re.compile(
    r"^(?:die\s+|der\s+|das\s+|la\s+|le\s+|les\s+|l['']|lo\s+|il\s+|i\s+|gli\s+|"
    r"the\s+|die\s+gesellschaft\s+|die\s+firma\s+|die\s+ag\s+|die\s+gmbh\s+)",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _normalize(s: str) -> str:
    s = s.lower()
    s = _WHITESPACE.sub(" ", s).strip()
    s = _LEADING_ART.sub("", s)
    return s


def _already_covered(sentence: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(sentence) for p in patterns)


def _sentence_to_regex(sentence: str) -> str:
    """Convert a normalized sentence to a loose regex that tolerates minor variations."""
    # Escape special regex chars, then relax whitespace to \s+
    escaped = re.escape(sentence)
    return re.sub(r"\\ ", r"\\s+", escaped)


def _safe_compile(pattern: str) -> re.Pattern | None:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None
