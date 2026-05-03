"""Thin facade — re-exports all pipeline functions for backward compatibility.

The implementation has been split into focused modules:
  zefix_import.py        — Zefix API fetch, bulk import, detail collect
  web_enrichment.py      — Google search enrichment, batch collect
  geocoding_pipeline.py  — Geocoding and flex-score recalculation
  noga_pipeline.py       — NOGA industry classification
  claude_classify.py     — Claude Haiku AI scoring
  language_detection.py  — Purpose language detection (bulk)
"""

from __future__ import annotations

import re

# ── Shared data / utilities kept here ────────────────────────────────────────
# _TFIDF_STOPWORDS is imported by app.crud.google_scoring_filter — keep in this module.

_TFIDF_STOPWORDS: set[str] = {
    # Generic activity words — too broad to form meaningful clusters
    "erbringung", "dienstleistungen", "dienstleistung", "leistungen", "leistung",
    "waren", "ware",
    "tätigkeiten", "tätigkeit", "aktivitäten", "aktivität",
    "verwaltung", "führung", "betreuung",
    "bereich", "bereiche", "bereichen", "gebiet", "gebiete", "gebieten",
    "erwerb", "erwerben", "veräusserung", "veräussern",
    "beteiligung", "beteiligungen", "beteiligen", "halten", "verwalten", "betreiben",
    "erbringen", "anbieten", "durchführen", "ausführen",
    "ausführung", "ausführungen",
    "art",
    "übernahme", "übernahmen",
    "vertretungen", "vertretung",
    "zubehör",
    "dazugehörig", "dazugehörigen", "dazugehörigem",
    "darlehen", "immaterialgüter", "immaterialgüt", "anderer", "zusammenhängen", "bezwecken",
    "einschliesslich", "einschließlich", "einschl", "ähnliche", "ähnlichen", "weitere", "weiteren", "entsprechende",
    "jeglicher", "zusammenhängende", "zusammenhängenden", "zusammenhängendem", "weiterveräussern",
    "dritter", "dritten", "weit",
    "die", "der", "das", "und", "oder", "mit", "von", "für", "des", "dem",
    "den", "ein", "eine", "einer", "eines", "sich", "auf", "zu", "ist",
    "sowie", "als", "auch", "nicht", "nach", "bei", "alle", "durch", "wird",
    "deren", "diese", "dieser", "dieses", "sie", "ihr", "ihren", "ihres",
    "haben", "hat", "hatte", "werden", "war", "sind", "sein",
    "im", "an", "am", "ab", "um", "bis", "vor", "aus", "über", "unter",
    "zum", "zur", "beim", "vom", "ans", "ins", "er", "es", "wir",
    "ihm", "ihn", "ihnen", "uns", "man", "kein", "keine",
    "diesem", "diesen", "solche", "solcher", "welche", "welcher",
    "jede", "jeder", "jedes", "aller", "allem", "allen",
    "jedoch", "daher", "dabei", "dazu", "davon", "darüber", "dafür",
    "soweit", "sowohl", "darunter", "hierzu", "hierbei", "hierfür", "bzgl", "bzw",
    "etc", "usw", "inklusive", "inkl", "exklusive", "exkl", "anderen", "anderer", "anderes",
    "beispielsweise", "z.B", "zB", "u.a", "ua", "namentlich", "hauptsächlich", "vorzugsweise",
    "allgemein", "allgemeine", "allgemeinen", "sonstige", "sonstigen",
    "eigen", "eigene", "eigenen",
    "zusammenhängend", "zusammenhängendem", "zusammenhängende", "zusammenhängenden",
    "raiffeisen", "raiffeisenbank", "raiffeisenbanken", "sämtlicher", "sämtlichen", "sämtliche",
    "zusammen", "zusammenschliessen", "zusammenschluss", "zusammenschlüssen", "schliessen",
    # ── Swiss registry standard boilerplate ──────────────────────────────────
    "kann", "errichten", "anderen", "andern", "geschäfte", "geschäftstätigkeit", "geschäftstätigkeiten",
    "tätigen", "direkt", "indirekt", "ihrem", "zusammenhang", "stehen",
    "grundeigentum", "grundstück", "grundstücke", "belasten", "finanzierungen", "eigene", "fremde", "rechnung",
    "vornehmen", "garantien", "bürgschaften", "dritte", "eingehen",
    "tochtergesellschaft", "tochtergesellschaften",
    "zweigniederlassung", "zweigniederlassungen", "niederlassung", "niederlassungen",
    "inland", "ausland", "verbundenen",
    "liegenschaften", "liegenschaft",
    "fördern", "fördert", "förderung",
    "geeignet", "geeignete", "geeigneten",
    "gesellschaftszweck", "zwecksetzung",
    "gleicher", "gleiche", "gleichen",
    "ähnlicher", "unternehmungen", "ferner",
    "bezweckt",
    "gleichartige", "gleichartiger", "gleichartigen",
    "verwandte", "verwandten", "verwandter",
    "solchen", "zusammenschliessen",
    "verträge", "vertrag", "abschliessen",
    "sicherheiten", "zugunsten", "gewähren", "übernehmen", "damit",
    "sämtliche", "sämtlichen",
    "innmaterialgüterrechte", "unternehmens",
    "fiduziare", "fiduziar", "fiduziaren",
    "übereignung", "übertragung", "übertragen",
    "pfandrecht", "pfandrechte", "verpfändung", "verpfänden",
    "mittels",
    "aktiven", "passiven", "beteiligungen", "beteiligen",
    "aktionär", "aktionäre",
    "fremd", "finanzierung", "geschäft",
    "gesellschaft", "gesellschaften", "gesellschafts", "unternehmen", "betrieb", "zweck", "zwecks",
    "aktien", "gmbh", "ag", "sarl", "sàrl", "cie", "co", "inc",
    "insbesondere",
    "verschiedene", "verschiedenen", "verschiedenste",
    "konzern", "konzerne", "gruppe", "gruppen",
    "hauptsitz", "sitz", "domizil", "domizile",
    "schweiz", "schweizer", "schweizerische", "schweizerischen",
    "europa", "europäische", "europäischen",
    "weltweit", "international", "global", "national",
    "urheberrecht", "markenrecht", "patentrecht", "designrecht", "immaterialgüterrecht",
    "patent", "zweigniederlassunge", "tochtergesellschafte", "beteiligunge",
    # ── French ───────────────────────────────────────────────────────────────
    "les", "une", "est", "dans", "par", "sur", "aux",
    "de", "la", "le", "et", "en", "du", "au", "avec", "qui", "que",
    "se", "son", "sa", "ses", "toute", "tous", "toutes",
    "il", "ils", "elle", "elles", "nous", "vous", "leur", "leurs",
    "ce", "cet", "cette", "ces", "ou", "mais", "donc",
    "pour", "pas", "plus", "comme", "aussi",
    "notamment", "ainsi", "dont", "afin", "selon",
    # ── Italian ──────────────────────────────────────────────────────────────
    "di", "il", "e", "del", "della", "dello", "dei", "delle",
    "un", "una", "su", "con", "per", "al", "alla", "alle", "ai",
    "che", "sono", "ed", "ha", "hanno", "si", "da", "dal",
    "dalla", "dai", "dagli", "tra", "fra", "lo", "gli",
    "ne", "ci", "non", "anche", "come", "tutti", "ogni",
    # ── English ──────────────────────────────────────────────────────────────
    "the", "and", "of", "in", "for", "to", "a", "an", "with", "its",
    "as", "by", "at", "from", "or", "be", "is", "are", "was", "were",
    "have", "has", "had", "will", "can", "all", "any",
    "other", "such", "their", "this", "that", "these", "those",
    "including", "related", "services", "company", "activities",
    "general", "various", "especially", "particular",
    # ── Common bigrams/phrases ────────────────────────────────────────────────
    "handel mit waren", "mit waren aller art", "erbringung von dienstleistungen",
    "dienstleistungen aller art", "handel mit waren aller art",
    "fremd garantie", "garantie bürgschaft", "bürgschaft dritter",
    "bürgschaft", "garantie", "können",
    "anderer geschäft", "verbunden abgeben", "fremd sicherheit",
    "sicherheit verbindlichkeit", "verbindlichkeit verbunden",
    "abgeben", "verbindlichkeit", "verbunden",
    "übrig vgl", "übrig können", "übrig kommerziell", "übrig immaterialgüterrecht",
    "immaterialgüterrecht", "ausländisch", "übrig geschäft", "übrig finanzierung", "übrig",
    "vornahme finanzierung", "eingehung bürgschaft", "finanzierung eingehung",
    "vornahme", "eingehung", "belastung vornahme", "belastung",
    "genossenschaft", "anderer gleichartig", "verwandt zusammenschliess",
    "gleichartig verwandt", "zusammenschliess", "gleichartig", "verwandt",
    "wert vermitteln", "immateriell wert", "anderer immateriell", "wert",
    "überhaupt geschäft", "lizenz patent", "aufnehmen garantie", "darlehen aufnehmen",
    "garantie anderer", "stellen immaterialgüterrecht", "aufnehmen", "stiftung",
    "bezwecken handel", "kredit", "sicherheit", "gegenüber dritter",
    "verbindlichkeit gegenüber", "hauptzweck erzielen", "synergie hauptzweck",
    "synergie", "erzielen", "geschäft synergie", "hauptzweck", "erzielen können",
    "führen", "schutzrecht", "weiterveräussern geschäft", "unternehmung vorkehren",
    "vorkehren", "dienen", "dienen können", "anderer unternehmung", "unternehmung",
    "unternehmung gleichartig", "konzerngesellschaft dritter", "konzerngesellschaft",
    "aktionär konzerngesellschaft", "finanzierung sanierung", "verpflichtung sicherheit",
    "darlehen verpflichtung", "gunst", "fiduziarisch jeglicher", "geschäft entwickeln",
    # ── Holding/intercompany boilerplate ─────────────────────────────────────
    "überdies", "entgeltlich", "unentgeltlich",
    "personen", "person", "zudem", "daran", "zwar",
    "ohne", "gegenleistung", "zinslos",
    "ausschluss", "gewinnerzielungsabsicht",
    "klumpenrisiko", "klumpenrisiken",
    "gruppengesellschaft", "gruppengesellschaften",
    "liquiditätsausgleich", "liquiditätsausgleiche",
    "nettoliquiditätszentralisierung", "nettoliquiditätszentralisierungen",
    "cashpooling", "cash-pooling",
    "periodisch", "periodische", "periodischer", "periodischen",
    "saldoanpassung", "saldoanpassungen", "balancing",
    "vorzugskondition", "vorzugskonditionen",
    "kommerziell", "kommerzielle", "kommerziellen",
    "finanziell", "finanzielle", "finanziellen",
}

