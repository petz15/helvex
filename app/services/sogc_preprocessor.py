"""Preprocessing pipeline for SOGC publication history.

Reads the `sogc_pub` JSON blob stored on each company (sourced from Zefix
`sogcPub` field) and explodes it into structured `sogc_publications` and
`sogc_changes` rows.

Encoding fix: some newer Zefix entries are encoded in latin-1 but decoded as
utf-8, producing mojibake (e.g. "Ã¼" instead of "ü"). The fix re-encodes the
string as latin-1 bytes and decodes them as utf-8. It is only applied when
known mojibake byte-sequences are detected.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.clients.shab_client import _guess_sub_rubric
from app.models.company import Company
from app.models.sogc_change import SogcChange
from app.models.sogc_publication import SogcPublication

logger = logging.getLogger(__name__)

# ── Encoding fix ──────────────────────────────────────────────────────────────

_MOJIBAKE_MARKERS = ["Ã¼", "Ã¤", "Ã¶", "Ã©", "Ã«", "Ã¡", "Ã "]


def _try_fix_encoding(text: str) -> tuple[str, bool]:
    """Return (fixed_text, was_fixed). Only applies the fix when mojibake markers are present."""
    if not text or not any(m in text for m in _MOJIBAKE_MARKERS):
        return text, False
    try:
        fixed = text.encode("latin-1").decode("utf-8")
        return fixed, True
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text, False


# ── Multilingual change-type keyword patterns ─────────────────────────────────

CHANGE_PATTERNS: dict[str, dict[str, list[str]]] = {
    "address": {
        "de": ["sitz verlegt", "sitzverlegung", "adresse geändert", "domizil"],
        "fr": ["siège social transféré", "changement d'adresse", "domicile"],
        "it": ["sede trasferita", "cambio di indirizzo", "domicilio"],
        "en": ["registered office moved", "address changed"],
    },
    "person_added": {
        "de": ["neu:", "neu bestellt", "eingetreten", "tritt ein", "ernannt"],
        "fr": ["nouveau:", "nouvellement nommé", "nommé", "nouveau membre"],
        "it": ["nuovo:", "nominato", "nuovo membro"],
        "en": ["newly appointed", "new member", "appointed"],
    },
    "person_removed": {
        "de": ["tritt zurück", "ausgetreten", "ausgeschieden", "ist zurückgetreten"],
        "fr": ["démissionne", "démission", "sortant"],
        "it": ["si dimette", "dimissioni", "uscente"],
        "en": ["resigns", "resignation", "leaves"],
    },
    "capital": {
        "de": ["aktienkapital", "stammkapital", "kapitalerhöhung", "kapitalherabsetzung"],
        "fr": ["capital-actions", "capital social", "augmentation du capital"],
        "it": ["capitale azionario", "capitale sociale", "aumento di capitale"],
        "en": ["share capital", "capital increase", "capital reduction"],
    },
    "name": {
        "de": ["firma neu:", "umfirmiert", "firmenänderung", "neue firma:"],
        "fr": ["nouvelle raison sociale:", "changement de raison sociale"],
        "it": ["nuova ditta:", "cambio di ragione sociale"],
        "en": ["new company name:", "name change"],
    },
    "merger": {
        "de": ["fusion", "fusionsvertrag", "verschmelzung"],
        "fr": ["fusion", "fusionné avec"],
        "it": ["fusione", "fusionato con"],
        "en": ["merger", "merged with"],
    },
    "acquisition": {
        "de": ["übernahme", "übertragung", "erworben von", "hat übernommen"],
        "fr": ["reprise", "cession", "acquisition"],
        "it": ["acquisizione", "cessione"],
        "en": ["acquisition", "acquired by", "takeover"],
    },
    "purpose": {
        "de": ["zweck:", "zweck neu:", "zweckänderung", "zweck geändert"],
        "fr": ["but social:", "but:", "modification du but"],
        "it": ["scopo:", "scopo sociale modificato"],
        "en": ["purpose:", "business purpose changed"],
    },
    "status": {
        "de": ["auflösung", "in liquidation", "liquidation", "gelöscht"],
        "fr": ["dissolution", "en liquidation", "liquidation"],
        "it": ["scioglimento", "in liquidazione", "liquidazione"],
        "en": ["dissolution", "in liquidation", "liquidation"],
    },
}

_LANG_ORDER = ("de", "fr", "it", "en")


# ── sogcPub JSON parsing ───────────────────────────────────────────────────────

def _parse_sogc_pub_entries(sogc_pub_json: str) -> list[dict[str, Any]]:
    """Parse `sogc_pub` JSON blob → list of raw entry dicts.

    Handles both list and single-dict payloads.
    """
    try:
        data = json.loads(sogc_pub_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _extract_sogc_id(entry: dict[str, Any]) -> str | None:
    raw = entry.get("sogcId") or entry.get("id") or entry.get("sogcPubId")
    return str(raw).strip() if raw is not None else None


def _extract_pub_date(entry: dict[str, Any]) -> str | None:
    raw = entry.get("sogcDate") or entry.get("date") or entry.get("publicationDate")
    return str(raw)[:10] if raw else None


def _extract_pub_number(entry: dict[str, Any]) -> str | None:
    raw = entry.get("publicationNumber") or entry.get("number") or entry.get("pubNumber")
    return str(raw).strip() if raw else None


def _extract_mutation_keys(entry: dict[str, Any]) -> list[str]:
    mut = entry.get("mutationTypes") or []
    if isinstance(mut, list):
        return [str(m.get("key") or "") for m in mut if isinstance(m, dict)]
    return []


def _extract_text_fields(entry: dict[str, Any]) -> dict[str, str | None]:
    """Return {"de": ..., "fr": ..., "it": ..., "en": ...} from an entry.

    Tries several key shapes observed in Zefix API responses:
    1. publicationTexts.{de|fr|it|en}  (nested dict)
    2. sogcPubTexts: [{languageKey, text}]  (list of lang objects)
    3. text.{de|fr|it|en}  (nested dict under "text")
    4. textDe / textFr / textIt / textEn  (flat camelCase keys)
    """
    texts: dict[str, str | None] = {"de": None, "fr": None, "it": None, "en": None}

    pub_texts = entry.get("publicationTexts") or entry.get("text")
    if isinstance(pub_texts, dict):
        for lang in _LANG_ORDER:
            val = pub_texts.get(lang)
            if val and isinstance(val, str):
                texts[lang] = val.strip() or None
        if any(texts.values()):
            return texts

    sogc_pub_texts = entry.get("sogcPubTexts") or entry.get("publicationTextsList")
    if isinstance(sogc_pub_texts, list):
        for item in sogc_pub_texts:
            if not isinstance(item, dict):
                continue
            lang = str(item.get("languageKey") or item.get("language") or "").lower()
            text_val = item.get("text") or item.get("value") or ""
            if lang in texts and text_val:
                texts[lang] = str(text_val).strip() or None
        if any(texts.values()):
            return texts

    # Flat camelCase fallback
    for lang in _LANG_ORDER:
        key = f"text{lang.capitalize()}"
        val = entry.get(key)
        if val and isinstance(val, str):
            texts[lang] = val.strip() or None

    return texts


# ── Change detection ──────────────────────────────────────────────────────────

def _detect_changes(texts: dict[str, str | None]) -> list[dict[str, Any]]:
    """Detect change types via keyword matching across all available text languages.

    Returns list of {change_type, keywords_matched (JSON), raw_excerpt}.
    """
    detected: list[dict[str, Any]] = []
    seen_types: set[str] = set()

    for change_type, lang_keywords in CHANGE_PATTERNS.items():
        matched_keywords: list[str] = []
        excerpt: str | None = None

        for lang in _LANG_ORDER:
            text = texts.get(lang)
            if not text:
                continue
            text_lower = text.lower()
            keywords = lang_keywords.get(lang, [])
            for kw in keywords:
                if kw in text_lower:
                    matched_keywords.append(kw)
                    if excerpt is None:
                        # Find the sentence or chunk around the keyword
                        idx = text_lower.find(kw)
                        start = max(0, idx - 50)
                        end = min(len(text), idx + 200)
                        excerpt = text[start:end].strip()

        if matched_keywords and change_type not in seen_types:
            seen_types.add(change_type)
            detected.append({
                "change_type": change_type,
                "keywords_matched": json.dumps(list(dict.fromkeys(matched_keywords))),
                "raw_excerpt": excerpt,
            })

    return detected


# ── Per-company preprocessing ─────────────────────────────────────────────────

def _get_sogc_pub_json(company: Company) -> str | None:
    """Return the SOGC publication JSON for a company.

    Preference order:
    1. company.sogc_pub  — already extracted and stored as its own column
    2. sogcPub key inside company.zefix_raw  — fallback for companies imported
       before the sogc_pub column was populated
    """
    if company.sogc_pub:
        return company.sogc_pub
    if company.zefix_raw:
        try:
            raw = json.loads(company.zefix_raw)
            sogc_pub_raw = raw.get("sogcPub")
            if sogc_pub_raw is not None:
                return json.dumps(sogc_pub_raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def preprocess_company_sogc_pub(db: Session, company: Company) -> int:
    """Upsert sogc_publications + sogc_changes for one company.

    Returns the number of publication rows written (created or updated).
    Idempotent: existing rows are updated in-place; their sogc_changes are
    deleted and re-inserted.

    Data source: uses company.sogc_pub when available, falls back to
    extracting sogcPub from company.zefix_raw.
    """
    sogc_pub_json = _get_sogc_pub_json(company)
    if not sogc_pub_json:
        return 0

    entries = _parse_sogc_pub_entries(sogc_pub_json)
    if not entries:
        return 0

    written = 0
    for entry in entries:
        sogc_id = _extract_sogc_id(entry)
        if not sogc_id:
            logger.debug("SOGC entry for uid=%s has no sogc_id — skipped", company.uid)
            continue

        pub_number = _extract_pub_number(entry)
        mutation_keys = _extract_mutation_keys(entry)
        sub_rubric = _guess_sub_rubric(pub_number or "", mutation_keys)

        texts = _extract_text_fields(entry)

        # Apply encoding fix to all text fields
        encoding_fixed = False
        for lang in _LANG_ORDER:
            if texts[lang]:
                fixed, did_fix = _try_fix_encoding(texts[lang])  # type: ignore[arg-type]
                texts[lang] = fixed
                encoding_fixed = encoding_fixed or did_fix

        # Determine primary language: first non-null text, fall back to company language
        detected_language = next(
            (lang for lang in _LANG_ORDER if texts.get(lang)), None
        ) or company.purpose_language

        if not any(texts.values()):
            logger.debug(
                "SOGC entry sogc_id=%s for uid=%s has no text fields — storing raw only",
                sogc_id, company.uid,
            )

        # Upsert sogc_publications row
        existing = db.query(SogcPublication).filter_by(sogc_id=sogc_id).first()
        if existing:
            pub = existing
            pub.company_uid = company.uid
            pub.pub_date = _extract_pub_date(entry)
            pub.sub_rubric = sub_rubric or None
            pub.pub_number = pub_number
            pub.text_de = texts["de"]
            pub.text_fr = texts["fr"]
            pub.text_it = texts["it"]
            pub.text_en = texts["en"]
            pub.detected_language = detected_language
            pub.encoding_fixed = encoding_fixed
            pub.raw_json = json.dumps(entry)
        else:
            pub = SogcPublication(
                sogc_id=sogc_id,
                company_uid=company.uid,
                pub_date=_extract_pub_date(entry),
                sub_rubric=sub_rubric or None,
                pub_number=pub_number,
                text_de=texts["de"],
                text_fr=texts["fr"],
                text_it=texts["it"],
                text_en=texts["en"],
                detected_language=detected_language,
                encoding_fixed=encoding_fixed,
                raw_json=json.dumps(entry),
            )
            db.add(pub)
            db.flush()  # get pub.id

        # Re-insert changes
        db.query(SogcChange).filter_by(sogc_publication_id=pub.id).delete()
        changes = _detect_changes(texts)
        for ch in changes:
            db.add(SogcChange(
                sogc_publication_id=pub.id,
                change_type=ch["change_type"],
                keywords_matched=ch["keywords_matched"],
                raw_excerpt=ch["raw_excerpt"],
            ))

        pub.preprocessed_at = datetime.now(tz=timezone.utc)
        written += 1

    return written


# ── Batch job ─────────────────────────────────────────────────────────────────

def run_sogc_preprocess_batch(
    db: Session,
    *,
    mode: str = "missing",
    uids: list[str] | None = None,
    batch_size: int = 500,
    resume_from: int = 0,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict[str, Any]:
    """Batch-preprocess companies from sogc_pub or zefix_raw.sogcPub.

    Args:
        mode: "missing" — only companies with no rows yet in sogc_publications;
              "all" — reprocess every company regardless.
        uids: Optional list of CHE UIDs (e.g. ["CHE-123.456.789"]) to restrict
              processing to specific companies. When given, mode still applies
              (i.e. mode="missing" skips already-processed UIDs even in this list).
        batch_size: DB commit interval.
        resume_from: Skip the first N qualifying companies (for resumption).
                     Ignored when uids is provided (small targeted runs don't need it).
    """
    from sqlalchemy import or_, exists

    stats: dict[str, Any] = {
        "selected": 0,
        "processed": 0,
        "publications_written": 0,
        "skipped_no_pub": 0,
        "errors": [],
    }

    # Include companies that have sogc_pub OR a non-empty zefix_raw (fallback path)
    q = db.query(Company).filter(
        or_(Company.sogc_pub.isnot(None), Company.zefix_raw.isnot(None))
    )

    if uids:
        normalised = [u.strip() for u in uids if u and u.strip()]
        if normalised:
            q = q.filter(Company.uid.in_(normalised))

    if mode == "missing":
        already_done = db.query(SogcPublication.company_uid).distinct().subquery()
        q = q.filter(~exists().where(already_done.c.company_uid == Company.uid))

    # Cursor pagination: filter by id > last_id to avoid O(n²) OFFSET scans.
    # resume_from is treated as the last company.id already processed (not a row count).
    q = q.order_by(Company.id.asc())
    total = q.count()
    stats["selected"] = total

    if status_cb:
        status_cb(f"SOGC preprocess: {total} companies to process (mode={mode})")

    last_id: int = resume_from  # resume_from=0 means start from the beginning
    done = 0

    while True:
        if abort_cb:
            abort_cb()

        batch: list[Company] = q.filter(Company.id > last_id).limit(batch_size).all()
        if not batch:
            break

        for company in batch:
            if abort_cb:
                abort_cb()
            try:
                n = preprocess_company_sogc_pub(db, company)
                if n:
                    stats["processed"] += 1
                    stats["publications_written"] += n
                else:
                    stats["skipped_no_pub"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("SOGC preprocess failed uid=%s: %s", company.uid, exc, exc_info=True)
                stats["errors"].append(f"{company.uid}: {type(exc).__name__}: {exc}")
                try:
                    db.rollback()
                except Exception:
                    pass

        db.commit()
        last_id = batch[-1].id
        done += len(batch)

        if progress_cb:
            progress_cb(done, total, stats)

    if status_cb:
        status_cb(
            f"SOGC preprocess done — {stats['processed']} companies, "
            f"{stats['publications_written']} publications"
        )

    return stats
