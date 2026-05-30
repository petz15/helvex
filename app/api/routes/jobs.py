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
from app.services.rate_limit import check_job_rate_limit, check_rate_limit
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.services import credits as credits_service
from app.services.job_worker import enqueue_job
from app.services.tiers import get_export_limit, has_feature, normalize_tier


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
):
    try:
        return enqueue_job(request.app, job_type=job_type, label=label, params=params, db=db, org_id=org_id, user_id=user_id)
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
        key=lambda j: j.queued_at or j.started_at,
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
    from app.services.job_worker import kick_job_worker
    kick_job_worker(request.app)
    return JobOut.from_orm_obj(job)


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
            key=lambda j: j.queued_at or j.started_at,
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
    canton: str | None = None
    min_zefix_score: int | None = None   # passed as-is to job params; worker maps to min_flex_score
    min_claude_score: int | None = None  # passed as-is to job params; worker maps to min_ai_score
    purpose_keywords: str | None = None
    tfidf_cluster: str | None = None
    review_status: str | None = None


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
        job_type="batch",
        label=f"Batch enrichment — up to {body.limit} companies",
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
    from app.services.collection import claude_classify_batch
    import anthropic as _anthropic

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
    from app.services.boilerplate_analysis import seed_multilang_boilerplate
    seed_multilang_boilerplate(db)
    job = _enqueue_or_http_error(
        request,
        job_type="analyze_boilerplate",
        label="Boilerplate pattern analysis",
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
    from app.services.s3_client import is_configured
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
    # rounded up to the nearest 10k unit so the cost is deterministic.
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
                    from app.services.s3_client import generate_presigned_url
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
