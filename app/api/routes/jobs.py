"""REST API for job management and collection/scoring triggers."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.api.zefix_client import SWISS_CANTONS
from app.database import get_db
from app.services.job_worker import enqueue_job

router = APIRouter(tags=["jobs"])


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
def list_jobs(limit: int = 100, db: Session = Depends(get_db)):
    return [JobOut.from_orm_obj(j) for j in crud.list_jobs(db, limit=limit)]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut.from_orm_obj(job)


@router.get("/jobs/{job_id}/events", response_model=list[EventOut])
def get_job_events(job_id: int, db: Session = Depends(get_db)):
    return [EventOut.from_orm_obj(e) for e in crud.list_events(db, job_id=job_id, limit=200, exclude_debug=False)]


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
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
def pause_job(job_id: int, db: Session = Depends(get_db)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "running":
        raise HTTPException(status_code=400, detail="Only running jobs can be paused")
    crud.mark_pause_requested(db, job)
    crud.create_event(db, job_id=job.id, level="info", message="Pause requested")
    return JobOut.from_orm_obj(job)


@router.post("/jobs/{job_id}/resume", response_model=JobOut)
def resume_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "paused":
        raise HTTPException(status_code=400, detail="Only paused jobs can be resumed")
    crud.resume_paused_job(db, job)
    crud.create_event(db, job_id=job.id, level="info", message=f"Resumed from {job.progress_done or 0}")
    from app.services.job_worker import kick_job_worker
    kick_job_worker(request.app)
    return JobOut.from_orm_obj(job)


@router.get("/jobs/stream/active")
def stream_active_jobs(db: Session = Depends(get_db)):
    """SSE stream that sends 'update' while active jobs exist, 'done' when all finish."""
    def event_generator():
        while True:
            active = crud.list_active_jobs(db)
            if not active:
                yield "data: done\n\n"
                return
            yield "data: update\n\n"
            time.sleep(2)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Collection triggers ────────────────────────────────────────────────────────

class BulkImportBody(BaseModel):
    cantons: list[str] | None = None
    active_only: bool = True
    delay: float = 0.5


class BatchCollectBody(BaseModel):
    limit: int = 100
    only_missing_website: bool = True
    refresh_zefix: bool = False
    run_google: bool = True
    canton: str | None = None
    min_zefix_score: int | None = None
    min_claude_score: int | None = None
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


class ClaudeClassifyBody(BaseModel):
    canton: str | None = None
    min_zefix_score: int | None = None
    max_zefix_score: int | None = None
    min_google_score: int | None = None
    purpose_keywords: str | None = None
    rerun_classified: bool = False
    auto_filter_keywords: bool = False
    use_fixed_categories: bool = False
    limit: int = 500
    system_prompt: str | None = None
    use_batch_api: bool = False
    companies_per_message: int = 1


class ClusterPipelineBody(BaseModel):
    n_clusters: int = 150
    max_clusters_per_company: int = 7
    min_similarity: float = 0.10
    n_components: int = 50
    top_terms: int = 5
    top_keywords_per_company: int = 10
    canton: str | None = None
    min_zefix_score: int | None = None
    max_zefix_score: int | None = None
    limit: int | None = None
    use_keywords: bool = False


@router.post("/collection/bulk", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_bulk(body: BulkImportBody, request: Request, db: Session = Depends(get_db)):
    canton_list = [c.upper() for c in body.cantons] if body.cantons else None
    label = f"Bulk import — cantons: {', '.join(canton_list) if canton_list else 'all 26'}"
    job = enqueue_job(
        request.app,
        job_type="bulk",
        label=label,
        params={"cantons": canton_list, "active_only": body.active_only, "delay": body.delay},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/batch", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_batch(body: BatchCollectBody, request: Request, db: Session = Depends(get_db)):
    job = enqueue_job(
        request.app,
        job_type="batch",
        label=f"Batch enrichment — up to {body.limit} companies",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/collection/initial", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_initial(body: InitialCollectBody, request: Request, db: Session = Depends(get_db)):
    if not body.names and not body.uids:
        raise HTTPException(status_code=400, detail="Provide at least one name or UID")
    label = f"Specific search — {len(body.names)} name(s), {len(body.uids)} UID(s)"
    job = enqueue_job(request.app, job_type="initial", label=label, params=body.model_dump(), db=db)
    return JobOut.from_orm_obj(job)


@router.post("/collection/detail", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_detail(body: DetailCollectBody, request: Request, db: Session = Depends(get_db)):
    if body.cantons:
        label = f"Zefix detail fetch — cantons: {', '.join(body.cantons)}"
    elif body.uids:
        label = f"Zefix detail fetch — {len(body.uids)} UID(s)"
    else:
        label = "Zefix detail fetch — all matching companies"
    if body.only_missing_details:
        label += " (missing details only)"
    job = enqueue_job(request.app, job_type="detail", label=label, params=body.model_dump(), db=db)
    return JobOut.from_orm_obj(job)


@router.post("/scoring/zefix", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_recalc_zefix(request: Request, db: Session = Depends(get_db)):
    job = enqueue_job(
        request.app,
        job_type="recalculate_scores",
        label="Recalculate Zefix scores",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/google", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_recalc_google(request: Request, db: Session = Depends(get_db)):
    job = enqueue_job(
        request.app,
        job_type="recalculate_google_scores",
        label="Recalculate Google scores",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/re-geocode", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_re_geocode(request: Request, db: Session = Depends(get_db)):
    job = enqueue_job(
        request.app,
        job_type="re_geocode",
        label="Re-geocode all companies",
        params={},
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/claude", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_claude_classify(body: ClaudeClassifyBody, request: Request, db: Session = Depends(get_db)):
    job = enqueue_job(
        request.app,
        job_type="claude_classify",
        label=f"Claude classify — up to {body.limit} companies",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.post("/scoring/cluster", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_cluster_pipeline(body: ClusterPipelineBody, request: Request, db: Session = Depends(get_db)):
    job = enqueue_job(
        request.app,
        job_type="hdbscan_cluster",
        label="Cluster pipeline",
        params=body.model_dump(),
        db=db,
    )
    return JobOut.from_orm_obj(job)


@router.get("/cantons")
def list_cantons():
    return {"cantons": SWISS_CANTONS}
