"""REST API for job management and collection/scoring triggers."""
from __future__ import annotations

import asyncio
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.clients.zefix_client import SWISS_CANTONS
from app.auth import get_current_user, require_superadmin
from app.services.jobs.rate_limit import check_job_rate_limit
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.services.billing import credits as credits_service
from app.services.jobs.job_worker import enqueue_job
from app.services.billing.tiers import get_export_limit, has_feature, normalize_tier


router = APIRouter(tags=["jobs"])


def _assert_job_visible_to_user(job, current_user: User) -> None:
    if current_user.is_superadmin:
        return
    if job.user_id == current_user.id:
        return
    if current_user.org_id is not None and job.org_id == current_user.org_id:
        return
    raise HTTPException(status_code=404, detail="Job not found")


def _enqueue_or_http_error(
    request: Request,
    *,
    job_type: str,
    label: str,
    params: dict,
    db: Session,
    org_id: int | None = None,
    user_id: int | None = None,
    credit_deduction: dict | None = None,
):
    try:
        return enqueue_job(request.app, job_type=job_type, label=label, params=params, db=db, org_id=org_id, user_id=user_id, credit_deduction=credit_deduction)
    except ValueError as exc:
        msg = str(exc)
        http_status = status.HTTP_402_PAYMENT_REQUIRED if "Insufficient credits" in msg else 400
        raise HTTPException(status_code=http_status, detail=msg) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── Schemas ────────────────────────────────────────────────────────────────────

class JobOut(BaseModel):
    id: int
    job_type: str
    label: str
    status: str
    message: str | None
    progress_done: int | None
    progress_total: int | None
    error: str | None
    restart_count: int
    created_at: str
    started_at: str | None
    finished_at: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_obj(cls, j) -> "JobOut":
        return cls(
            id=j.id,
            job_type=j.job_type,
            label=j.label,
            status=j.status,
            message=j.message,
            progress_done=j.progress_done,
            progress_total=j.progress_total,
            error=j.error,
            restart_count=j.restart_count or 0,
            created_at=j.queued_at.isoformat() if j.queued_at else "",
            started_at=j.started_at.isoformat() if j.started_at else None,
            finished_at=j.completed_at.isoformat() if j.completed_at else None,
        )


class EventOut(BaseModel):
    id: int
    job_id: int
    level: str
    message: str
    created_at: str

    @classmethod
    def from_orm_obj(cls, e) -> "EventOut":
        return cls(
            id=e.id,
            job_id=e.job_id,
            level=e.level,
            message=e.message,
            created_at=e.created_at.isoformat() if e.created_at else "",
        )


# ── Job CRUD ───────────────────────────────────────────────────────────────────