_SENTENCE_SPLIT = re.compile(r'(?<=\.)\s+(?=[A-ZÄÖÜ])')


def strip_purpose_boilerplate(text: str, patterns: list) -> str:
    """Remove boilerplate sentences from purpose text using DB-loaded patterns.

    Alias for noga._strip_purpose_boilerplate — kept here for backward compat.
    """
    from app.services.noga import _strip_purpose_boilerplate
    return _strip_purpose_boilerplate(text, patterns)


# ── Re-exports from split modules ─────────────────────────────────��──────────

from app.services.zefix_import import (  # noqa: E402
    bulk_import_zefix,
    enrich_company,
    import_company_from_zefix_uid,
    initial_collect,
    reextract_purpose_from_zefix_raw,
    run_zefix_detail_collect,
    _extract_company_fields,
    _extract_purpose_from_raw,
    _is_control_signal_exception,
    _sleep_with_abort,
)

from app.services.web_enrichment import (  # noqa: E402
    enrich_company_website,
    recalculate_google_scores,
    rescore_from_stored_results,
    run_batch_collect,
    _google_search_ready,
    _google_scoring_overrides,
)

from app.services.geocoding_pipeline import (  # noqa: E402
    geocode_and_update_company,
    re_geocode_all_companies,
    recalculate_flex_scores,
)

