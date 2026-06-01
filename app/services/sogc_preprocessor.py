"""Preprocessing pipeline for SOGC publication history.

Reads the `sogc_pub` JSON blob stored on each company (sourced from Zefix
`sogcPub` field) and explodes it into structured `sogc_publications` and
`sogc_changes` rows.

The Zefix company detail endpoint returns sogcPub entries with a single
`message` field (German, French, or Italian depending on the registry canton)
plus HTML-like <FT TYPE="..."> tags that are stripped on ingestion.

Encoding fix: some entries have UTF-8 bytes that were decoded as latin-1,
producing mojibake.  The fix tries encode("latin-1").decode("utf-8") and
keeps the result only when it both succeeds and differs from the original.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.clients.shab_client import _guess_sub_rubric
from app.models.company import Company
from app.models.sogc_change import SogcChange
from app.models.sogc_publication import SogcPublication

logger = logging.getLogger(__name__)

# ── Encoding fix ──────────────────────────────────────────────────────────────

def _try_fix_encoding(text: str) -> tuple[str, bool]:
    """Return (fixed_text, was_fixed).

    Interprets mojibake — UTF-8 bytes decoded as latin-1 or Windows-1252.
    Fast path: encode whole string as latin-1 then cp1252, decode as UTF-8.
    This covers e.g. 'Ã¤' → 'ä' (latin-1) and 'Ãœ' → 'Ü' (cp1252, where
    0x9C maps to U+0153 'œ' rather than the latin-1 control char).
    Partial-fix path handles mixed text (legitimate em-dashes etc. alongside
    mojibake) by processing cp1252-encodable runs independently.
    """
    if text.isascii():
        return text, False

    # Fast path — try latin-1 then cp1252 (Windows-1252 superset for 0x80-0x9F).
    for codec in ("latin-1", "cp1252"):
        try:
            fixed = text.encode(codec).decode("utf-8")
            return (fixed, True) if fixed != text else (text, False)
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    # Partial-fix path — text has characters not encodable even as cp1252
    # (e.g. em-dash U+2013 when the surrounding text is legitimate Unicode)
    # mixed with mojibake runs.  Process contiguous cp1252-encodable runs as
    # byte sequences; leave everything else untouched.
    result: list[str] = []
    run = bytearray()
    changed = False

    def _flush(run: bytearray) -> str:
        nonlocal changed
        if not run:
            return ""
        try:
            decoded = bytes(run).decode("utf-8")
            if decoded != bytes(run).decode("latin-1"):
                changed = True
            return decoded
        except UnicodeDecodeError:
            return bytes(run).decode("latin-1")

    for ch in text:
        try:
            run.extend(ch.encode("cp1252"))
        except (UnicodeEncodeError, ValueError):
            result.append(_flush(run))
            run = bytearray()
            result.append(ch)
    result.append(_flush(run))

    fixed = "".join(result)
    return (fixed, True) if changed else (text, False)


# ── HTML tag stripping ────────────────────────────────────────────────────────

_FT_TAG_RE = re.compile(r"</?FT[^>]*>", re.IGNORECASE)


def _strip_ft_tags(text: str) -> str:
    cleaned = _FT_TAG_RE.sub("", text)
    return html.unescape(cleaned).strip()


# ── Language detection ────────────────────────────────────────────────────────

def _lang_for_entry(msg: str, canton: str | None) -> str:
    """Detect publication language using lingua, with canton as fallback.

    lingua handles bilingual cantons (BE, FR, GR, VS) correctly by reading
    the actual text rather than relying on a fixed canton→language mapping.
    """
    try:
        from app.services.language_detection import detect_purpose_language
        detected = detect_purpose_language(msg)
        if detected in ("de", "fr", "it", "en"):
            return detected  # type: ignore[return-value]
    except Exception:
        pass
    # Canton fallback (only truly monolingual cantons are mapped).
    if canton:
        c = canton.upper()
        if c in ("GE", "JU", "NE", "VD"):
            return "fr"
        if c == "TI":
            return "it"
    return "de"


# ── Section-based change detection ───────────────────────────────────────────
#
# SOGC HR publications use labelled sections separated by ": ".  Each section
# header unambiguously identifies the change type; this avoids false-positive
# keyword matches such as "neu" appearing in "Zweck neu:" (purpose, not person).
#
# split=True sections (person lists) are split on ";" so each individual
# becomes its own sogc_changes row.

_SECTION_DEFS: list[tuple[re.Pattern, str, bool]] = [
    # ── German ───────────────────────────────────────────────────────────────
    (re.compile(r"ausgeschiedene\s+personen[^:]*:", re.I),  "person_removed", True),
    (re.compile(r"erloschene\s+unterschriften[^:]*:",  re.I), "person_removed", True),
    (re.compile(r"eingetretene\s+personen[^:]*:",      re.I), "person_added",   True),
    (re.compile(r"neue\s+unterschriften[^:]*:",        re.I), "person_added",   True),
    (re.compile(r"neu\s+bestellt[^:]*:",               re.I), "person_added",   True),
    (re.compile(r"eingetragene\s+personen[^:]*:",      re.I), "person_changed", True),
    (re.compile(r"mutierte\s+personen[^:]*:",          re.I), "person_changed", True),
    (re.compile(r"aktienkapital[^:.]{0,30}:",          re.I), "capital",        False),
    (re.compile(r"stammkapital[^:.]{0,30}:",           re.I), "capital",        False),
    (re.compile(r"stammanteile[^:.]{0,30}:",           re.I), "capital",        True),
    (re.compile(r"kapitalerhöhung[^:.]{0,30}:",        re.I), "capital",        False),
    (re.compile(r"sitz\s+verlegt[^:.]*:|neuer\s+sitz[^:.]*:|sitz[^:.]{0,20}:", re.I), "address", False),
    (re.compile(r"neue\s+adresse[^:.]*:",              re.I), "address",        False),
    (re.compile(r"domizil[^:.]{0,20}:",                re.I), "address",        False),
    (re.compile(r"zweck[^:.]{0,15}:",                  re.I), "purpose",        False),
    (re.compile(r"firma\s+neu[^:.]*:|neue\s+firma[^:.]*:|umfirmiert", re.I), "name", False),
    (re.compile(r"in\s+liquidation|auflösung[^:.]*:|gelöscht", re.I), "status", False),
    (re.compile(r"fusion[^:.]*:|fusionsvertrag[^:.]*:|verschmelzung[^:.]*:", re.I), "merger", False),
    (re.compile(r"übernahme[^:.]*:|übertragung[^:.]*:", re.I), "acquisition",   False),
    (re.compile(r"statutenänderung[^:.]*:",            re.I), "governance",     False),
    (re.compile(r"vinkulierung[^:.]*:",               re.I), "restriction",    False),
    (re.compile(r"mitteilungen[^:.]{0,30}:",          re.I), "governance",     False),
    # ── French ───────────────────────────────────────────────────────────────
    (re.compile(r"personnes?\s+démissionnaires?[^:]*:", re.I), "person_removed", True),
    (re.compile(r"signatures?\s+éteintes?[^:]*:",      re.I), "person_removed", True),
    (re.compile(r"nouveaux?\s+membres?[^:]*:",         re.I), "person_added",   True),
    (re.compile(r"nouvelles?\s+signatures?[^:]*:",     re.I), "person_added",   True),
    (re.compile(r"personnes?\s+nouvelles?\s+ou[^:]*:", re.I), "person_changed", True),
    (re.compile(r"inscriptions?\s+nouvelles?\s+(?:et\s+)?mutations?[^:]*:", re.I), "person_changed", True),
    (re.compile(r"inscription\s+ou\s+modification\s+de\s+personne[^:.]*:", re.I), "person_changed", True),
    (re.compile(r"radiation\s+de\s+personne[^:.]*:",   re.I), "person_removed", True),
    (re.compile(r"nouvelle\s+inscription\s+de\s+personne[^:.]*:", re.I), "person_added", True),
    (re.compile(r"nouveau\s+capital[-\s]?actions?[^:.]*:",  re.I), "capital",   False),
    (re.compile(r"nouvelles?\s+actions?[^:.]*:",            re.I), "capital",   False),
    (re.compile(r"capital\-?actions[^:.]{0,20}:|capital\s+social[^:.]{0,20}:", re.I), "capital", False),
    (re.compile(r"augmentation\s+(?:\w+\s+)?du\s+capital[^:.]*:", re.I), "capital", False),
    (re.compile(r"réduction\s+(?:\w+\s+)?du\s+capital[^:.]*:",    re.I), "capital", False),
    (re.compile(r"siège\s+social[^:.]*:|nouveau\s+siège[^:.]*:", re.I), "address", False),
    (re.compile(r"nouvelle\s+adresse[^:.]*:",          re.I), "address",        False),
    (re.compile(r"domicile[^:.]{0,20}:",               re.I), "address",        False),
    (re.compile(r"but\s+social[^:.]*:|but\s*:",        re.I), "purpose",        False),
    (re.compile(r"modification\s+des?\s+statuts[^:.]*:", re.I), "governance",   False),
    (re.compile(r"clause\s+d.agr[eé]ment[^:.]*:|restriction\s+de\s+transfert[^:.]*:", re.I), "restriction", False),
    (re.compile(r"communications?\s+aux\s+actionnaires?[^:.]*:|avis\s+aux\s+actionnaires?[^:.]*:", re.I), "governance", False),
    (re.compile(r"nouvelle\s+raison\s+sociale[^:.]*:", re.I), "name",           False),
    (re.compile(r"en\s+liquidation|dissolution[^:.]*:", re.I), "status",        False),
    (re.compile(r"fusion[^:.]*:",                      re.I), "merger",         False),
    # ── French — SHAB archive PDF format (compact section headers) ───────────
    # These section headers appear in SHAB PDF publications and differ from the
    # verbose headers used in SOGC/Zefix text (e.g. "personnes démissionnaires").
    (re.compile(r"personne\(s\)\s+et\s+signature\(s\)\s+radi[ée]{1,2}\(s\)\s*:", re.I), "person_removed", True),
    (re.compile(r"personne\(s\)\s+inscrite\(s\)\s+nouvelles?\s+ou\s+mutante\(s\)\s*:", re.I), "person_changed", True),
    (re.compile(r"personne\(s\)\s+inscrite\(s\)\s*:",        re.I), "person_added",   True),
    (re.compile(r"but\s+de\s+l.entreprise\s*:",              re.I), "purpose",        False),
    (re.compile(r"raison\s+sociale\s+(?:nouvelle|modifi[ée]e)[^:.]*:", re.I), "name", False),
    (re.compile(r"si[eè]ge\s+(?:social\s+)?(?:nouveau|modifi[ée])[^:.]*:", re.I), "address", False),
    # ── Italian ──────────────────────────────────────────────────────────────
    (re.compile(r"persone\s+uscenti[^:]*:|firme\s+estinte[^:]*:", re.I), "person_removed", True),
    (re.compile(r"nuovi\s+membri[^:]*:|nuove\s+firme[^:]*:",      re.I), "person_added",   True),
    (re.compile(r"persone\s+nuove\s+o[^:]*:",                     re.I), "person_changed", True),
    (re.compile(r"iscrizioni\s+mutate[^:]*:",                     re.I), "person_changed", True),
    (re.compile(r"capitale\s+azionario[^:.]{0,20}:|capitale\s+sociale[^:.]{0,20}:", re.I), "capital", False),
    (re.compile(r"quote\s+sociali[^:.]*:",             re.I), "capital",        True),
    (re.compile(r"sede\s+sociale[^:.]*:|nuova\s+sede[^:.]*:|sede[^:.]{0,10}:", re.I), "address", False),
    (re.compile(r"nuovo\s+indirizzo[^:.]*:",           re.I), "address",        False),
    (re.compile(r"scopo[^:.]{0,10}:",                  re.I), "purpose",        False),
    (re.compile(r"modifica\s+(?:dello\s+)?statuto[^:.]*:", re.I), "governance", False),
    (re.compile(r"vincol[oa]\s+(?:di|alla)\s+(?:trasferimento|trasmissibilità)[^:.]*:|clausola\s+di\s+gradimento[^:.]*:|restrizione\s+(?:alla|di)\s+trasfer[^:.]*:", re.I), "restriction", False),
    (re.compile(r"nuova\s+ditta[^:.]*:",               re.I), "name",           False),
    (re.compile(r"in\s+liquidazione|scioglimento[^:.]*:", re.I), "status",      False),
    (re.compile(r"fusione[^:.]*:",                     re.I), "merger",         False),
]

_LANG_ORDER = ("de", "fr", "it", "en")

# Detects correction publications (Berichtigung / Rectification / Rettifica).
# Checked against the original text BEFORE header stripping.
_CORRECTION_RE = re.compile(
    r"^\s*(?:Berichtigung|Rectification|Rettifica|Correction)\b",
    re.I,
)

# Strips the standard SOGC header line (company name, UID, form, SHAB ref) so
# that phrases in company names ("AG in Liquidation", "GmbH", etc.) never
# trigger false-positive section matches.
# Matches up to and including the closing "Publ. XXXXXXXX). " pattern.
_SOGC_HEADER_RE = re.compile(
    r"^.+?\((?:SHAB|No\.?\s*FOSC|FF\s*\d)[^)]+\)\.\s*",
    re.DOTALL | re.I,
)

# Strips "[nicht: ...]" / "[non: ...]" / "[no: ...]" brackets from correction
# publications — these contain the NEGATED (wrong) text and must not trigger
# false-positive keyword matches.
_NEGATION_BRACKET_RE = re.compile(
    r"\[\s*(?:nicht|non|no)\s*:[^\]]*\]",
    re.I | re.DOTALL,
)

# Detects bracketed content containing deletion-related keywords that indicate
# the change should be ignored (e.g., "Domizil neu: [Das Domizil wird im
# Handelsregister gelöscht.]" means the domicil is being deleted, not added).
_DELETION_BRACKET_RE = re.compile(
    r"\[(?:[^\]]*(?:"
    r"gelöscht|deleted|supprimé|cancellato"
    r"|aufgehoben|revoked|révoqué|revocato"
    r"|widerrufen|rescinded|annulé|annullato"
    r")[^\]]*)\]",
    re.I | re.DOTALL,
)

# Matches auditor/statutory-auditor keywords in person row excerpts.
# A person_removed/person_changed/person_added row that contains one of these
# terms gets a mirrored auditor_change row so auditor changes are queryable
# independently of general person changes.
_AUDITOR_KW_RE = re.compile(
    r"revisionsstelle"                          # DE
    r"|organe\s+de\s+r[eé]vision"              # FR
    r"|organe\s+de\s+contr[oô]le"              # FR alt
    r"|soci[eé]t[eé]\s+de\s+r[eé]vision"      # FR alt
    r"|ufficio\s+di\s+revisione"               # IT
    r"|organo\s+di\s+revisione"                # IT alt
    r"|societ[aà]\s+di\s+revisione",           # IT alt
    re.I,
)

# Sentence-level patterns for free-form insolvency/legal-proceeding sentences
# that have no section header colon.  Each match extracts the surrounding
# sentence as raw_excerpt.
_SENTENCE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # German — insolvency
    (re.compile(r"konkursverfahren[^.]*(?:eingestellt|eröffnet|aufgehoben|widerrufen)", re.I), "status"),
    (re.compile(r"mangels\s+aktiven", re.I), "status"),
    (re.compile(r"nachlassstundung", re.I), "status"),
    (re.compile(r"nachlassvertrag", re.I), "status"),
    (re.compile(r"schuldenruf", re.I), "status"),
    # German — audit exemption (match the key phrase; _sentence_containing grabs context)
    (re.compile(r"(?:eingeschränkte|ordentliche)\s+revision\b", re.I), "governance"),
    (re.compile(r"opting.out\b", re.I), "governance"),
    # French — capital sentence (no colon, e.g. "Augmentation ordinaire du capital-actions.")
    (re.compile(r"augmentation\s+(?:\w+\s+)?du\s+capital", re.I), "capital"),
    (re.compile(r"réduction\s+(?:\w+\s+)?du\s+capital", re.I), "capital"),
    # French — share transfer restriction embedded in content (not as section header)
    (re.compile(r"restriction\s+de\s+transmissibilité", re.I), "restriction"),
    # French — insolvency
    (re.compile(r"procédure\s+de\s+(?:faillite|liquidation)", re.I), "status"),
    (re.compile(r"sursis\s+concordataire", re.I), "status"),
    (re.compile(r"liquidation\s+(?:forcée|officielle)", re.I), "status"),
    # French — audit exemption
    (re.compile(r"contrôle\s+restreint\b", re.I), "governance"),
    (re.compile(r"renonce[^,;]{0,60}révision", re.I), "governance"),
    # Italian — insolvency
    (re.compile(r"procedura\s+(?:fallimentare|di\s+liquidazione)", re.I), "status"),
    (re.compile(r"concordato\s+preventivo", re.I), "status"),
    # Italian — audit exemption
    (re.compile(r"revisione\s+limitata\b", re.I), "governance"),
]


# Sentence boundary: "." not flanked on both sides by digits (avoids "1'000.00", dates like "01.12.")
# Also treats "]." and ")." as sentence endings.
_SENT_BOUNDARY_RE = re.compile(r"(?<!\d)\.(?!\d)\s+|[\])]\.(?=\s+[A-ZÀ-Ö\[])")


def _sentence_containing(text: str, match_start: int, match_end: int) -> str:
    before = text[:match_start]
    sent_start = 0
    for m in _SENT_BOUNDARY_RE.finditer(before):
        sent_start = m.end()
    after = text[match_end:]
    m2 = _SENT_BOUNDARY_RE.search(after)
    sent_end = match_end + m2.start() + 1 if m2 else len(text)
    return text[sent_start:sent_end].strip()


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

    Extraction order (first match wins):
    1. message  — single text from Zefix company detail endpoint; language
                  inferred from registryOfCommerceCanton
    2. publicationTexts.{de|fr|it|en} or text.{de|fr|it|en}  (nested dict)
    3. sogcPubTexts / publicationTextsList  (list of {languageKey, text})
    4. textDe / textFr / textIt / textEn  (flat camelCase)
    """
    texts: dict[str, str | None] = {"de": None, "fr": None, "it": None, "en": None}

    # Primary: Zefix company detail uses a single "message" field with FT tags
    msg = entry.get("message")
    if msg and isinstance(msg, str):
        msg = _strip_ft_tags(msg)
        if msg:
            lang = _lang_for_entry(msg, entry.get("registryOfCommerceCanton"))
            texts[lang] = msg
            return texts

    # Nested dict: publicationTexts.de / text.de
    pub_texts = entry.get("publicationTexts") or entry.get("text")
    if isinstance(pub_texts, dict):
        for lang in _LANG_ORDER:
            val = pub_texts.get(lang)
            if val and isinstance(val, str):
                texts[lang] = val.strip() or None
        if any(texts.values()):
            return texts

    # List form: sogcPubTexts / publicationTextsList
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

    # Flat camelCase: textDe / textFr / textIt / textEn
    for lang in _LANG_ORDER:
        val = entry.get(f"text{lang.capitalize()}")
        if val and isinstance(val, str):
            texts[lang] = val.strip() or None

    return texts


