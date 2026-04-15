"""Activity logging service.

Call ``log_activity()`` from any route to record a user action.
The call is best-effort: exceptions are caught and logged, never re-raised,
so a logging failure never breaks the user-facing request.

Action catalogue
----------------
Auth
  user_registered       — new account created via /auth/register
  user_login            — successful cookie or token login
  email_verified        — user clicked verification link and confirmed email
  password_changed      — user changed their own password
  email_changed         — user confirmed an email address change

Companies
  company_viewed        — user opened a company detail page
  company_updated       — user patched one or more company fields
  company_deleted       — user deleted a company record
  company_exported      — user enqueued a CSV export job
  company_bulk_updated  — user bulk-updated a status field across N companies
  company_bulk_tagged   — user added/removed a tag across N companies
  company_website_set   — user selected a website from Google results

Views (saved filters)
  view_created          — user saved a new dashboard view
  view_deleted          — user deleted a saved view
  view_alert_toggled    — user enabled or disabled new-company alert on a view

Notes
  note_created          — user added a note to a company
  note_deleted          — user deleted a note

Org / members
  org_state_updated     — org-level overlay (review/contact/tags) updated
  org_member_invited    — invitation sent to a new org member
  org_member_removed    — member removed from org
  org_created           — new organisation created

Admin (superadmin actions)
  admin_user_updated    — superadmin changed a user's status/flags
  admin_credit_granted  — superadmin granted credits to an org
  admin_credit_deducted — superadmin deducted credits from an org
  admin_org_updated     — superadmin updated an org's name or tier
  admin_org_deleted     — superadmin deleted an org

Jobs
  job_enqueued          — background job enqueued (type in meta.job_type)
  job_cancelled         — user cancelled a running job
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# All valid action slugs — used for documentation; not enforced at runtime.
ACTIONS: frozenset[str] = frozenset({
    # auth
    "user_registered",
    "user_login",
    "email_verified",
    "password_changed",
    "email_changed",
    # companies
    "company_viewed",
    "company_updated",
    "company_deleted",
    "company_exported",
    "company_bulk_updated",
    "company_bulk_tagged",
    "company_website_set",
    # views
    "view_created",
    "view_deleted",
    "view_alert_toggled",
    # notes
    "note_created",
    "note_deleted",
    # org / members
    "org_state_updated",
    "org_member_invited",
    "org_member_removed",
    "org_created",
    # admin
    "admin_user_updated",
    "admin_credit_granted",
    "admin_credit_deducted",
    "admin_org_updated",
    "admin_org_deleted",
    # jobs
    "job_enqueued",
    "job_cancelled",
})


def log_activity(
    db: Session,
    *,
    action: str,
    user_id: int | None = None,
    org_id: int | None = None,
    resource_type: str | None = None,
    resource_id: int | None = None,
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Insert one ActivityLog row.  Never raises — failures are logged and swallowed."""
    try:
        from app.models.activity_log import ActivityLog  # local import avoids circular

        entry = ActivityLog(
            action=action,
            user_id=user_id,
            org_id=org_id,
            resource_type=resource_type,
            resource_id=resource_id,
            meta=meta or None,
            ip=ip,
        )
        db.add(entry)
        db.flush()  # write within the caller's transaction; caller commits
    except Exception:  # noqa: BLE001
        logger.warning("activity_log.write_failed action=%s user_id=%s", action, user_id, exc_info=True)
