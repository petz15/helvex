"""rescore_scope — materializes company_score for one (org, optional user) scope.

Scoring/multi-tenancy rework, phase 3. Chunked/batched per the 700k-row scale
rule (two passes over companies in id-keyset batches, same shape as
geocoding_pipeline.recalculate_flex_scores — never loads all companies as ORM
objects at once, though the raw-score dict held in memory across the whole
run mirrors that existing function's accepted pattern).

What's actually per-scope-configurable today:
  - flex_score: fully config-driven via compute_flex_score_breakdown(config=...)
    + population-wide min-max normalization (must see every company's raw
    score in this scope's config before any one company's final score is known).
  - ai_score: not recomputed here — read from org_company_ai (org-shared,
    written by claude_classify).
  - web_score: copied from the global Company.web_score. There is no per-org
    web-scoring lever yet (website identity verdict uses crawl confidence +
    global AppSetting thresholds, not scoring_* config) — this is a known
    simplification, not a placeholder bug. A future phase can make this
    per-org once such a lever exists.
  - combined_score: Company.compute_combined_score(...) unchanged, fed this
    scope's flex/web/ai values (though today's formula doesn't weight flex in
    anyway — see that function's docstring).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.crud import company_score as score_crud
from app.crud import org_company_ai as ai_crud
from app.models.company import Company
from app.services.scoring.config_resolution import effective_config, resolve_scope
from app.services.scoring.scoring import compute_flex_score_breakdown, normalize_raw_scores


def rescore_scope(
    db: Session,
    *,
    org_id: int,
    user_id: int | None = None,
    batch_size: int = 1000,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Recompute and persist company_score for the resolved (org_id, scope_user_id) scope.

    user_id: the requesting user; resolved to their own materialized scope only
    if they have scoring_* overrides recorded (see config_resolution.resolve_scope) —
    otherwise this is a no-op alias for the org-default scope.
    """
    scope_user_id = resolve_scope(db, org_id=org_id, user_id=user_id)
    config = effective_config(db, org_id=org_id, user_id=scope_user_id)
    cancelled_score = int(config.get("scoring_cancelled_score", "5") or 5)

    stats: dict[str, Any] = {"updated": 0, "errors": []}
    raw_scores: dict[int, int | None] = {}
    web_scores: dict[int, int | None] = {}
    noga_conf: dict[int, float | None] = {}
    purpose_kw: dict[int, str | None] = {}

    total = db.query(func.count(Company.id)).scalar() or 0
    offset = 0
    while True:
        batch = db.query(Company).order_by(Company.id.asc()).offset(offset).limit(batch_size).all()
        if not batch:
            break
        for company in batch:
            try:
                bd = compute_flex_score_breakdown(
                    legal_form=company.legal_form,
                    legal_form_short_name=company.legal_form_short_name,
                    status=company.status,
                    canton=company.canton,
                    municipality=company.municipality,
                    lat=company.lat,
                    lon=company.lon,
                    purpose_keywords=company.purpose_keywords,
                    tfidf_cluster=company.tfidf_cluster,
                    noga_path=company.noga_path,
                    noga_code=company.noga_code,
                    noga_level=company.noga_level,
                    config=config,
                )
                raw_scores[company.id] = None if bd.get("cancelled") else int(bd["raw_total"])
                web_scores[company.id] = company.web_score
                noga_conf[company.id] = company.noga_confidence
                purpose_kw[company.id] = company.purpose_keywords
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")
        offset += len(batch)
        if progress_cb:
            progress_cb(min(offset, total), total, {**stats, "_phase": "scoring"})

    normalized_flex = normalize_raw_scores(raw_scores, cancelled_score=cancelled_score)

    ai_by_company = ai_crud.bulk_get_org_ai(db, org_id=org_id, company_ids=list(normalized_flex.keys()))

    write_total = len(normalized_flex)
    write_done = 0
    for company_id, flex_score in normalized_flex.items():
        try:
            web_score = web_scores.get(company_id)
            ai_row = ai_by_company.get(company_id)
            ai_score = ai_row.ai_score if ai_row else None
            combined = Company.compute_combined_score(
                ai_score, noga_conf.get(company_id), purpose_kw.get(company_id), web_score=web_score,
            )
            score_crud.upsert_score(
                db,
                org_id=org_id, user_id=scope_user_id, company_id=company_id,
                flex_score=flex_score, web_score=web_score, combined_score=combined,
            )
            stats["updated"] += 1
            write_done += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"company {company_id} [{type(exc).__name__}]: {exc}")

        if write_done % batch_size == 0:
            db.commit()
            if progress_cb:
                progress_cb(write_done, write_total, {**stats, "_phase": "writing"})

    db.commit()
    if progress_cb:
        progress_cb(write_done, write_total, {**stats, "_phase": "writing"})

    return stats