# ── Change detection ──────────────────────────────────────────────────────────

def _parse_sections(text: str) -> list[dict[str, Any]]:
    """Extract change entries from a single SOGC publication text.

    Finds all section headers defined in _SECTION_DEFS, determines the content
    of each section (text between this header and the next), and builds one
    sogc_changes dict per entry.

    Person sections (split=True) are split on ";" so each individual person
    becomes its own row.  This means two people leaving in one publication
    produce two person_removed rows, each with just that person's excerpt.
    """
    # Strip the standard SOGC header line (company name + SHAB ref) so that
    # phrases like "AG in Liquidation" in the company name don't trigger
    # false-positive section matches.
    # Flag correction publications before stripping the header
    is_correction = bool(_CORRECTION_RE.match(text))

    hdr_m = _SOGC_HEADER_RE.match(text)
    body = text[hdr_m.end():] if hdr_m else text

    # Remove "[nicht: ...]" / "[non: ...]" brackets so negated (wrong) text
    # in correction publications doesn't trigger false-positive matches.
    body = _NEGATION_BRACKET_RE.sub(" ", body)

    # Collect all section-header match positions
    hits: list[tuple[int, int, str, bool]] = []  # (start, end, change_type, split)
    for pattern, change_type, split in _SECTION_DEFS:
        for m in pattern.finditer(body):
            hits.append((m.start(), m.end(), change_type, split))

    hits.sort(key=lambda h: h[0])

    results: list[dict[str, Any]] = []
    if is_correction:
        results.append({
            "change_type": "correction",
            "keywords_matched": "berichtigung",
            "raw_excerpt": body[:200].strip(),
        })
    for i, (start, end, change_type, split) in enumerate(hits):
        header = body[start:end].rstrip(": \t")
        # Content runs to the next section header or end of body
        next_start = hits[i + 1][0] if i + 1 < len(hits) else len(body)
        content = body[end:next_start].strip().rstrip(". \t\n")

        if not content:
            continue

        if split:
            # Split on ";" to get one row per individual (person, signatory)
            entries = [e.strip().rstrip(".") for e in content.split(";") if e.strip()]
        else:
            entries = [content]

        for entry_text in entries:
            if entry_text:
                # Skip entries that have deletion-related keywords in brackets
                # (e.g., "Domizil neu: [Das Domizil wird gelöscht]" → ignore)
                if _DELETION_BRACKET_RE.search(entry_text):
                    continue
                results.append({
                    "change_type": change_type,
                    "keywords_matched": header.lower(),
                    "raw_excerpt": entry_text,
                })

    # Sentence-level patterns for free-form text with no section-header colon
    # (e.g. insolvency proceedings described in plain prose).
    seen_excerpts: set[tuple[str, str]] = {(r["change_type"], r["raw_excerpt"]) for r in results}
    for pattern, change_type in _SENTENCE_PATTERNS:
        for m in pattern.finditer(body):
            excerpt = _sentence_containing(body, m.start(), m.end())[:300]
            key = (change_type, excerpt)
            if key not in seen_excerpts:
                seen_excerpts.add(key)
                results.append({
                    "change_type": change_type,
                    "keywords_matched": m.group(0).lower(),
                    "raw_excerpt": excerpt,
                })

    # Mirror any person row that mentions an auditor keyword as auditor_change
    # so auditor changes are independently queryable.
    for r in list(results):
        if r["change_type"] in ("person_removed", "person_changed", "person_added"):
            if _AUDITOR_KW_RE.search(r.get("raw_excerpt") or ""):
                kw_m = _AUDITOR_KW_RE.search(r["raw_excerpt"])
                key = ("auditor_change", r["raw_excerpt"])
                if key not in seen_excerpts:
                    seen_excerpts.add(key)
                    results.append({
                        "change_type": "auditor_change",
                        "keywords_matched": kw_m.group(0).lower(),
                        "raw_excerpt": r["raw_excerpt"],
                    })

    return results


