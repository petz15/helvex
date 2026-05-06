from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JobRun(Base):
    """Persistent queued/running background jobs started from the UI."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    pause_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Org/user context: NULL for superadmin catalog jobs, set for org-triggered jobs
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    # Dedup: prevents enqueueing duplicate active jobs (same type + org).
    # NULL means no dedup enforced for this job type (e.g. batch, csv_export).
    dedup_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Heartbeat: updated every ~30 s by the worker while the job is running.
    # requeue_interrupted_jobs() skips jobs with a recent heartbeat so that
    # live worker-pod jobs are never double-executed after a web-pod restart.
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Incremented each time the job is re-queued after a pod crash or restart.
    # Jobs exceeding MAX_RESTART_COUNT are killed instead of re-queued.
    restart_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
