from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session

from app import crud
from app.models.company import Company
from app.schemas.company import CompanyUpdate


_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]{3,}")
_SENTENCE_SPLIT = re.compile(r"(?<=\.)\s+(?=[A-ZÄÖÜ])")

# Compact stopword set for classification token quality.
_NOGA_STOPWORDS: set[str] = {
    "die", "der", "das", "und", "oder", "mit", "von", "für", "des", "dem", "den",
    "ein", "eine", "einer", "eines", "sich", "auf", "zu", "ist", "sowie", "als",
    "auch", "nicht", "nach", "bei", "alle", "durch", "wird", "im", "an", "am",
    "company", "companies", "services", "general", "related", "activities",
    "gesellschaft", "gesellschaften", "zweck", "bezweckt", "dienstleistungen", "dienstleistung",
    "erbringung", "tätigkeit", "tätigkeiten", "handel", "waren", "art", "insbesondere",
}


@dataclass(frozen=True)
class NogaClassification:
    code: str
    label: str | None
    level: str | None
    confidence: float


@dataclass(frozen=True)
class _NogaIndex:
    token_to_codes: dict[str, set[str]]
    code_meta: dict[str, dict[str, Any]]



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]



def _normalize_text(value: str) -> str:
    return value.strip().lower()



def _collect_multilang_text(v: Any) -> list[str]:
    if isinstance(v, str):
        txt = v.strip()
        return [txt] if txt else []
    if isinstance(v, dict):
        out: list[str] = []
        for k in ("de", "fr", "it", "en", "rm"):
            raw = v.get(k)
            if isinstance(raw, str) and raw.strip():
                out.append(raw.strip())
        if out:
            return out
        for raw in v.values():
            if isinstance(raw, str) and raw.strip():
                out.append(raw.strip())
        return out
    return []



def _tokens_from_texts(texts: list[str]) -> set[str]:
    tokens: set[str] = set()
    for t in texts:
        for m in _WORD_RE.findall(t.lower()):
            tok = m.strip().lower()
            if not tok or tok in _NOGA_STOPWORDS:
                continue
            tokens.add(tok)
    return tokens


def _strip_purpose_boilerplate(text: str, patterns: list[re.Pattern]) -> str:
    if not text or len(text) < 40 or not patterns:
        return text

    sentences = _SENTENCE_SPLIT.split(text.strip())
    kept = [
        s for s in sentences
        if s.strip() and not any(pat.search(s) for pat in patterns)
    ]
    result = " ".join(kept).strip()
    return result if result else text



def _extract_node_tokens(node: dict[str, Any]) -> set[str]:
    texts: list[str] = []

    texts.extend(_collect_multilang_text(node.get("name")))

    for ann in node.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        texts.extend(_collect_multilang_text(ann.get("text")))

    return _tokens_from_texts(texts)


@lru_cache(maxsize=1)
def _load_noga_index() -> _NogaIndex:
    lookup_path = _repo_root() / "noga_lookup.json"
    if not lookup_path.exists():
        raise FileNotFoundError(
            f"NOGA lookup not found at {lookup_path}. Run scripts/create_noga_json.py once first."
        )

    with lookup_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise ValueError("noga_lookup.json must be a dict mapping code -> node")

    token_to_codes: dict[str, set[str]] = {}
    code_meta: dict[str, dict[str, Any]] = {}

    for code, raw_node in payload.items():
        if not isinstance(raw_node, dict):
            continue

        node = dict(raw_node)
        node["code"] = str(code)
        code_meta[str(code)] = node

        for tok in _extract_node_tokens(node):
            token_to_codes.setdefault(tok, set()).add(str(code))

    return _NogaIndex(token_to_codes=token_to_codes, code_meta=code_meta)



def _company_tokens(db: Session, company: Company) -> set[str]:
    texts: list[str] = []
    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)

    if company.name:
        texts.append(company.name)
    if company.purpose:
        texts.append(_strip_purpose_boilerplate(company.purpose, boilerplate_patterns))
    if company.purpose_keywords:
        texts.append(company.purpose_keywords.replace(",", " "))
    if company.tfidf_cluster:
        texts.append(company.tfidf_cluster.replace("|", " ").replace(",", " "))

    return _tokens_from_texts(texts)



def classify_company_noga(db: Session, company: Company) -> NogaClassification | None:
    tokens = _company_tokens(db, company)
    if not tokens:
        return None

    idx = _load_noga_index()

    scores: dict[str, float] = {}
    for tok in tokens:
        for code in idx.token_to_codes.get(tok, set()):
            scores[code] = scores.get(code, 0.0) + 1.0

    if not scores:
        return None

    # Prefer deepest level (Art=6 digits) when scores are similar.
    ranked = sorted(
        scores.items(),
        key=lambda kv: (kv[1], len(kv[0]) == 6, len(kv[0]) == 4, len(kv[0]) == 3, len(kv[0]) == 2, len(kv[0]) == 1, kv[0]),
        reverse=True,
    )
    best_code, best_score = ranked[0]
    total = sum(scores.values())
    confidence = float(best_score / total) if total > 0 else 0.0

    meta = idx.code_meta.get(best_code, {})
    name = meta.get("name")
    label = None
    if isinstance(name, dict):
        label = name.get("de") or name.get("fr") or name.get("it") or name.get("en")
    elif isinstance(name, str):
        label = name

    level = meta.get("level") if isinstance(meta.get("level"), str) else None
    return NogaClassification(code=best_code, label=label, level=level, confidence=confidence)



def apply_noga_classification(db: Session, company: Company) -> CompanyUpdate | None:
    result = classify_company_noga(db, company)
    if not result:
        return None

    return CompanyUpdate(
        noga_code=result.code,
        noga_label=result.label,
        noga_level=result.level,
        noga_confidence=result.confidence,
        noga_classified_at=datetime.now(tz=timezone.utc),
    )