from app.services.noga_pipeline import (  # noqa: E402
    reclassify_low_confidence_noga,
    reclassify_noga,
)

from app.services.claude_classify import (  # noqa: E402
    claude_classify_batch,
    resume_claude_batch,
    _DEFAULT_CLAUDE_PROMPT,
)

from app.services.language_detection import detect_language_bulk  # noqa: E402

__all__ = [
    # Data constants
    "_TFIDF_STOPWORDS",
    "_SENTENCE_SPLIT",
    "strip_purpose_boilerplate",
    # Zefix import
    "bulk_import_zefix",
    "enrich_company",
    "import_company_from_zefix_uid",
    "initial_collect",
    "reextract_purpose_from_zefix_raw",
    "run_zefix_detail_collect",
    "_extract_company_fields",
    "_extract_purpose_from_raw",
    # Web enrichment
    "enrich_company_website",
    "recalculate_google_scores",
    "rescore_from_stored_results",
    "run_batch_collect",
    "_google_search_ready",
    # Geocoding
    "geocode_and_update_company",
    "re_geocode_all_companies",
    "recalculate_flex_scores",
    # NOGA
    "reclassify_noga",
    "reclassify_low_confidence_noga",
    # Claude
    "claude_classify_batch",
    "resume_claude_batch",
    # Language
    "detect_language_bulk",
]
