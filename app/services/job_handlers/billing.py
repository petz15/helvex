"""Handler for billing_renewal job."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_billing_renewal(ctx: JobContext) -> tuple[dict, str]:
    from app.services.billing_renewal import run_billing_renewal
    from app import crud

    crud.update_progress(ctx.db, ctx.job, message="Running subscription billing renewal…")
    stats = run_billing_renewal(ctx.db)
    return stats, (
        f"Done — {stats['renewed']} renewed, "
        f"{stats['cancelled']} cancelled, "
        f"{stats['failed']} failed, "
        f"{stats['skipped']} skipped"
    )
