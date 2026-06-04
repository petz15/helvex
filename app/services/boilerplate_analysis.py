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

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Seed patterns ─────────────────────────────────────────────────────────────
#
# These are standard phrases that appear verbatim (or nearly so) in Swiss
# commercial register purpose texts for DE, FR (Romandy) and IT (Ticino / Graubünden).
# Each tuple: (regex_pattern, human description, example sentence, truncate)
# truncate=True  → strip from the start of the matching sentence to end of text
# truncate=False → strip only the matching sentence (existing behaviour)

_DE_PATTERNS: list[tuple[str, str, str, bool]] = [
    (
        r"die\s+gesellschaft\s+kann\s+(?:\w+\s+){0,5}zweigniederlassungen",
        "DE truncate: branch/subsidiary founding clause",
        "Die Gesellschaft kann Zweigniederlassungen in der Schweiz und im Ausland errichten.",
        True,
    ),
    (
        r"die\s+gesellschaft\s+kann\s+alle\s+gesch[äa]fte\s+t[äa]tigen",
        "DE truncate: all-business catch-all clause",
        "Die Gesellschaft kann alle Geschäfte tätigen, die mit dem Gesellschaftszweck zusammenhängen.",
        True,
    ),
    (
        r"die\s+gesellschaft\s+kann\s+sich\s+an\s+anderen\s+unternehmen",
        "DE truncate: participation in other companies",
        "Die Gesellschaft kann sich an anderen Unternehmen beteiligen.",
        True,
    ),
    (
        r"sie\s+kann\s+(?:überdies|ausserdem|zudem|ferner)\b",
        "DE truncate: additive boilerplate clause (sie kann überdies/ausserdem)",
        "Sie kann überdies Grundstücke erwerben und Darlehen gewähren.",
        True,
    ),
    (
        r"die\s+gesellschaft\s+kann\s+(?:auch\s+)?grundst[üu]cke\s+(?:erwerben|halten|ver[äa]ussern|kaufen)",
        "DE truncate: real estate secondary clause",
        "Die Gesellschaft kann auch Grundstücke erwerben und veräussern.",
        True,
    ),
]

_FR_PATTERNS: list[tuple[str, str, str, bool]] = [
    # ── Truncation triggers (terminal boilerplate openers) ────────────────────
    (
        r"d['']une\s+mani[eè]re\s+g[eé]n[eé]rale[,\s]",
        "FR truncate: general catch-all opener",
        "D'une manière générale, la société peut faire toutes opérations...",
        True,
    ),
    (
        r"la\s+soci[eé]t[eé]\s+peut\s+en\s+outre\b",
        "FR truncate: société peut en outre (furthermore)",
        "La société peut en outre créer des succursales en Suisse et à l'étranger.",
        True,
    ),
    (
        r"elle\s+peut\s+en\s+outre\b",
        "FR truncate: elle peut en outre (furthermore)",
        "Elle peut en outre acquérir des participations dans d'autres sociétés.",
        True,
    ),
    (
        r"la\s+soci[eé]t[eé]\s+peut\s+[eé]galement\b",
        "FR truncate: société peut également",
        "La société peut également accorder des prêts ou des garanties à des tiers.",
        True,
    ),
    (
        r"la\s+soci[eé]t[eé]\s+peut\s+par\s+ailleurs\b",
        "FR truncate: société peut par ailleurs",
        "La société peut par ailleurs fonder des succursales en Suisse.",
        True,
    ),
    (
        r"la\s+soci[eé]t[eé]\s+peut\s+(?:effectuer|accomplir|r[eé]aliser)\s+toutes\s+op[eé]rations",
        "FR truncate: all-operations catch-all",
        "La société peut effectuer toutes opérations commerciales, financières ou industrielles.",
        True,
    ),
    (
        r"la\s+soci[eé]t[eé]\s+peut\s+exercer\s+toute\s+activit[eé]",
        "FR truncate: all-activities catch-all",
        "La société peut exercer toute activité en rapport direct ou indirect avec son but.",
        True,
    ),
    (
        r"la\s+soci[eé]t[eé]\s+peut\s+(?:fonder|cr[eé]er|[eé]tablir)\s+(?:des\s+)?succursales",
        "FR truncate: branch founding clause",
        "La société peut fonder des succursales en Suisse et à l'étranger.",
        True,
    ),
    # ── Sentence-level patterns (remove matching sentence only) ───────────────
    (
        r"la soci[eé]t[eé] a pour but",
        "FR: standard purpose opening (La société a pour but...)",
        "La société a pour but de...",
        False,
    ),
    (
        r"la soci[eé]t[eé] a pour objet",
        "FR: alternative purpose opening (a pour objet...)",
        "La société a pour objet...",
        False,
    ),
    (
        r"notamment\s+(?:mais\s+pas\s+exclusivement\s+)?(?:de\s+|d')",
        "FR: boilerplate qualifier (notamment de / notamment d')",
        "notamment de fournir...",
        False,
    ),
    (
        r"elle peut[\s,]+(?:en\s+outre[\s,]+)?(?:notamment[\s,]+)?(?:acqu[eé]rir|cr[eé]er|d[eé]tenir|prendrepar|prendre\s+des\s+participations)",
        "FR: scope extension clause (elle peut notamment acquérir/détenir...)",
        "Elle peut, en outre, acquérir des participations...",
        False,
    ),
    (
        r"en\s+(?:suisse\s+et\s+)?[àa]\s+l['']?[eé]tranger",
        "FR: geographic scope (en Suisse et à l'étranger)",
        "en Suisse et à l'étranger",
        False,
    ),
    (
        r"toutes\s+op[eé]rations\s+(?:commerciales|financi[eè]res|immobili[eè]res|industrielles|civiles)",
        "FR: catch-all operations clause",
        "toutes opérations commerciales, financières, industrielles...",
        False,
    ),
    (
        r"l['']?(?:achat|acquisition|la\s+vente|la\s+location|la\s+gestion)\s+(?:et\s+la\s+)?(?:de\s+)?(?:biens\s+immobiliers|immeubles|terrains)",
        "FR: real estate boilerplate (achat/vente de biens immobiliers)",
        "l'achat, la vente et la gestion de biens immobiliers",
        False,
    ),
    (
        r"la\s+(?:gestion|administration|repr[eé]sentation)\s+de\s+(?:soci[eé]t[eé]s|filiales|participations)",
        "FR: holding/management boilerplate",
        "la gestion de sociétés et de participations",
        False,
    ),
    (
        r"ainsi\s+que\s+toutes\s+(?:autres\s+)?(?:activit[eé]s|op[eé]rations)\s+(?:connexes|accessoires|similaires)",
        "FR: catch-all suffix (ainsi que toutes activités connexes)",
        "ainsi que toutes activités connexes",
        False,
    ),
]