def _detect_changes(texts: dict[str, str | None]) -> list[dict[str, Any]]:
    """Detect changes from all available language texts.

    Runs _parse_sections on each non-null text.  Deduplicates by
    (change_type, raw_excerpt) in case the same text is stored in multiple
    language columns.
    """
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for lang in _LANG_ORDER:
        text = texts.get(lang)
        if not text:
            continue
        for ch in _parse_sections(text):
            key = (ch["change_type"], ch["raw_excerpt"])
            if key not in seen:
                seen.add(key)
                results.append(ch)
    return results


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
            pub.company_id = company.id
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
                company_id=company.id,
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


# ── Publications-only backfill ────────────────────────────────────────────────

def run_sogc_publications_backfill(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict:
    """Re-process existing sogc_publications rows: fix encoding and regenerate changes.

    Iterates over sogc_publications with cursor pagination, applies the encoding
    fix to any stored text that has mojibake, persists the corrected text back to
    the publication, then deletes + re-inserts the sogc_changes rows.

    Idempotent — safe to run multiple times.
    """
    stats: dict = {
        "selected": 0,
        "processed": 0,
        "changes_written": 0,
        "encoding_fixed": 0,
        "errors": [],
    }

    q = db.query(SogcPublication).order_by(SogcPublication.id.asc())
    total = q.count()
    stats["selected"] = total

    if status_cb:
        status_cb(f"SOGC changes backfill: {total} publications to process")

    last_id: int = resume_from
    done = 0

    while True:
        if abort_cb:
            abort_cb()

        batch: list[SogcPublication] = q.filter(SogcPublication.id > last_id).limit(batch_size).all()
        if not batch:
            break

        for pub in batch:
            if abort_cb:
                abort_cb()
            try:
                # Prefer re-extracting from raw_json so language placement and
                # encoding are fully redone with current logic.  Fall back to
                # the stored text columns when raw_json is absent.
                if pub.raw_json:
                    try:
                        entry = json.loads(pub.raw_json)
                        texts = _extract_text_fields(entry)
                    except (json.JSONDecodeError, TypeError):
                        entry = {}
                        texts = {"de": pub.text_de, "fr": pub.text_fr, "it": pub.text_it, "en": pub.text_en}
                else:
                    texts = {"de": pub.text_de, "fr": pub.text_fr, "it": pub.text_it, "en": pub.text_en}

                encoding_fixed = False
                for lang in _LANG_ORDER:
                    if texts[lang]:
                        fixed, did_fix = _try_fix_encoding(texts[lang])  # type: ignore[arg-type]
                        texts[lang] = fixed
                        encoding_fixed = encoding_fixed or did_fix

                detected_language = next(
                    (lang for lang in _LANG_ORDER if texts.get(lang)), None
                ) or pub.detected_language

                pub.text_de = texts["de"]
                pub.text_fr = texts["fr"]
                pub.text_it = texts["it"]
                pub.text_en = texts["en"]
                pub.detected_language = detected_language
                pub.encoding_fixed = encoding_fixed
                if encoding_fixed:
                    stats["encoding_fixed"] += 1

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
                stats["processed"] += 1
                stats["changes_written"] += len(changes)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SOGC changes backfill failed pub.id=%s: %s", pub.id, exc, exc_info=True
                )
                stats["errors"].append(f"pub_id={pub.id}: {type(exc).__name__}: {exc}")
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
            f"SOGC changes backfill done — {stats['processed']} publications, "
            f"{stats['changes_written']} changes, {stats['encoding_fixed']} encoding fixes"
        )

    return stats


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
    from sqlalchemy.orm import load_only

    stats: dict[str, Any] = {
        "selected": 0,
        "processed": 0,
        "publications_written": 0,
        "skipped_no_pub": 0,
        "errors": [],
    }

    # Only load the columns preprocess_company_sogc_pub actually reads —
    # avoids pulling zefix_raw (large JSONB), google_search_results_raw, etc.
    q = db.query(Company).options(load_only(
        Company.id,
        Company.uid,
        Company.canton,
        Company.purpose_language,
        Company.sogc_pub,
        Company.zefix_raw,
    )).filter(
        or_(Company.sogc_pub.isnot(None), Company.zefix_raw.isnot(None))
    )

    if uids:
        normalised = [u.strip() for u in uids if u and u.strip()]
        if normalised:
            q = q.filter(Company.uid.in_(normalised))

    if mode == "missing":
        # Direct correlated NOT EXISTS so PostgreSQL can use the
        # ix_sogc_pub_uid_date index on company_uid rather than materialising
        # a full DISTINCT subquery across all sogc_publications rows.
        q = q.filter(
            ~exists().where(SogcPublication.company_uid == Company.uid)
        )

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
