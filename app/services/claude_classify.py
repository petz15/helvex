"""Claude Haiku batch classification for company lead scoring."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import crud
from app.models.company import Company
from app.services.claude import (
    _system_param as _claude_system_param,
    claude_batch_create,
    claude_batch_iter_results,
    claude_call,
    resolve_claude_api_key,
    strip_fences,
)
from app.services.noga import _strip_purpose_boilerplate
from app.services.scoring import distance_to_origin_km, get_default_scoring_config

logger = logging.getLogger(__name__)

_DEFAULT_CLAUDE_PROMPT = """\
You are evaluating Swiss company register (Zefix) entries as B2B sales leads.

Each entry includes the company name, purpose text, \
purpose keywords (TF-IDF terms from the company's own text), cluster labels \
(segment groups derived from similar companies), and optionally a website URL \
and Google score (0–100 confidence that the found website belongs to this company).

Use ALL provided fields together. The keywords and cluster labels are pre-computed \
hints — let the purpose text be the primary signal when they conflict. \
A high Google score (≥60) suggests the company has an active, findable web presence \
which is a mild positive signal. A missing website or low Google score is neutral — \
many legitimate SMEs are not indexed.

Output ONLY a JSON object (no markdown, no explanation) with exactly two fields:
- "score": integer 0–100
- "category": short English label (e.g. "SaaS", "Industrial Machinery", "Accounting", "E-Commerce")

Scoring guidance — spread scores across the full range:
- 85–100: Strong lead. Active SME clearly operating in the target space.
- 60–84:  Relevant but not ideal. Adjacent industry or mixed activities.
- 35–59:  Marginal. Some overlap but mostly outside the target.
- 10–34:  Weak. Different industry, very generic, or unclear purpose.
- 0–9:    Irrelevant. Holding company, dormant entity, non-commercial association.

Do NOT cluster scores in the 40–70 band. Use the full range so leads can be ranked meaningfully.\
"""


def _load_scoring_config_for_org(db: Session, org_id: int | None) -> dict[str, str]:
    defaults = get_default_scoring_config()
    return {key: crud.get_effective_setting(db, key, org_id=org_id, default=val) for key, val in defaults.items()}


def claude_classify_batch(
    db: Session,
    *,
    canton: str | None = None,
    min_flex_score: int | None = None,
    max_flex_score: int | None = None,
    min_web_score: int | None = None,
    purpose_keywords: str | None = None,
    tfidf_cluster: str | None = None,
    rerun_classified: bool = False,
    auto_filter_keywords: bool = False,
    use_fixed_categories: bool = False,
    limit: int = 500,
    system_prompt: str | None = None,
    target_description: str | None = None,
    api_key: str | None = None,
    org_id: int | None = None,
    resume_from: int = 0,
    use_batch_api: bool = False,
    submit_only: bool = False,
    companies_per_message: int = 1,
    progress_cb: Any = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Classify companies using Claude Haiku and store a lead-quality score + category.

    ``companies_per_message`` controls how many companies are packed into a single
    API call. Higher values (e.g. 10–20) amortise the system prompt across multiple
    companies, reducing input token costs. The system prompt is sent with
    ``cache_control: ephemeral`` so repeated identical prompts are served from cache.

    ``use_batch_api=True`` submits all requests as a single Anthropic Message Batch
    (50% discount on top).
    """
    if not api_key:
        return {
            "classified": 0,
            "skipped": 0,
            "errors": ["Anthropic API key not configured. Set ANTHROPIC_API_KEY in .env"],
            "input_tokens": 0,
            "output_tokens": 0,
        }

    stats: dict[str, Any] = {"classified": 0, "skipped": 0, "errors": [], "input_tokens": 0, "output_tokens": 0}
    if dry_run:
        stats["preview_results"] = []

    base_prompt = (system_prompt or "").strip() or _DEFAULT_CLAUDE_PROMPT
    if target_description and target_description.strip():
        prompt = base_prompt + f"\n\nWhat we are looking for: {target_description.strip()}"
    else:
        prompt = base_prompt

    companies_per_message = max(1, companies_per_message)

    if companies_per_message > 1:
        prompt = (
            prompt
            + '\n\nYou will receive multiple companies separated by "---".'
            + " Output ONLY a JSON array with one object per company in the same order,"
            + ' each with "score" (0-100) and "category".'
        )

    scoring_config = _load_scoring_config_for_org(db, org_id)
    origin_lat = float(scoring_config.get("scoring_origin_lat", 46.9266))
    origin_lon = float(scoring_config.get("scoring_origin_lon", 7.4817))
    max_purpose_chars = int(scoring_config.get("scoring_claude_max_purpose_chars") or 800)

    fixed_categories: list[str] = []
    fixed_categories_lc: dict[str, str] = {}
    if use_fixed_categories:
        raw_cats = (crud.get_effective_setting(db, "claude_classify_categories", org_id=org_id, default="") or "").strip()
        if raw_cats:
            fixed_categories = [c.strip() for c in raw_cats.replace("\n", ",").split(",") if c.strip()]
            if fixed_categories:
                fixed_categories_lc = {c.lower(): c for c in fixed_categories}
                cat_str = ", ".join(fixed_categories)
                prompt = prompt + f'\n\nUse ONLY one of these categories (exact match): {cat_str}'

    def _coerce_category(raw: object) -> str | None:
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        if not fixed_categories_lc:
            return s[:128]
        canon = fixed_categories_lc.get(s.lower())
        if canon is not None:
            return canon[:128]
        cleaned = s.strip(" .,;:'\"").lower()
        canon = fixed_categories_lc.get(cleaned)
        return canon[:128] if canon else None

    query = db.query(Company).filter(Company.purpose.isnot(None))
    if not rerun_classified:
        query = query.filter(Company.ai_score.is_(None))
    if canton:
        query = query.filter(Company.canton == canton)
    if min_flex_score is not None:
        query = query.filter(Company.flex_score >= min_flex_score)
    if max_flex_score is not None:
        query = query.filter(Company.flex_score <= max_flex_score)
    if min_web_score is not None:
        query = query.filter(Company.web_score >= min_web_score)
    if purpose_keywords:
        query = query.filter(Company.purpose_keywords.ilike(f"%{purpose_keywords}%"))
    if tfidf_cluster:
        query = query.filter(Company.tfidf_cluster.ilike(f"%{tfidf_cluster}%"))
    if auto_filter_keywords:
        raw_target = (scoring_config.get("scoring_target_keywords") or "").strip()
        if raw_target:
            kw_terms = [t.strip() for t in raw_target.split(",") if t.strip()]
            if kw_terms:
                from sqlalchemy import or_
                query = query.filter(or_(
                    Company.purpose_keywords.ilike(f"%{kw}%") for kw in kw_terms
                ))

    all_candidates = query.all()
    all_candidates.sort(key=lambda c: (
        -(c.flex_score if c.flex_score is not None else -1),
        distance_to_origin_km(origin_lat, origin_lon, canton=c.canton, municipality=c.municipality, lat=c.lat, lon=c.lon) or float("inf"),
        c.id,
    ))
    companies = all_candidates[:limit]
    total = len(companies)
    offset = max(0, min(resume_from, total))
    selected = companies[offset:]

    _boilerplate_patterns = crud.get_active_boilerplate_patterns(db)

    def _build_user_text(company: Company) -> str:
        purpose = _strip_purpose_boilerplate(company.purpose or "", _boilerplate_patterns)
        if max_purpose_chars > 0 and len(purpose) > max_purpose_chars:
            purpose = purpose[:max_purpose_chars] + "…"
        parts = [f"Company: {company.name}", f"Purpose: {purpose}"]
        if company.purpose_keywords:
            parts.append(f"Keywords: {company.purpose_keywords}")
        if company.tfidf_cluster and company.tfidf_cluster != "Undefined":
            parts.append(f"Clusters: {company.tfidf_cluster}")
        if company.website_url:
            parts.append(f"Website: {company.website_url}")
        if company.web_score is not None:
            parts.append(f"Google score: {company.web_score} (0–100 confidence that the website belongs to this company)")
        if company.social_media_only:
            parts.append("Note: only social media presence found — no company website")
        return "\n".join(parts)

    def _refresh_combined(company: Company) -> None:
        company.combined_score = Company.compute_combined_score(
            company.ai_score, company.web_score, company.flex_score
        )

    def _apply_single(company: Company, response_text: str) -> None:
        data = json.loads(strip_fences(response_text))
        company.ai_score = max(0, min(100, int(data.get("score", 0))))
        coerced = _coerce_category(data.get("category"))
        if fixed_categories_lc and coerced is None and data.get("category"):
            stats.setdefault("offlist_categories", []).append(str(data.get("category"))[:64])
        company.ai_category = coerced
        company.ai_freeform = str(data["freeform"]) if data.get("freeform") else None
        company.ai_scored_at = datetime.now(tz=timezone.utc)
        _refresh_combined(company)

    def _apply_chunk(chunk: list[Company], response_text: str) -> None:
        data = json.loads(strip_fences(response_text))
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array, got {type(data).__name__}: {response_text!r}")
        now = datetime.now(tz=timezone.utc)
        for company, item in zip(chunk, data):
            company.ai_score = max(0, min(100, int(item.get("score", 0))))
            coerced = _coerce_category(item.get("category"))
            if fixed_categories_lc and coerced is None and item.get("category"):
                stats.setdefault("offlist_categories", []).append(str(item.get("category"))[:64])
            company.ai_category = coerced
            company.ai_freeform = str(item["freeform"]) if item.get("freeform") else None
            company.ai_scored_at = now
            _refresh_combined(company)

    def _chunk_ids(chunk: list[Company]) -> str:
        return ", ".join(c.uid for c in chunk)

    chunks: list[list[Company]] = [
        selected[i: i + companies_per_message]
        for i in range(0, len(selected), companies_per_message)
    ]

    # ── Batch API path ────────────────────────────���───────────────────────────

    if use_batch_api:
        def _batch_custom_id(idx: int, chunk: list[Company]) -> str:
            if len(chunk) == 1:
                return chunk[0].uid.replace(".", "_")[:64]
            return f"chunk_{idx}"

        chunk_map: dict[str, list[Company]] = {}
        requests_list = []
        for idx, chunk in enumerate(chunks):
            cid = _batch_custom_id(idx, chunk)
            chunk_map[cid] = chunk
            content = _build_user_text(chunk[0]) if len(chunk) == 1 else "\n---\n".join(_build_user_text(c) for c in chunk)
            requests_list.append({
                "custom_id": cid,
                "params": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 128 * len(chunk),
                    "system": _claude_system_param(prompt, cache=True),
                    "messages": [{"role": "user", "content": content}],
                },
            })

        if not requests_list:
            return stats

        stats["batch_id"] = claude_batch_create(requests_list, api_key=api_key)

        if submit_only:
            stats["chunk_company_ids"] = {
                cid: [c.id for c in cos] for cid, cos in chunk_map.items()
            }
            return stats

        if progress_cb:
            progress_cb(0, total, stats)

        def _batch_progress(done: int, _total: int) -> None:
            if progress_cb:
                progress_cb(min(done * companies_per_message, total), total, stats)

        for result in claude_batch_iter_results(
            stats["batch_id"],
            api_key=api_key,
            poll=True,
            progress_cb=_batch_progress,
            total_hint=total,
        ):
            chunk = chunk_map.get(result.custom_id)
            if chunk is None:
                continue
            if result.succeeded:
                stats["input_tokens"] += result.input_tokens
                stats["output_tokens"] += result.output_tokens
                try:
                    if len(chunk) == 1:
                        _apply_single(chunk[0], result.text)
                    else:
                        _apply_chunk(chunk, result.text)
                    stats["classified"] += len(chunk)
                except json.JSONDecodeError as exc:
                    stats["errors"].append(f"{_chunk_ids(chunk)}: JSON parse error — raw: {result.text!r} — {exc}")
                    stats["skipped"] += len(chunk)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Batch result parse failed for %s: %s", _chunk_ids(chunk), exc)
                    stats["errors"].append(f"{_chunk_ids(chunk)}: {type(exc).__name__}: {exc}")
                    stats["skipped"] += len(chunk)
            else:
                stats["errors"].append(f"{_chunk_ids(chunk)}: batch error — {result.error}")
                stats["skipped"] += len(chunk)

        db.commit()
        if progress_cb:
            progress_cb(total, total, stats)
        return stats

    # ── Per-request path (default) ─────────────────���──────────────────────────

    for i, chunk in enumerate(chunks):
        done = offset + i * companies_per_message + len(chunk)
        try:
            content = _build_user_text(chunk[0]) if len(chunk) == 1 else "\n---\n".join(_build_user_text(c) for c in chunk)
            response_text, tokens = claude_call(
                prompt,
                content,
                api_key=api_key,
                max_tokens=128 * len(chunk),
                cache_system=True,
            )
            stats["input_tokens"] += tokens["input_tokens"]
            stats["output_tokens"] += tokens["output_tokens"]
            if dry_run:
                parsed = json.loads(strip_fences(response_text))
                if len(chunk) == 1:
                    items = [parsed] if isinstance(parsed, dict) else parsed[:1]
                else:
                    items = parsed if isinstance(parsed, list) else []
                for company, item in zip(chunk, items):
                    stats["preview_results"].append({
                        "company_id": company.id,
                        "name": company.name,
                        "ai_score": max(0, min(100, int(item.get("score", 0)))) if item.get("score") is not None else None,
                        "ai_category": str(item.get("category", ""))[:128] if item.get("category") else None,
                        "ai_freeform": str(item["freeform"]) if item.get("freeform") else None,
                    })
                stats["classified"] += len(chunk)
            else:
                if len(chunk) == 1:
                    _apply_single(chunk[0], response_text)
                else:
                    _apply_chunk(chunk, response_text)
                stats["classified"] += len(chunk)

        except json.JSONDecodeError as exc:
            stats["errors"].append(f"{_chunk_ids(chunk)}: JSON parse error — raw: {response_text!r} — {exc}")
            stats["skipped"] += len(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Claude classify failed for %s: %s", _chunk_ids(chunk), exc)
            stats["errors"].append(f"{_chunk_ids(chunk)}: {type(exc).__name__}: {exc}")
            stats["skipped"] += len(chunk)

        if not dry_run and (done % 50 < companies_per_message or done >= total):
            db.commit()
            if progress_cb:
                progress_cb(min(done, total), total, stats)

        time.sleep(0.15)

    if not dry_run:
        db.commit()
    if progress_cb:
        progress_cb(total, total, stats)

    return stats


def resume_claude_batch(
    db: Session,
    *,
    batch_id: str,
    chunk_company_ids: dict,
    api_key: str,
    org_id: int | None = None,
) -> tuple[str, dict]:
    """Poll an Anthropic Message Batch and process results if it has finished.

    Returns ``(processing_status, stats)``. When ``processing_status == "ended"``
    the companies have been updated and the DB committed.
    """
    if not api_key:
        return ("error", {"error": "Anthropic API key not configured", "classified": 0, "skipped": 0, "errors": []})

    try:
        status = claude_batch_poll(batch_id, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        return ("error", {"error": f"Anthropic API error: {exc}", "classified": 0, "skipped": 0, "errors": [str(exc)]})

    if status != "ended":
        return (status, {"classified": 0, "skipped": 0, "errors": []})

    stats: dict = {"classified": 0, "skipped": 0, "errors": [], "input_tokens": 0, "output_tokens": 0}

    all_ids = [cid for ids in chunk_company_ids.values() for cid in ids]
    if not all_ids:
        return ("ended", stats)

    companies_by_id = {c.id: c for c in db.query(Company).filter(Company.id.in_(all_ids)).all()}
    chunk_map = {
        cid: [companies_by_id[cid2] for cid2 in ids if cid2 in companies_by_id]
        for cid, ids in chunk_company_ids.items()
    }

    now = datetime.now(tz=timezone.utc)

    for result in claude_batch_iter_results(batch_id, api_key=api_key, poll=False):
        chunk = chunk_map.get(result.custom_id)
        if not chunk:
            continue
        if result.succeeded:
            stats["input_tokens"] += result.input_tokens
            stats["output_tokens"] += result.output_tokens
            try:
                data = json.loads(strip_fences(result.text))
                if len(chunk) == 1:
                    company = chunk[0]
                    company.ai_score = max(0, min(100, int(data.get("score", 0))))
                    company.ai_category = str(data.get("category", ""))[:128] if data.get("category") else None
                    company.ai_freeform = str(data["freeform"]) if data.get("freeform") else None
                    company.ai_scored_at = now
                else:
                    if not isinstance(data, list):
                        raise ValueError(f"Expected array, got {type(data).__name__}")
                    for company, item in zip(chunk, data):
                        company.ai_score = max(0, min(100, int(item.get("score", 0))))
                        company.ai_category = str(item.get("category", ""))[:128] if item.get("category") else None
                        company.ai_freeform = str(item["freeform"]) if item.get("freeform") else None
                        company.ai_scored_at = now
                stats["classified"] += len(chunk)
            except Exception as exc:  # noqa: BLE001
                uids = ", ".join(c.uid for c in chunk)
                logger.warning("Resume batch parse failed for %s: %s", uids, exc)
                stats["errors"].append(f"{uids}: {type(exc).__name__}: {exc}")
                stats["skipped"] += len(chunk)
        else:
            uids = ", ".join(c.uid for c in chunk)
            stats["errors"].append(f"{uids}: batch error — {result.error}")
            stats["skipped"] += len(chunk)

    db.commit()
    return ("ended", stats)