_IT_PATTERNS: list[tuple[str, str, str, bool]] = [
    # ── Truncation triggers (terminal boilerplate openers) ────────────────────
    (
        r"la\s+societ[àa]\s+pu[oò]\s+inoltre\b",
        "IT truncate: società può inoltre (furthermore)",
        "La società può inoltre fondare succursali in Svizzera e all'estero.",
        True,
    ),
    (
        r"essa\s+pu[oò]\s+(?:altres[ìi]\s+)?(?:fondare|aprire|creare)\s+(?:delle\s+)?succursali",
        "IT truncate: branch founding clause",
        "Essa può altresì fondare succursali in Svizzera e all'estero.",
        True,
    ),
    (
        r"la\s+societ[àa]\s+pu[oò]\s+(?:compiere|svolgere|effettuare)\s+tutte\s+le\s+operazioni",
        "IT truncate: all-operations catch-all",
        "La società può compiere tutte le operazioni commerciali, finanziarie o industriali.",
        True,
    ),
    (
        r"la\s+societ[àa]\s+pu[oò]\s+svolgere\s+qualsiasi\s+attivit[àa]",
        "IT truncate: all-activities catch-all",
        "La società può svolgere qualsiasi attività in relazione diretta o indiretta con il suo scopo.",
        True,
    ),
    (
        r"in\s+modo\s+(?:pi[uù]\s+)?generale[,\s]",
        "IT truncate: general activities opener",
        "In modo più generale, la società può effettuare tutte le operazioni...",
        True,
    ),
    # ── Sentence-level patterns (remove matching sentence only) ───────────────
    (
        r"la\s+societ[àa]\s+ha\s+per\s+(?:scopo|oggetto)",
        "IT: standard purpose opening (La società ha per scopo/oggetto...)",
        "La società ha per scopo...",
        False,
    ),
    (
        r"(?:la\s+)?societ[àa]\s+pu[oò]\s+(?:altres[ìi]\s+)?(?:acquistare|detenere|creare|assumere\s+partecipazioni)",
        "IT: scope extension clause (può altresì acquistare...)",
        "La società può altresì acquistare partecipazioni...",
        False,
    ),
    (
        r"in\s+svizzera\s+e\s+all['']estero",
        "IT: geographic scope (in Svizzera e all'estero)",
        "in Svizzera e all'estero",
        False,
    ),
    (
        r"tutte\s+le\s+operazioni\s+(?:commerciali|finanziarie|immobiliari|industriali)",
        "IT: catch-all operations clause",
        "tutte le operazioni commerciali, finanziarie...",
        False,
    ),
    (
        r"nonch[eé]\s+tutte\s+le\s+(?:altre\s+)?(?:attivit[àa]|operazioni)\s+(?:connesse|accessorie|complementari)",
        "IT: catch-all suffix (nonché tutte le attività connesse)",
        "nonché tutte le attività connesse",
        False,
    ),
    (
        r"l['']?(?:acquisto|la\s+vendita|la\s+gestione|l['']amministrazione)\s+(?:di\s+)?(?:immobili|beni\s+immobili)",
        "IT: real estate boilerplate",
        "l'acquisto, la vendita e la gestione di immobili",
        False,
    ),
    (
        r"la\s+(?:gestione|amministrazione|rappresentanza)\s+di\s+(?:societ[àa]|filiali|partecipazioni)",
        "IT: holding/management boilerplate",
        "la gestione di società e partecipazioni",
        False,
    ),
    (
        r"in\s+particolare\s+(?:ma\s+non\s+esclusivamente\s+)?(?:la\s+|lo\s+|l[''])",
        "IT: qualifier (in particolare...)",
        "in particolare la fornitura di...",
        False,
    ),
]


def seed_multilang_boilerplate(db: Session) -> dict[str, int]:
    """Insert DE/FR/IT boilerplate patterns that are not yet in the DB.

    Returns {"inserted": N, "skipped": M}.
    """
    from app.models.boilerplate import BoilerplatePattern

    existing = {r.pattern for r in db.query(BoilerplatePattern.pattern).all()}
    inserted = 0
    skipped = 0

    all_patterns = (
        [("DE", p) for p in _DE_PATTERNS]
        + [("FR", p) for p in _FR_PATTERNS]
        + [("IT", p) for p in _IT_PATTERNS]
    )
    for lang, (pattern, description, example, truncate) in all_patterns:
        if pattern in existing:
            skipped += 1
            continue
        db.add(BoilerplatePattern(
            pattern=pattern,
            description=f"[{lang}] {description}",
            example=example,
            active=True,
            truncate=truncate,
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
    total = db.query(func.count(Company.id)).filter(Company.purpose.isnot(None)).scalar() or 0
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