@router.get("/jobs", response_model=list[JobOut])
def list_jobs(limit: int = 100, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Always include active jobs so the UI never "loses" a recovered job due
    # to history limit cutoffs after a restart/redeploy.
    if current_user.is_superadmin:
        recent = crud.list_jobs(db, limit=limit)
        active = crud.list_active_jobs(db)
    else:
        recent = crud.list_jobs_for_user(db, user_id=current_user.id, org_id=current_user.org_id, limit=limit)
        active = crud.list_active_jobs_for_user(db, user_id=current_user.id, org_id=current_user.org_id)

    by_id = {j.id: j for j in recent}
    for j in active:
        by_id[j.id] = j

    merged = sorted(
        by_id.values(),
        key=lambda j: j.completed_at or j.queued_at or j.started_at,
        reverse=True,
    )
    return [JobOut.from_orm_obj(j) for j in merged]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _assert_job_visible_to_user(job, current_user)
    return JobOut.from_orm_obj(job)


@router.get("/jobs/{job_id}/events", response_model=list[EventOut])
def get_job_events(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _assert_job_visible_to_user(job, current_user)
    return [EventOut.from_orm_obj(e) for e in crud.list_events(db, job_id=job_id, limit=200, exclude_debug=False)]


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _assert_job_visible_to_user(job, current_user)
    if job.status in ("queued", "paused"):
        crud.mark_cancelled(db, job, message="Cancelled before execution")
        crud.create_event(db, job_id=job.id, level="warn", message="Job cancelled")
    elif job.status == "running":
        crud.mark_cancel_requested(db, job)
        crud.create_event(db, job_id=job.id, level="warn", message="Cancellation requested")
    else:
        raise HTTPException(status_code=400, detail="Only queued, running, or paused jobs can be cancelled")
    return JobOut.from_orm_obj(job)


@router.post("/jobs/{job_id}/pause", response_model=JobOut)
def pause_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _assert_job_visible_to_user(job, current_user)
    if job.status != "running":
        raise HTTPException(status_code=400, detail="Only running jobs can be paused")
    crud.mark_pause_requested(db, job)
    crud.create_event(db, job_id=job.id, level="info", message="Pause requested")
    return JobOut.from_orm_obj(job)


@router.post("/jobs/{job_id}/resume", response_model=JobOut)
def resume_job(job_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _assert_job_visible_to_user(job, current_user)
    if job.status != "paused":
        raise HTTPException(status_code=400, detail="Only paused jobs can be resumed")
    crud.resume_paused_job(db, job)
    crud.create_event(db, job_id=job.id, level="info", message=f"Resumed from {job.progress_done or 0}")
    from app.services.jobs.job_worker import kick_job_worker
    kick_job_worker(request.app)
    return JobOut.from_orm_obj(job)


class RerunBody(BaseModel):
    mode: str  # "new" | "continue"


@router.post("/jobs/{job_id}/rerun", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def rerun_job(
    job_id: int,
    body: RerunBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-enqueue a failed/cancelled job.

    mode="new"      — fresh run from the beginning (same params, progress_done=0).
    mode="continue" — resume from where it stopped (adds resume_from=progress_done).
    """
    import json as _json

    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _assert_job_visible_to_user(job, current_user)
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(status_code=400, detail="Only failed or cancelled jobs can be rerun")

    params: dict = {}
    if job.params_json:
        try:
            params = _json.loads(job.params_json)
        except Exception:
            params = {}

    if body.mode == "continue" and job.progress_done:
        params = {**params, "resume_from": job.progress_done}

    # Re-charge jobs whose credits were taken at the route rather than the enqueue
    # path (e.g. csv_export). A plain re-enqueue would run them free — metered jobs
    # (claude_classify etc.) are still charged by _apply_credit_deduction_if_needed.
    credit_deduction = None
    old_stats = {}
    if job.stats_json:
        try:
            old_stats = _json.loads(job.stats_json)
        except Exception:
            old_stats = {}
    old_ded = old_stats.get("_credit_deduction") if isinstance(old_stats, dict) else None
    if (
        isinstance(old_ded, dict)
        and old_ded.get("source") == "route"
        and job.org_id
        and not current_user.is_superadmin
    ):
        _action = str(old_ded.get("action") or "")
        _count = int(old_ded.get("count") or 0)
        if _action and _count > 0:
            if not credits_service.check_and_deduct(
                db, job.org_id, _action, _count,
                reference_id=f"rerun:{job.job_type}:job_{job_id}:user_{current_user.id}",
            ):
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail="Insufficient credits to rerun this job.",
                )
            credit_deduction = old_ded

    label_prefix = "↺ " if body.mode == "new" else "⏩ "
    new_job = _enqueue_or_http_error(
        request,
        job_type=job.job_type,
        label=label_prefix + job.label,
        params=params,
        db=db,
        org_id=job.org_id,
        user_id=current_user.id,
        credit_deduction=credit_deduction,
    )
    return JobOut.from_orm_obj(new_job)


@router.get("/jobs/stream/active")
async def stream_active_jobs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """SSE stream that pushes the full job list as JSON on every change.

    Polls DB every second and sends an update only when the serialised result
    changes (much better than N clients each polling at 3 s intervals).

    A heartbeat comment is emitted every 30 s to keep the TCP connection alive
    through proxies.  SSE comments are never dispatched as 'message' events by
    the browser, so the frontend onmessage handler is never called for them.

    The route is async so that `await asyncio.sleep(1)` yields back to the event
    loop between polls instead of blocking a worker thread.  The blocking
    SQLAlchemy calls inside `_fetch_jobs` are dispatched to a thread-pool via
    `asyncio.to_thread` so they never stall the loop either.
    """
    import json as _json

    HEARTBEAT_INTERVAL = 30

    def _fetch_jobs() -> list[dict]:
        # Runs inside asyncio.to_thread — blocking DB I/O is safe here.
        db.expire_all()
        if current_user.is_superadmin:
            recent = crud.list_jobs(db, limit=100)
            active = crud.list_active_jobs(db)
        else:
            recent = crud.list_jobs_for_user(
                db, user_id=current_user.id, org_id=current_user.org_id, limit=100
            )
            active = crud.list_active_jobs_for_user(
                db, user_id=current_user.id, org_id=current_user.org_id
            )
        by_id = {j.id: j for j in recent}
        for j in active:
            by_id[j.id] = j
        merged = sorted(
            by_id.values(),
            key=lambda j: j.completed_at or j.queued_at or j.started_at,
            reverse=True,
        )
        return [JobOut.from_orm_obj(j).model_dump() for j in merged]

    async def event_generator():
        last_sent: str | None = None
        last_hb = time.time()
        initial = _json.dumps(await asyncio.to_thread(_fetch_jobs))
        yield f"data: {initial}\n\n"
        last_sent = initial
        while True:
            await asyncio.sleep(1)
            current = _json.dumps(await asyncio.to_thread(_fetch_jobs))
            if current != last_sent:
                yield f"data: {current}\n\n"
                last_sent = current
            now = time.time()
            if now - last_hb >= HEARTBEAT_INTERVAL:
                yield ": heartbeat\n\n"
                last_hb = now

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Collection triggers ────────────────────────────────────────────────────────

class BulkImportBody(BaseModel):
    cantons: list[str] | None = None
    start_from_canton: str | None = None  # skip cantons before this one
    active_only: bool = False
    delay: float = 0.5
    empty_abort_threshold: int = 100  # stop after this many consecutive empty prefixes


class BatchCollectBody(BaseModel):
    limit: int = 100
    only_missing_website: bool = True
    refresh_zefix: bool = False
    run_google: bool = True
    concurrency: int = 1                 # parallel Serper/ScrapingDog requests (2–5 recommended)
    # ── Geography ─────────────────────────────────────────────────────────────
    canton: str | None = None            # single-canton shorthand (backward compat)
    cantons: list[str] | None = None     # include any of these cantons
    exclude_cantons: list[str] | None = None
    # ── Status / registration ─────────────────────────────────────────────────
    active_only: bool = True             # exclude CANCELLED/BEING_CANCELLED/Gelöscht
    skip_uid_only: bool = False          # exclude registration_type='uid_only'
    skip_mwst_only: bool = False         # exclude registration_type='mwst'
    # ── Language ──────────────────────────────────────────────────────────────
    purpose_language: str | None = None
    # ── Scores ────────────────────────────────────────────────────────────────
    min_zefix_score: int | None = None   # maps to min_flex_score in worker
    max_zefix_score: int | None = None   # maps to max_flex_score in worker
    min_claude_score: int | None = None  # maps to min_ai_score in worker
    min_combined_score: float | None = None
    # ── Keywords / clusters ───────────────────────────────────────────────────
    purpose_keywords: str | None = None
    exclude_purpose_keywords: str | None = None
    tfidf_cluster: str | None = None
    review_status: str | None = None
    # ── Industry (NOGA) ───────────────────────────────────────────────────────
    noga_code: str | None = None         # prefix include — e.g. "47" for retail
    exclude_noga_code: str | None = None # prefix exclude — e.g. "64" for financials
    # ── Company type ──────────────────────────────────────────────────────────
    legal_form: str | None = None
    business_model: str | None = None
    # ── Date ranges ───────────────────────────────────────────────────────────
    registered_after: str | None = None  # first_sogc_date >=  (YYYY-MM-DD)
    registered_before: str | None = None # first_sogc_date <=
    sogc_after: str | None = None        # sogc_date >= (last Zefix publication)
    sogc_before: str | None = None       # sogc_date <=
    # ── Ordering ──────────────────────────────────────────────────────────────
    # flex_score_desc | combined_score_desc | ai_score_desc | web_score_desc |
    # last_enriched_asc | created_asc | sogc_date_desc |
    # first_sogc_date_asc | first_sogc_date_desc | name_asc
    order_by: str = "flex_score_desc"


class InitialCollectBody(BaseModel):
    names: list[str] = []
    uids: list[str] = []
    canton: str | None = None
    legal_form: str | None = None
    active_only: bool = True
    run_google: bool = True


class DetailCollectBody(BaseModel):
    cantons: list[str] | None = None
    uids: list[str] | None = None
    delay: float = 0.3
    only_missing_details: bool = False
    score_if_missing: bool = False


class RecalcZefixBody(BaseModel):
    pass


class RecalcGoogleBody(BaseModel):
    pass


class ReextractPurposeBody(BaseModel):
    only_missing_purpose: bool = True


class ReextractZefixRawBody(BaseModel):
    fields: list[str] | None = None  # None = all re-extractable fields
    ids: list[str | int] | None = None  # None = all; mix of internal IDs, UIDs (CHE-…), CHIDs
    mode: str = "missing"  # "missing" = only NULL rows; "all" = overwrite everything


class ReclassifyNogaBody(BaseModel):
    only_missing_noga: bool = False
    include_stale: bool = False
    only_detailed_raw: bool = True
    embed_mode: str = "clean"  # "clean" | "full_and_clean" | "none"


class BuildNogaEmbeddingsBody(BaseModel):
    batch_size: int = 256


class DetectLanguageBulkBody(BaseModel):
    only_missing: bool = True


class ReclassifyLowConfNogaBody(BaseModel):
    confidence_threshold: float = 0.80


class EmbedPurposeBody(BaseModel):
    only_missing: bool = True


class ClaudeClassifyBody(BaseModel):
    canton: str | None = None
    min_zefix_score: int | None = None   # passed as-is to job params; worker maps to min_flex_score
    max_zefix_score: int | None = None   # passed as-is to job params; worker maps to max_flex_score
    min_google_score: int | None = None  # passed as-is to job params; worker maps to min_web_score
    purpose_keywords: str | None = None
    rerun_classified: bool = False
    auto_filter_keywords: bool = False
    use_fixed_categories: bool = False
    limit: int = 500
    system_prompt: str | None = None
    use_batch_api: bool = False
    companies_per_message: int = 1


class ClusterPipelineBody(BaseModel):
    n_clusters: int = 50
    max_clusters_per_company: int = 7
    min_similarity: float = 0.10
    n_components: int = 50
    top_terms: int = 5
    top_keywords_per_company: int = 10
    canton: str | None = None
    min_zefix_score: int | None = None
    max_zefix_score: int | None = None
    limit: int | None = None
    use_keywords: bool = True  # Cluster on pre-extracted purpose_keywords (recommended)


class ReextractKeywordsBody(BaseModel):
    only_missing: bool = False
    canton: str | None = None
    limit: int | None = None


class RecomputeKeywordsBody(BaseModel):
    top_keywords_per_company: int = 10
    canton: str | None = None
    limit: int | None = None


class ClusterAnalysisBody(BaseModel):
    top_n_clusters: int = 20
    top_n_terms: int = 10


class DiscoverStopwordsBody(BaseModel):
    use_ai: bool = False


@router.post("/collection/bulk", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_bulk(body: BulkImportBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    canton_list = [c.upper() for c in body.cantons] if body.cantons else None
    start_from = body.start_from_canton.upper() if body.start_from_canton else None
    scope = ', '.join(canton_list) if canton_list else 'all 26'
    status_scope = "active only" if body.active_only else "active + closed"
    label = f"Bulk import — cantons: {scope} ({status_scope})" + (f" (from {start_from})" if start_from else "")
    job = _enqueue_or_http_error(
        request,
        job_type="bulk",
        label=label,
        params={
            "cantons": canton_list,
            "start_from_canton": start_from,
            "active_only": body.active_only,
            "delay": body.delay,
            "empty_abort_threshold": body.empty_abort_threshold,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/batch", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_batch(body: BatchCollectBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    job = _enqueue_or_http_error(
        request,
        job_type="web_search_batch",
        label=f"URL search enrichment — up to {body.limit} companies",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/initial", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_initial(body: InitialCollectBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    if not body.names and not body.uids:
        raise HTTPException(status_code=400, detail="Provide at least one name or UID")
    label = f"Specific search — {len(body.names)} name(s), {len(body.uids)} UID(s)"
    job = _enqueue_or_http_error(request, job_type="initial", label=label, params=body.model_dump(), db=db)
    return JobOut.from_orm_obj(job)


@router.post("/collection/detail", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_detail(body: DetailCollectBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    if body.cantons:
        label = f"Zefix detail fetch — cantons: {', '.join(body.cantons)}"
    elif body.uids:
        label = f"Zefix detail fetch — {len(body.uids)} UID(s)"
    else:
        label = "Zefix detail fetch — all matching companies"
    if body.only_missing_details:
        label += " (missing details only)"
    job = _enqueue_or_http_error(request, job_type="detail", label=label, params=body.model_dump(), db=db)
    return JobOut.from_orm_obj(job)


@router.post("/scoring/zefix", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_recalc_zefix(request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    job = _enqueue_or_http_error(
        request,
        job_type="recalculate_scores",
        label="Recalculate Zefix scores",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/google", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_recalc_google(request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    job = _enqueue_or_http_error(
        request,
        job_type="recalculate_google_scores",
        label="Recalculate Google scores",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/rescore-scope", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_rescore_scope(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Recompute company_score for the caller's effective scope.

    Resolves to the org-default scope unless the caller has scoring_*
    overrides recorded for this org, in which case their own materialized
    scope is (re)computed instead. See scoring/config_resolution.resolve_scope.
    """
    if not current_user.org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No organization context")
    job = _enqueue_or_http_error(
        request,
        job_type="rescore_scope",
        label="Rescore companies",
        params={"user_id": current_user.id},
        db=db,
        org_id=current_user.org_id,
        user_id=current_user.id,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/re-geocode", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_re_geocode(request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    job = _enqueue_or_http_error(
        request,
        job_type="re_geocode",
        label="Re-geocode all companies",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/reextract-purpose", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_reextract_purpose(
    body: ReextractPurposeBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    label = "Re-extract purpose from detailed zefix_raw"
    if body.only_missing_purpose:
        label += " (missing only)"
    job = _enqueue_or_http_error(
        request,
        job_type="reextract_purpose",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/reextract-zefix-raw", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_reextract_zefix_raw(
    body: ReextractZefixRawBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Re-extract columns from the stored zefix_raw JSON blob without any API calls.

    Pass ``fields`` to limit which columns are updated (default: all 29 re-extractable
    columns). Pass ``ids`` to restrict to specific companies. ``mode`` controls whether
    only rows with NULL values are updated ("missing") or all rows ("all").
    """
    fields_label = f" fields={body.fields}" if body.fields else ""
    ids_label = f" ids={len(body.ids)}" if body.ids else ""
    label = f"Re-extract from zefix_raw [{body.mode}]{fields_label}{ids_label}"
    job = _enqueue_or_http_error(
        request,
        job_type="reextract_zefix_raw",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/reclassify-noga", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_reclassify_noga(
    body: ReclassifyNogaBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    label = "Reclassify NOGA from local taxonomy"
    if body.only_missing_noga:
        label += " (missing only)"
    if body.include_stale:
        label += " + stale"
    if body.only_detailed_raw:
        label += " (detailed zefix_raw only)"
    job = _enqueue_or_http_error(
        request,
        job_type="reclassify_noga",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/build-noga-embeddings", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_build_noga_embeddings(
    body: BuildNogaEmbeddingsBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    job = _enqueue_or_http_error(
        request,
        job_type="build_noga_embeddings",
        label="Build NOGA pgvector embeddings",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/detect-language", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_detect_language_bulk(
    body: DetectLanguageBulkBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    label = "Detect purpose language"
    if body.only_missing:
        label += " (missing only)"
    job = _enqueue_or_http_error(
        request,
        job_type="detect_language_bulk",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/reclassify-low-conf-noga", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_reclassify_low_conf_noga(
    body: ReclassifyLowConfNogaBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    job = _enqueue_or_http_error(
        request,
        job_type="reclassify_low_conf_noga",
        label=f"Reclassify low-confidence NOGA (threshold {body.confidence_threshold})",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/embed-purpose-full", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_embed_purpose_full(
    body: EmbedPurposeBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Embed raw company purpose text (purpose_full) for semantic search."""
    label = "Embed purpose (full)"
    if body.only_missing:
        label += " — missing only"
    job = _enqueue_or_http_error(
        request,
        job_type="embed_purpose_full",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/embed-purpose-clean", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_embed_purpose_clean(
    body: EmbedPurposeBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Embed boilerplate-stripped company purpose text (purpose_clean) for semantic search."""
    label = "Embed purpose (clean)"
    if body.only_missing:
        label += " — missing only"
    job = _enqueue_or_http_error(
        request,
        job_type="embed_purpose_clean",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/claude", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_claude_classify(body: ClaudeClassifyBody, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Non-org users have no credit balance — block to prevent free AI calls.
    if not current_user.is_superadmin and not current_user.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization membership required to use AI classification")

    org: Organization | None = None
    org_tier = "free"
    if current_user.org_id:
        org = db.get(Organization, current_user.org_id)
        if org:
            org_tier = normalize_tier(org.tier)

    # Tier-aware rate limit (free: 2/15 min, simple: 5/10 min, explorer+: 15/10 min).
    check_job_rate_limit(request, current_user, "claude_classify", org_tier=org_tier)

    # Minimum-balance pre-check so user gets an immediate 402 instead of a silent job failure.
    if not current_user.is_superadmin and org and not getattr(org, "credits_unlimited", False):
        min_cost = credits_service.CREDIT_COSTS["batch_llm"]  # cost for 1 company
        balance = getattr(org, "credits_balance", 0) or 0
        if balance < min_cost:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Insufficient credits. AI classification costs {min_cost:,} credits per company. Current balance: {balance:,} credits.",
            )

    # Only superadmins may supply a custom system_prompt — it is stored and
    # executed verbatim by Claude, which is a prompt-injection risk.
    params = body.model_dump()
    if not current_user.is_superadmin:
        params["system_prompt"] = None
        # immediate_llm (use_batch_api=False) requires Explorer tier or above.
        # Free / Simple orgs are silently downgraded to batch mode (cheaper, async).
        if org is not None and not has_feature(org, "immediate_llm"):
            params["use_batch_api"] = True

    job = _enqueue_or_http_error(
        request,
        job_type="claude_classify",
        label=f"Claude classify — up to {body.limit} companies",
        params=params,
        db=db,
        org_id=current_user.org_id,
        user_id=current_user.id,
    )
    return JobOut.from_orm_obj(job)


_PREVIEW_MAX_COMPANIES = 5
_PREVIEW_RATE_LIMIT = 3  # calls per org per calendar day


class ClaudePreviewBody(BaseModel):
    canton: str | None = None
    min_zefix_score: int | None = None
    max_zefix_score: int | None = None
    purpose_keywords: str | None = None
    use_fixed_categories: bool = False


class ClaudePreviewResult(BaseModel):
    company_id: int
    name: str
    ai_score: float | None
    ai_category: str | None
    ai_freeform: str | None


class ClaudePreviewOut(BaseModel):
    results: list[ClaudePreviewResult]
    previews_used: int
    previews_remaining: int


@router.post("/scoring/claude-preview", response_model=ClaudePreviewOut)
def trigger_claude_preview(
    body: ClaudePreviewBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run AI classification on up to 5 companies without persisting results.

    Hard security limits:
    - Max 5 companies per call (enforced server-side, ignores any limit param).
    - Always uses batch mode — never immediate_llm regardless of tier.
    - Always uses the platform Anthropic key — BYO keys are never used.
    - system_prompt is always stripped.
    - Rate-limited to 3 calls per org per 24 h to prevent free LLM abuse.
    - Org membership required.
    - Results are returned inline and never written to the database.
    """
    if not current_user.is_superadmin and not current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required to use AI preview",
        )

    org_id = current_user.org_id

    # ── Rate limiting (DB-backed daily counter per org) ────────────────────────
    if not current_user.is_superadmin and org_id:
        from datetime import date as _date
        from app.crud.app_setting import set_org_setting
        today = _date.today().isoformat()
        raw = crud.get_effective_setting(db, f"preview_count:{today}", org_id=org_id, default="0")
        try:
            count = int(raw) + 1
        except ValueError:
            count = 1
        if count > _PREVIEW_RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Preview rate limit reached ({_PREVIEW_RATE_LIMIT}/day).",
            )
        set_org_setting(db, org_id=org_id, key=f"preview_count:{today}", value=str(count))
        previews_used = count
        previews_remaining = max(0, _PREVIEW_RATE_LIMIT - count)
    else:
        previews_used = 0
        previews_remaining = _PREVIEW_RATE_LIMIT

    # ── Run classification synchronously (no job queue) ────────────────────────
    from app.config import settings as _cfg
    from app.services.ingestion.collection import claude_classify_batch

    api_key = (_cfg.anthropic_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="AI classification is not configured on this server")

    try:
        stats = claude_classify_batch(
            db,
            canton=body.canton,
            min_flex_score=body.min_zefix_score,
            max_flex_score=body.max_zefix_score,
            purpose_keywords=body.purpose_keywords,
            use_fixed_categories=body.use_fixed_categories,
            rerun_classified=True,  # include already-classified for preview
            limit=_PREVIEW_MAX_COMPANIES,
            system_prompt=None,           # always stripped for safety
            api_key=api_key,              # always platform key, never BYO
            org_id=org_id,
            use_batch_api=False,          # synchronous, inline results
            dry_run=True,                 # do not persist to DB
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI classification error: {exc}") from exc

    results = [
        ClaudePreviewResult(
            company_id=r["company_id"],
            name=r["name"],
            ai_score=r.get("ai_score"),
            ai_category=r.get("ai_category"),
            ai_freeform=r.get("ai_freeform"),
        )
        for r in (stats.get("preview_results") or [])
    ]
    return ClaudePreviewOut(
        results=results,
        previews_used=previews_used,
        previews_remaining=previews_remaining,
    )


@router.post("/scoring/cluster", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_cluster_pipeline(body: ClusterPipelineBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    job = _enqueue_or_http_error(
        request,
        job_type="tfidf_kmeans_cluster",
        label="TF-IDF + KMeans cluster pipeline",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/reextract-keywords", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_reextract_keywords(body: ReextractKeywordsBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    label = "Re-extract keywords from cached cluster artifacts"
    if body.only_missing:
        label += " (missing only)"
    if body.canton:
        label += f" — canton {body.canton.upper()}"
    if body.limit:
        label += f" — limit {body.limit}"
    job = _enqueue_or_http_error(
        request,
        job_type="reextract_keywords",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/recompute-keywords", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_recompute_keywords(body: RecomputeKeywordsBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    label = "Recompute keywords from purpose text"
    if body.canton:
        label += f" — canton {body.canton.upper()}"
    if body.limit:
        label += f" — limit {body.limit}"
    job = _enqueue_or_http_error(
        request,
        job_type="recompute_keywords",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/cluster-analysis", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_cluster_analysis(body: ClusterAnalysisBody, request: Request, db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    job = _enqueue_or_http_error(
        request,
        job_type="cluster_analysis",
        label=f"Cross-cluster analysis — top {body.top_n_clusters} clusters",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


class AnalyzeBoilerplateBody(BaseModel):
    min_match_count: int = 500
    max_candidates: int = 200
    sample_limit: int = 200_000
    language_filter: str | None = None   # DE / FR / IT / EN — None = all languages
    truncate_mode: bool = False           # when True, only scan last sentence of each purpose text
    pre_strip: bool = False               # when True, apply existing active rules before counting


@router.post("/scoring/analyze-boilerplate", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_analyze_boilerplate(
    body: AnalyzeBoilerplateBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Analyse purpose text corpus to find new boilerplate pattern candidates.

    Results are written as inactive BoilerplatePattern rows for admin review.
    Also seeds the standard FR/IT patterns before running the corpus scan.
    """
    from app.services.ml.boilerplate_analysis import seed_multilang_boilerplate
    seed_multilang_boilerplate(db)
    parts = [f"sample={body.sample_limit:,}", f"min_count={body.min_match_count}"]
    if body.language_filter:
        parts.append(f"lang={body.language_filter.upper()}")
    if body.truncate_mode:
        parts.append("truncate-tail")
    if body.pre_strip:
        parts.append("pre-strip")
    job = _enqueue_or_http_error(
        request,
        job_type="analyze_boilerplate",
        label=f"Boilerplate analysis — {', '.join(parts)}",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/discover-stopwords", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_discover_stopwords(
    body: DiscoverStopwordsBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Run the 4-phase automated stopword/boilerplate discovery pipeline.

    Phase 1: IDF-based term frequency analysis (free).
    Phase 2: Sentence-level hash deduplication (free, multilingual).
    Phase 3: Cross-cluster stopword auto-staging (free).
    Phase 4: Optional Claude Haiku review (use_ai=true, credit-consuming).
    """
    job = _enqueue_or_http_error(
        request,
        job_type="discover_stopwords",
        label="Stopword & boilerplate discovery" + (" + Claude review" if body.use_ai else ""),
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


# ── SHAB import triggers ───────────────────────────────────────────────────────

class ShabDailyBody(BaseModel):
    date: str | None = None          # ISO date (YYYY-MM-DD); defaults to yesterday
    request_delay: float = 0.15      # seconds between SHAB detail API calls


class ShabBackfillBody(BaseModel):
    from_date: str                   # ISO date (YYYY-MM-DD), required
    to_date: str | None = None       # ISO date; defaults to yesterday
    request_delay: float = 0.15


class SogcPreprocessBody(BaseModel):
    mode: str = "missing"            # "missing" | "all"
    uids: list[str] | None = None    # Optional CHE UIDs to restrict processing
    batch_size: int = 500


class UidImportBody(BaseModel):
    batch_size: int = 500
    not_in_hr: bool = True            # only commercialRegisterStatus="3" (the non-Zefix gap)
    mwst_only: bool = False           # restrict to MWST/VAT-registered
    refine_mwst: bool = True          # pull MWST subset of a still-capped postcode bucket
    max_depth: int = 1                # allow one AND-ed 2nd token on capped postcodes (0=off)
    min_token_freq: int = 2           # skip dictionary tokens rarer than this
    max_terms: int = 50000            # max dictionary terms (freq-sorted) — cost vs completeness
    second_token_count: int = 200     # top tokens tried as a 2nd AND term
    on_conflict: str = "update"       # "update" (refresh uid rows, never Zefix) | "ignore" (skip existing)


class UidDetailBody(BaseModel):
    batch_size: int = 100


class ShabArchiveBody(BaseModel):
    # Date-window mode (recommended for full historical import)
    date_start: str | None = None    # ISO date YYYY-MM-DD; enables date-window mode
    date_end: str | None = None      # ISO date YYYY-MM-DD; defaults to today
    window_days: int = 28            # Days per API window (keep ≤28 to stay under 50K cap)
    # Page mode (quick test when no dates given — limited to default 2018 window)
    start_page: int = 0
    end_page: int | None = None
    # Common
    page_size: int = 100
    request_delay: float = 0.5
    pdf_delay: float = 0.3


@router.post("/collection/uid-import", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_uid_import(
    body: UidImportBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Discover the non-Zefix gap from the UID register via a name-dictionary sweep.

    Outer loop is a dictionary of name tokens/keywords built from Zefix company
    names; each is queried nationally with commercialRegisterStatus="3" (not in HR)
    to find sole traders / MWST-only / uid_only entities absent from Zefix. Capped
    token queries are refined by canton → postcode → MWST → 2nd token. Existing
    Zefix rows get registration_type updated; new ones are inserted source='uid'.
    Run uid_detail then re_geocode afterwards to fill addresses + coordinates.
    """
    if body.mwst_only:
        label = "UID gap sweep (name dictionary × non-HR, MWST-only)"
    elif body.not_in_hr:
        label = "UID gap sweep (name dictionary × non-HR companies)"
    else:
        label = "UID register sweep (name dictionary × all companies)"
    job = _enqueue_or_http_error(
        request,
        job_type="uid_import",
        label=label,
        params={
            "batch_size": body.batch_size,
            "not_in_hr": body.not_in_hr,
            "mwst_only": body.mwst_only,
            "refine_mwst": body.refine_mwst,
            "max_depth": body.max_depth,
            "min_token_freq": body.min_token_freq,
            "max_terms": body.max_terms,
            "second_token_count": body.second_token_count,
            "on_conflict": body.on_conflict,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/uid-detail", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_uid_detail(
    body: UidDetailBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Fetch GetByUID detail for source='uid' companies that have no address yet.

    Populates address, legal_form, municipality, canton, address_zip from the
    UID detail endpoint. Run after uid_import; then trigger re_geocode.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="uid_detail",
        label="UID detail fetch (GetByUID — address + legal form)",
        params={"batch_size": body.batch_size},
        db=db,
    )
    return JobOut.from_orm_obj(job)


_SHAB_BACKFILL_MIN_DATE = date(2016, 2, 3)


@router.post("/collection/shab-daily", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_shab_daily(
    body: ShabDailyBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Import SHAB HR publications for a single day (default: yesterday)."""
    from datetime import date, timedelta, timezone
    from datetime import datetime as _dt

    if body.date:
        try:
            target = date.fromisoformat(body.date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from exc
    else:
        target = (_dt.now(tz=timezone.utc) - timedelta(days=1)).date()

    label = f"SHAB daily import — {target.isoformat()}"
    job = _enqueue_or_http_error(
        request,
        job_type="shab_daily",
        label=label,
        params={"date": target.isoformat(), "request_delay": body.request_delay},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/shab-backfill", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_shab_backfill(
    body: ShabBackfillBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Import all SHAB HR publications for a date range (historical backfill)."""
    from datetime import date, timedelta, timezone
    from datetime import datetime as _dt

    try:
        from_date = date.fromisoformat(body.from_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid from_date: {exc}") from exc

    if body.to_date:
        try:
            to_date = date.fromisoformat(body.to_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid to_date: {exc}") from exc
    else:
        to_date = (_dt.now(tz=timezone.utc) - timedelta(days=1)).date()

    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date")
    if from_date < _SHAB_BACKFILL_MIN_DATE:
        raise HTTPException(
            status_code=400,
            detail=(
                "from_date must be on or after "
                f"{_SHAB_BACKFILL_MIN_DATE.isoformat()}"
            ),
        )

    days = (to_date - from_date).days + 1
    label = f"SHAB backfill — {from_date} → {to_date} ({days} day{'s' if days != 1 else ''})"
    job = _enqueue_or_http_error(
        request,
        job_type="shab_backfill",
        label=label,
        params={
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "request_delay": body.request_delay,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


# ── SIMAP award import triggers ───────────────────────────────────────────────

_SIMAP_EARLIEST_DATE_STR = "2024-07-01"


class SimapDailyBody(BaseModel):
    date: str | None = None       # ISO date YYYY-MM-DD; defaults to yesterday
    request_delay: float = 0.12


class SimapBackfillBody(BaseModel):
    from_date: str                # ISO date YYYY-MM-DD, required
    to_date: str | None = None    # defaults to yesterday
    request_delay: float = 0.12


@router.post("/collection/simap-daily", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_simap_daily(
    body: SimapDailyBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Import SIMAP award publications for a single day (default: yesterday)."""
    from datetime import date, timedelta, timezone
    from datetime import datetime as _dt

    if body.date:
        try:
            target = date.fromisoformat(body.date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from exc
    else:
        target = (_dt.now(tz=timezone.utc) - timedelta(days=1)).date()

    label = f"SIMAP daily import — {target.isoformat()}"
    job = _enqueue_or_http_error(
        request,
        job_type="simap_daily",
        label=label,
        params={"date": target.isoformat(), "request_delay": body.request_delay},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/simap-backfill", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_simap_backfill(
    body: SimapBackfillBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Import all SIMAP award publications for a date range.

    SIMAP launched in July 2024 — use from_date=2024-07-01 for full history.
    """
    from datetime import date, timedelta, timezone
    from datetime import datetime as _dt

    _earliest = date.fromisoformat(_SIMAP_EARLIEST_DATE_STR)

    try:
        from_date = date.fromisoformat(body.from_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid from_date: {exc}") from exc

    if body.to_date:
        try:
            to_date = date.fromisoformat(body.to_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid to_date: {exc}") from exc
    else:
        to_date = (_dt.now(tz=timezone.utc) - timedelta(days=1)).date()

    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date")
    if from_date < _earliest:
        raise HTTPException(
            status_code=400,
            detail=f"from_date must be on or after {_SIMAP_EARLIEST_DATE_STR} (SIMAP launch date)",
        )

    days = (to_date - from_date).days + 1
    label = f"SIMAP backfill — {from_date} → {to_date} ({days} day{'s' if days != 1 else ''})"
    job = _enqueue_or_http_error(
        request,
        job_type="simap_backfill",
        label=label,
        params={
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "request_delay": body.request_delay,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


class SimapArchiveBody(BaseModel):
    from_date: str | None = None   # ISO date YYYY-MM-DD; defaults to 2007-01-01
    to_date: str | None = None     # ISO date YYYY-MM-DD; defaults to 2023-12-31
    request_delay: float = 0.10


@router.post("/collection/simap-archive", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_simap_archive(
    body: SimapArchiveBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Import pre-2024 SIMAP award notices from archiv.simap.ch (2007–2023).

    Fetches OB02 (Zuschlag) publications, de-duplicates by project, fuzzy-matches
    the contractor to our companies table by name + zip, and upserts into
    simap_awards / simap_award_vendors.  Archive IDs are prefixed with "arch-"
    to avoid collision with post-2024 simap.ch UUIDs.
    """
    from datetime import date

    _earliest = date(2007, 1, 1)
    _latest = date(2023, 12, 31)

    from_date: date | None = None
    to_date: date | None = None

    if body.from_date:
        try:
            from_date = date.fromisoformat(body.from_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid from_date: {exc}") from exc
    if body.to_date:
        try:
            to_date = date.fromisoformat(body.to_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid to_date: {exc}") from exc

    fd = from_date or _earliest
    td = to_date or _latest
    if fd > td:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date")

    label = f"SIMAP archive import — {fd} → {td}"
    job = _enqueue_or_http_error(
        request,
        job_type="simap_archive",
        label=label,
        params={
            "from_date": fd.isoformat(),
            "to_date": td.isoformat(),
            "request_delay": body.request_delay,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/shab-archive", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_shab_archive(
    body: ShabArchiveBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Import historical SHAB publications directly from the shab.ch archive API.

    Fetches the public archive page-by-page, downloads the PDF for each
    HR01/HR02/HR03 entry, extracts text, and upserts into ``sogc_publications``
    + ``sogc_changes``.  Supports pause/resume.

    Unlike the Zefix-backed SHAB backfill this does not touch the companies
    table — it only writes to the SOGC publication tables.  Use the SOGC
    preprocess job afterwards to link publications to companies via UID.
    """
    if body.date_start:
        label = (
            f"SHAB archive import — {body.date_start} → {body.date_end or 'today'} "
            f"({body.window_days}d windows)"
        )
    else:
        end_label = f"→{body.end_page}" if body.end_page is not None else "→end"
        label = f"SHAB archive import — pages {body.start_page}{end_label} (size={body.page_size})"
    job = _enqueue_or_http_error(
        request,
        job_type="shab_archive",
        label=label,
        params={
            "date_start": body.date_start,
            "date_end": body.date_end,
            "window_days": body.window_days,
            "start_page": body.start_page,
            "end_page": body.end_page,
            "page_size": body.page_size,
            "request_delay": body.request_delay,
            "pdf_delay": body.pdf_delay,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


class LinkSogcStubsBody(BaseModel):
    batch_size: int = 500


@router.post("/collection/link-sogc-stubs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_link_sogc_stubs(
    body: LinkSogcStubsBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Back-fill company_id on sogc_publications/sogc_person_appearances from existing data.

    Creates shab_stub Company rows for UIDs not yet in the companies table.
    No API calls — works entirely from already-imported sogc_publications rows.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="link_sogc_stubs",
        label="Link SOGC stubs from existing publications",
        params={"batch_size": body.batch_size},
        db=db,
    )
    return JobOut.from_orm_obj(job)


class ResolveShabOldUidsBody(BaseModel):
    batch_size: int = 200
    max_lookups: int | None = None   # cap reverse-Search API calls per run (rate ~17/min); None = unlimited


@router.post("/collection/resolve-shab-old-uids", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_resolve_shab_old_uids(
    body: ResolveShabOldUidsBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Link pre-2014 SHAB publications that cite an OLD cantonal HR number.

    Local ``old_uids`` overlap first (free); on a miss, reverse-Search the UID
    register by ``otherOrganisationId`` to get the modern UID, cache it into
    ``old_uids`` and link the publication (unknown companies get a modern-UID stub).
    Resumable; shares the UID SOAP rate limit — refuses to run alongside
    uid_import/uid_detail. Use ``max_lookups`` to bound API calls per run.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="resolve_shab_old_uids",
        label="Resolve SHAB old-UID publications (reverse UID Search)",
        params={"batch_size": body.batch_size, "max_lookups": body.max_lookups},
        db=db,
    )
    return JobOut.from_orm_obj(job)


class BackfillShabOldUidExtractionBody(BaseModel):
    batch_size: int = 500
    pdf_workers: int = 8
    request_delay: float = 0.5


@router.post("/collection/backfill-shab-old-uid-extraction", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_backfill_shab_old_uid_extraction(
    body: BackfillShabOldUidExtractionBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Re-extract old_uid/uid for publications whose raw_json never captured one.

    Targeted repair pass (not a full historical re-import): re-fetches only the
    specific PDFs behind publications with neither an extracted_uid nor an
    extracted_old_uid/ch_number, re-runs the current extraction, and updates
    raw_json. No UID SOAP calls — safe to run alongside uid_import/uid_detail/
    resolve_shab_old_uids.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="backfill_shab_old_uid_extraction",
        label="Backfill SHAB old-UID text extraction",
        params={
            "batch_size": body.batch_size,
            "pdf_workers": body.pdf_workers,
            "request_delay": body.request_delay,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/sogc-preprocess", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_sogc_preprocess(
    body: SogcPreprocessBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Preprocess SOGC publication history from sogc_pub / zefix_raw into structured tables."""
    uid_count = len(body.uids) if body.uids else 0
    if uid_count:
        label = f"SOGC preprocess — {uid_count} UID(s) (mode={body.mode})"
    else:
        label = f"SOGC preprocess — {body.mode}"
    job = _enqueue_or_http_error(
        request,
        job_type="sogc_preprocess",
        label=label,
        params={
            "mode": body.mode,
            "uids": body.uids or [],
            "batch_size": body.batch_size,
        },
        db=db,
    )
    return JobOut.from_orm_obj(job)


class ExtractSogcPersonsBody(BaseModel):
    mode: str = "missing"   # "missing" | "all"
    batch_size: int = 1000


@router.post(
    "/scoring/extract-sogc-persons",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_extract_sogc_persons(
    body: ExtractSogcPersonsBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Extract structured persons and auditors from SOGC change excerpts."""
    job = _enqueue_or_http_error(
        request,
        job_type="extract_sogc_persons",
        label=f"Extract SOGC persons — {body.mode}",
        params={"mode": body.mode, "batch_size": body.batch_size},
        db=db,
    )
    return JobOut.from_orm_obj(job)


class ResolveBisherLinksBody(BaseModel):
    batch_size: int = 500


@router.post(
    "/scoring/resolve-bisher-links",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_resolve_bisher_links(
    body: ResolveBisherLinksBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Resolve person entities using bisher hard links.

    Merges entities that belong to the same person but have different
    normalized keys due to name changes (e.g. Müller → Müller-Schneider).
    Run after extract_sogc_persons.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="resolve_bisher_links",
        label="Resolve bisher links",
        params={"batch_size": body.batch_size},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post(
    "/scoring/repair-is-current",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_repair_is_current(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Fix is_current flags on existing sogc_person_appearances in-place.

    Corrects rows where multiple non-removed appearances for the same person
    at the same company were all marked is_current=True.  Only the most recent
    one per (entity, company) group is set True; all earlier ones become False.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="repair_is_current",
        label="Repair SOGC is_current flags",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.get("/cantons")
def list_cantons():
    return {"cantons": SWISS_CANTONS}


# ── CSV Export job ──────────────────────────────────────────────────────────────

class CSVExportBody(BaseModel):
    sort: str = "-updated"
    q: str | None = None
    uid: str | None = None
    canton: str | None = None
    review_status: str | None = None
    contact_status: str | None = None
    google_searched: str | None = None
    min_web_score: int | None = None
    min_flex_score: int | None = None
    min_ai_score: int | None = None
    tags: str | None = None
    tfidf_cluster: str | None = None
    purpose_keywords: str | None = None
    noga_code: str | None = None
    noga_label: str | None = None
    noga_level: str | None = None
    exclude_tags: str | None = None
    exclude_review_status: str | None = None
    exclude_canton: str | None = None
    exclude_contact_status: str | None = None
    exclude_noga_code: str | None = None
    exclude_noga_label: str | None = None
    exclude_noga_level: str | None = None
    active_only: bool = True


class CSVExportStatusOut(BaseModel):
    job: JobOut | None
    download_url: str | None
    expires_at: str | None
    row_count: int | None
    capped: bool | None = None
    tier_limit: int | None = None
    total_matching: int | None = None
    upgrade_to: str | None = None


@router.post("/jobs/enqueue/csv-export", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def enqueue_csv_export(
    body: CSVExportBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue a CSV export with the current dashboard filters.

    Row count is capped by the org's tier limit (free=100, simple=1k, explorer=5k,
    researcher=20k, strategist=100k). Superadmins have no cap.

    Credits are deducted upfront based on the tier row cap (worst-case rows),
    rounded up to the nearest 10k unit. Action type: bulk_export_basic.
    Superadmin orgs with credits_unlimited=True are never blocked or charged.

    Max 1 active export per user — any queued/running export is cancelled first.
    The finished file is stored in S3 for 7 days.
    """
    from app.services.platform.s3_client import is_configured
    if not is_configured():
        raise HTTPException(status_code=503, detail="S3 export storage is not configured on this server")

    # Determine and embed the tier row cap into params so the worker honours it.
    if current_user.is_superadmin:
        row_limit = None  # unlimited for superadmins
        org = None
    elif current_user.org_id:
        org = db.get(Organization, current_user.org_id)
        row_limit = get_export_limit(org) if org else 100
    else:
        org = None
        row_limit = 100  # free/org-less users get the free tier cap

    # Tier-aware rate limit (free: 1/15 min, simple: 3/10 min, explorer+: 5/10 min).
    org_tier = normalize_tier(org.tier) if org else "free"
    check_job_rate_limit(request, current_user, "csv_export", org_tier=org_tier)

    # Deduct credits before enqueuing. We charge for the tier cap (worst case)
    # rounded up to the nearest 10k unit so the cost is deterministic. The
    # deduction is recorded on the job (credit_deduction) so a failed/cancelled
    # export is fully refunded — the enqueue path must NOT charge again.
    _csv_deduction = None
    if current_user.org_id and not current_user.is_superadmin:
        cap = row_limit or 0
        units = max(1, -(-cap // 10_000))  # ceiling division
        if not credits_service.check_and_deduct(
            db,
            current_user.org_id,
            "bulk_export_basic",
            units,
            reference_id=f"csv_export_user_{current_user.id}",
        ):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Insufficient credits. A {cap:,}-row export costs "
                    f"{credits_service.compute_cost('bulk_export_basic', units):,} credits."
                ),
            )
        _csv_deduction = {
            "action": "bulk_export_basic",
            "count": units,
            "cost": credits_service.compute_cost("bulk_export_basic", units),
            "prorate": False,
            "source": "route",
        }

    crud.cancel_active_csv_exports(db, user_id=current_user.id)

    params = body.model_dump()
    params["row_limit"] = row_limit  # worker reads this to cap the export

    tier_label = ""
    if row_limit is not None:
        tier_label = f" (tier cap: {row_limit:,} rows)"

    job = _enqueue_or_http_error(
        request,
        job_type="csv_export",
        label=f"CSV export{tier_label}",
        params=params,
        db=db,
        org_id=current_user.org_id,
        user_id=current_user.id,
        credit_deduction=_csv_deduction,
    )
    return JobOut.from_orm_obj(job)


@router.get("/jobs/csv-export/status", response_model=CSVExportStatusOut)
def get_csv_export_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the latest CSV export job for the current user and a presigned download URL if ready."""
    import json as _json
    from datetime import datetime, timezone as _tz

    job = crud.get_latest_csv_export(db, user_id=current_user.id)
    if job is None:
        return CSVExportStatusOut(job=None, download_url=None, expires_at=None, row_count=None)

    download_url = None
    expires_at = None
    row_count = None
    capped = None
    tier_limit = None
    total_matching = None
    upgrade_to = None

    if job.status == "completed" and job.stats_json:
        try:
            s = _json.loads(job.stats_json)
            s3_key = s.get("s3_key")
            exp = s.get("expires_at")
            row_count = s.get("row_count")
            capped = s.get("capped")
            tier_limit = s.get("tier_limit")
            total_matching = s.get("total_matching")
            upgrade_to = s.get("upgrade_to")
            if s3_key and exp:
                # Only generate URL if not expired
                exp_dt = datetime.fromisoformat(exp)
                if exp_dt > datetime.now(tz=_tz.utc):
                    from app.services.platform.s3_client import generate_presigned_url
                    remaining = int((exp_dt - datetime.now(tz=_tz.utc)).total_seconds())
                    download_url = generate_presigned_url(s3_key, expires_in=min(remaining, 3600))
                    expires_at = exp
        except Exception:  # noqa: BLE001
            pass

    return CSVExportStatusOut(
        job=JobOut.from_orm_obj(job),
        download_url=download_url,
        expires_at=expires_at,
        row_count=row_count,
        capped=capped,
        tier_limit=tier_limit,
        total_matching=total_matching,
        upgrade_to=upgrade_to,
    )


# ── Web crawler endpoints ──────────────────────────────────────────────────────

class WebUrlPopulateBody(BaseModel):
    batch_size: int = 500


class WebCrawlBody(BaseModel):
    batch_size: int = 20
    canton: str | None = None
    max_pages: int = 5           # total pages per domain (homepage + subpages)
    rate_limit_delay: float = 0.5  # seconds between requests to the same domain (0 = disabled)


class WebCrawlHttpBody(WebCrawlBody):
    rerun: bool = False          # reset previously crawled/failed HTTP rows (including js_required) before starting
    order_by: str = "company_id_asc"  # company_id_asc | last_crawled_asc | flex_score_desc | combined_score_desc
    limit: int | None = None     # stop after this many companies (None = all)


class WebCrawlPlaywrightBody(WebCrawlBody):
    batch_size: int = 10
    rerun: bool = False          # reset previously crawled/failed playwright rows before starting
    order_by: str = "company_id_asc"  # company_id_asc | last_crawled_asc | flex_score_desc | combined_score_desc
    limit: int | None = None     # stop after this many companies (None = all)


class WebExtractBody(BaseModel):
    batch_size: int = 200


class WebSelectUrlBody(BaseModel):
    company_id: int
    url_candidate_id: int


@router.post("/crawler/populate-urls", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_web_url_populate(
    body: WebUrlPopulateBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Seed company_url_candidates from existing company_search_results.

    Run the batch enrichment job first so companies have stored search results.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="web_url_populate",
        label="Web URL population — seed candidates from search results",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/crawler/crawl-http", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_web_crawl_http(
    body: WebCrawlHttpBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Trigger HTTP crawler — fast path using httpx, no browser."""
    parts = []
    if body.canton:
        parts.append(body.canton)
    if body.rerun:
        parts.append("rerun")
    if body.order_by != "company_id_asc":
        parts.append(body.order_by)
    if body.limit:
        parts.append(f"limit {body.limit}")
    label = "HTTP crawler" + (f" — {', '.join(parts)}" if parts else "")
    job = _enqueue_or_http_error(
        request,
        job_type="web_crawl_http",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/crawler/crawl-playwright", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_web_crawl_playwright(
    body: WebCrawlPlaywrightBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Trigger Playwright crawler — JS-rendering path for bot-protected / JS-heavy sites."""
    parts = []
    if body.canton:
        parts.append(body.canton)
    if body.rerun:
        parts.append("rerun")
    if body.order_by != "company_id_asc":
        parts.append(body.order_by)
    if body.limit:
        parts.append(f"limit {body.limit}")
    label = "Playwright crawler" + (f" — {', '.join(parts)}" if parts else "")
    job = _enqueue_or_http_error(
        request,
        job_type="web_crawl_playwright",
        label=label,
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


class WebCrawlSingleBody(BaseModel):
    company_id: int
    max_pages: int = 5
    rate_limit_delay: float = 0.5
    force: bool = False  # re-crawl even if already crawled


@router.post("/crawler/crawl-single", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_web_crawl_single(
    body: WebCrawlSingleBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Crawl a single company end-to-end.

    Populates URL candidates from stored Serper results if needed, then crawls
    via HTTP (with automatic Playwright fallback if JS rendering is required).
    Pass force=true to re-crawl a company that has already been crawled.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="web_crawl_single",
        label=f"Single crawl — company {body.company_id}",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/crawler/extract", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_web_extract(
    body: WebExtractBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Extract structured data (contacts, UID, socials, keywords) from crawled HTML.

    Reads raw HTML stored in S3 by the crawlers and writes one resolved row per
    company into company_web_extract. Deterministic only — no API cost.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="web_extract",
        label="Web extraction — structured data from crawled HTML",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/crawler/reextract", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_web_reextract(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Flag every crawled page (HTML already in S3) for re-extraction, then run web_extract.

    Use after improving the extractor — reprocesses all stored HTML at zero crawl cost.
    """
    from app.crud.crawler import reset_extraction_flags
    reset_extraction_flags(db)
    db.commit()
    job = _enqueue_or_http_error(
        request,
        job_type="web_extract",
        label="Re-extract all crawled HTML (no re-crawl)",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/crawler/enrich-purpose-sim", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_enrich_web_purpose_sim(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Compute purpose↔site semantic similarity for company_web_extract rows.

    Embeds the company's Zefix purpose text and the crawled site's description/
    service_keywords using paraphrase-multilingual-mpnet-base-v2 (same model as NOGA),
    stores cosine similarity as purpose_sim (0–1) in company_web_extract. This feeds
    into website_status._extract_tier() as a 4th confidence layer. Requires the ML
    worker (sentence-transformers). No API cost.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="enrich_web_purpose_sim",
        label="Enrich web extract purpose similarity (ML worker)",
        params={"only_missing": True},
        db=db,
    )
    return JobOut.from_orm_obj(job)


class DiscoverDirectoryDomainsBody(BaseModel):
    min_companies: int = 30
    limit: int = 200


@router.post("/crawler/discover-directory-domains", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_discover_directory_domains(
    body: DiscoverDirectoryDomainsBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Scan company_url_candidates for high-frequency domains not yet in the managed list.

    Domains appearing for >= min_companies companies that are not already blocked or known
    are inserted into directory_crawl_domains as pending_review for admin evaluation.
    Run after large Google enrichment batches to surface new directories.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="discover_directory_domains",
        label=f"Discover directory domains (≥{body.min_companies} companies)",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


class DirectoryCrawlBody(BaseModel):
    batch_size: int = 100
    limit: int | None = None
    timeout: float = 15.0
    rate_limit_delay: float = 0.3
    rerun: bool = False


@router.post("/crawler/directory-crawl", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_directory_crawl(
    body: DirectoryCrawlBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Crawl business directory profile pages for company context enrichment.

    Fetches HTML from directory sites (moneyhouse.ch, local.ch, northdata.com, etc.)
    that appear in company_url_candidates, extracts text + ratings + categories, and
    stores results in company_directory_data. This data feeds into Claude AI scoring
    as additional context about what the company does and how it is rated externally.
    No S3 — text stored directly in the DB (capped at 5000 chars per page).
    """
    job = _enqueue_or_http_error(
        request,
        job_type="directory_crawl",
        label="Directory crawl — profile pages (moneyhouse, local.ch, northdata…)",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/crawler/recompute-website-status", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_recompute_website_status(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Recompute the company-level website verdict (website_status + website_count).

    Aggregates crawl-verification extracts + search results into verified / confirmed /
    likely / social_only / directory_only / none, and counts distinct genuine websites.
    Deterministic — no API or crawl cost. Use to backfill or after tuning thresholds.
    """
    job = _enqueue_or_http_error(
        request,
        job_type="recompute_website_status",
        label="Recompute website status (verdict + multi-site count)",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/crawler/select-url", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_web_select_url(
    body: WebSelectUrlBody,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Switch the selected URL candidate for a company and reset its crawl state to pending."""
    job = _enqueue_or_http_error(
        request,
        job_type="web_select_url",
        label=f"Select URL candidate {body.url_candidate_id} for company {body.company_id}",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)
