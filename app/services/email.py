"""Transactional email sending via SMTP (STARTTLS).

All outbound emails go through `send_email()`. If SMTP is not configured the
function logs a warning and returns without raising — this keeps dev/test
environments working without an SMTP server.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(*, to: str, subject: str, html: str, text: str | None = None) -> None:
    """Send a transactional email.

    Silently skips if SMTP is not configured (dev mode).
    Raises on SMTP errors so callers can surface them to the user.
    """
    is_prod_like = (settings.app_env or "").lower().strip() in {"prod", "production", "staging"}
    if not settings.smtp_host or not settings.smtp_from:
        msg = f"SMTP not configured (SMTP_HOST/SMTP_FROM missing) — cannot send email to {to} (subject: {subject})"
        if is_prod_like:
            raise RuntimeError(msg)
        logger.warning("%s", msg)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to

    if text:
        msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.sendmail(settings.smtp_from, to, msg.as_string())

    logger.info("Email sent to %s — %s", to, subject)


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

def send_verification_email(*, to: str, token: str) -> None:
    link = f"{settings.app_base_url}/verify-email?token={token}"
    subject = "Verify your Helvex email address"
    html = f"""
    <p>Please verify your email address by clicking the link below.
    The link is valid for 24 hours.</p>
    <p><a href="{link}">{link}</a></p>
    <p>If you did not create a Helvex account, you can ignore this email.</p>
    """
    text = f"Verify your email: {link}\n\nLink valid for 24 hours.\n"
    send_email(to=to, subject=subject, html=html, text=text)


def send_password_reset_email(*, to: str, token: str) -> None:
    link = f"{settings.app_base_url}/reset-password?token={token}"
    subject = "Reset your Helvex password"
    html = f"""
    <p>You requested a password reset. Click the link below to set a new password.
    The link expires in <strong>1 hour</strong>.</p>
    <p><a href="{link}">{link}</a></p>
    <p>If you did not request this, you can safely ignore this email — your password has not changed.</p>
    """
    text = f"Reset your password: {link}\n\nLink expires in 1 hour.\n"
    send_email(to=to, subject=subject, html=html, text=text)


def send_welcome_email(*, to: str) -> None:
    subject = "Welcome to Helvex"
    html = f"""
    <p>Your email address has been verified. Welcome to Helvex!</p>
    <p><a href="{settings.app_base_url}/app/dashboard">Open the dashboard →</a></p>
    """
    text = f"Your email is verified. Open the dashboard: {settings.app_base_url}/app/dashboard\n"
    send_email(to=to, subject=subject, html=html, text=text)


def send_invite_email(*, to: str, org_name: str, invited_by_email: str, token: str) -> None:
    link = f"{settings.app_base_url}/accept-invite?token={token}"
    subject = f"You've been invited to join {org_name} on Helvex"
    html = f"""
    <p>{invited_by_email} has invited you to join <strong>{org_name}</strong> on Helvex.</p>
    <p>Click the link below to accept the invitation. The link is valid for <strong>7 days</strong>.</p>
    <p><a href="{link}">{link}</a></p>
    <p>If you don't have a Helvex account yet, you'll be able to create one when you accept.</p>
    <p>If you were not expecting this invitation, you can safely ignore this email.</p>
    """
    text = (
        f"{invited_by_email} invited you to join {org_name} on Helvex.\n\n"
        f"Accept the invitation: {link}\n\n"
        "Link valid for 7 days.\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_capture_failure_alert(*, to: str, failed_tx_ids: list[int]) -> None:
    """Alert the operator that Saferpay capture retries failed.

    Called by the nightly billing_renewal job when one or more authorized
    transactions could not be captured after retry.  Funds are reserved at
    Saferpay but will NOT be settled unless manually captured before expiry.
    """
    from datetime import datetime, timezone
    ids_str = ", ".join(f"#{i}" for i in failed_tx_ids)
    count = len(failed_tx_ids)
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    admin_url = f"{settings.app_base_url}/app/admin/billing"

    subject = f"[ACTION REQUIRED] {count} Saferpay capture(s) failed — {now_str}"
    html = f"""
    <p><strong>⚠️ {count} Worldline/Saferpay transaction(s) could not be captured during the nightly billing renewal.</strong></p>
    <p>These payments are <em>authorized</em> (funds reserved) but <strong>not yet settled</strong>.
    Without a successful capture, Saferpay will release the authorization after ~7 days and you will not receive the money.</p>
    <table style="border-collapse:collapse;font-family:monospace;">
      <tr>
        <th style="text-align:left;padding:4px 12px 4px 0;border-bottom:1px solid #ccc;">Payment Transaction IDs</th>
      </tr>
      {"".join(f'<tr><td style="padding:4px 12px 4px 0;">#{tid}</td></tr>' for tid in failed_tx_ids)}
    </table>
    <p><strong>What to do:</strong></p>
    <ol>
      <li>Open <a href="{admin_url}">Admin → Billing</a> and locate each transaction above.</li>
      <li>Verify the Saferpay transaction ID (Provider tx ID) is present.</li>
      <li>Manually trigger a capture via the Saferpay Back Office, or fix the configuration issue and redeploy so the next nightly run can retry.</li>
    </ol>
    <p style="color:#666;font-size:12px;">Sent by the nightly billing_renewal job at {now_str}. Transaction IDs: {ids_str}</p>
    """
    text = (
        f"ACTION REQUIRED: {count} Saferpay capture(s) failed during nightly billing renewal ({now_str}).\n\n"
        f"Transaction IDs: {ids_str}\n\n"
        f"Funds are reserved but not settled. Log in to the admin panel to investigate:\n{admin_url}\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_email_change_verification(*, to: str, token: str) -> None:
    link = f"{settings.app_base_url}/confirm-email-change?token={token}"
    subject = "Confirm your new Helvex email address"
    html = f"""
    <p>You requested to change your Helvex email address to this address.</p>
    <p>Click the link below to confirm the change. The link expires in <strong>1 hour</strong>.</p>
    <p><a href="{link}">{link}</a></p>
    <p>If you did not request this change, you can safely ignore this email.</p>
    """
    text = f"Confirm your new email address: {link}\n\nLink expires in 1 hour.\n"
    send_email(to=to, subject=subject, html=html, text=text)


# ── Transactional notification templates ─────────────────────────────────────


def send_low_credit_alert(
    *,
    to: str,
    org_name: str,
    balance: int,
    threshold: int,
) -> None:
    """Alert an org admin that the credit balance has dropped below the threshold."""
    topup_url = f"{settings.app_base_url}/app/billing"
    balance_chf = f"{balance * 0.0001:.2f}"
    subject = f"[Helvex] Low credit balance for {org_name}"
    html = f"""
    <p>Your organisation <strong>{org_name}</strong> has a low credit balance.</p>
    <p>Current balance: <strong>{balance:,} credits (CHF {balance_chf})</strong></p>
    <p>AI scoring and other credit-based features will stop working when the balance reaches zero.</p>
    <p><a href="{topup_url}">Top up credits →</a></p>
    <p style="color:#888;font-size:12px;">
      You can disable these alerts under Billing → Notification Settings.
    </p>
    """
    text = (
        f"Low credit balance for {org_name}.\n\n"
        f"Current balance: {balance:,} credits (CHF {balance_chf})\n\n"
        f"Top up at: {topup_url}\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_export_ready(
    *,
    to: str,
    row_count: int,
    job_id: int,
    download_url: str,
) -> None:
    """Notify the user that their CSV export is ready to download."""
    jobs_url = f"{settings.app_base_url}/app/jobs"
    subject = "[Helvex] Your CSV export is ready"
    html = f"""
    <p>Your CSV export (<strong>{row_count:,} rows</strong>) has finished and is ready to download.</p>
    <p>The file is available for 7 days.</p>
    {"<p><a href='" + download_url + "'>Download CSV →</a></p>" if download_url else ""}
    <p>Or open the <a href="{jobs_url}">Jobs page</a> to download from there.</p>
    """
    text = (
        f"Your CSV export ({row_count:,} rows) is ready.\n\n"
        + (f"Download: {download_url}\n\n" if download_url else "")
        + f"Jobs page: {jobs_url}\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_job_failed(
    *,
    to: str,
    job_type: str,
    label: str,
    job_id: int,
    summary: str,
) -> None:
    """Notify the user that a job they started has failed."""
    jobs_url = f"{settings.app_base_url}/app/jobs"
    subject = f"[Helvex] Job failed: {label}"
    html = f"""
    <p>A background job you started has failed.</p>
    <table style="font-size:14px;border-collapse:collapse;">
      <tr><td style="padding:3px 12px 3px 0;color:#888;">Job</td><td><strong>{label}</strong></td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#888;">Type</td><td>{job_type}</td></tr>
      <tr><td style="padding:3px 12px 3px 0;color:#888;">Error</td><td style="color:#c00;">{summary}</td></tr>
    </table>
    <p><a href="{jobs_url}">View in Jobs →</a></p>
    """
    text = (
        f"Job failed: {label}\n"
        f"Type: {job_type}\n"
        f"Error: {summary}\n\n"
        f"Details: {jobs_url}\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_saved_view_alert(
    *,
    to: str,
    view_name: str,
    new_count: int,
    previous_count: int,
    view_url: str,
) -> None:
    """Alert a user that new companies match their saved search."""
    noun = "company" if new_count == 1 else "companies"
    verb = "matches" if new_count == 1 else "match"
    subject = f'[Helvex] {new_count} new {noun} {verb} "{view_name}"'
    html = f"""
    <p><strong>{new_count} new {'company' if new_count == 1 else 'companies'}</strong>
    {'matches' if new_count == 1 else 'match'} your saved search
    <strong>"{view_name}"</strong>.</p>
    <p>Total matching: {previous_count + new_count:,} (was {previous_count:,})</p>
    <p><a href="{view_url}">View results →</a></p>
    <p style="color:#888;font-size:12px;">
      Disable this alert by clicking the bell icon next to "{view_name}" in the filter bar.
    </p>
    """
    text = (
        f"{new_count} new {'company' if new_count == 1 else 'companies'} match your saved search \"{view_name}\".\n\n"
        f"Total: {previous_count + new_count:,} (was {previous_count:,})\n\n"
        f"View results: {view_url}\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)
