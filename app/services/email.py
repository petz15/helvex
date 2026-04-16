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
# Shared HTML layout
# ---------------------------------------------------------------------------

def _layout(body_html: str, footer_html: str = "") -> str:
    """Wrap content in a clean, email-client-safe HTML shell."""
    settings_url = f"{settings.app_base_url}/app/billing"
    default_footer = (
        f'<a href="{settings_url}" style="color:#6b7280;text-decoration:none;">Manage notification settings</a>'
        f' &nbsp;·&nbsp; <a href="{settings.app_base_url}" style="color:#6b7280;text-decoration:none;">Helvex</a>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Helvex</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:40px 16px;">
    <tr>
      <td align="center">
        <!-- Logo / brand -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
          <tr>
            <td style="padding-bottom:20px;">
              <span style="font-size:18px;font-weight:700;color:#111827;letter-spacing:-0.3px;">Helvex</span>
            </td>
          </tr>
        </table>
        <!-- Card -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e5e7eb;">
          <tr>
            <td style="padding:32px 36px 28px;">
              {body_html}
            </td>
          </tr>
        </table>
        <!-- Footer -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
          <tr>
            <td style="padding:20px 0 0;text-align:center;font-size:12px;color:#9ca3af;line-height:1.6;">
              {footer_html or default_footer}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _btn(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;margin-top:20px;padding:10px 20px;'
        f'background:#2563eb;color:#ffffff;text-decoration:none;border-radius:7px;'
        f'font-size:14px;font-weight:600;letter-spacing:0.1px;">{label}</a>'
    )


def _h1(text: str) -> str:
    return f'<h1 style="margin:0 0 16px;font-size:20px;font-weight:700;color:#111827;letter-spacing:-0.3px;">{text}</h1>'


def _p(text: str, muted: bool = False) -> str:
    colour = "#6b7280" if muted else "#374151"
    return f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:{colour};">{text}</p>'


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

def send_verification_email(*, to: str, token: str) -> None:
    link = f"{settings.app_base_url}/verify-email?token={token}"
    subject = "Verify your Helvex email address"
    body = (
        _h1("Confirm your email address")
        + _p("Click the button below to verify your email. The link is valid for <strong>24 hours</strong>.")
        + _btn(link, "Verify email address")
        + _p(f'Or copy this link into your browser:<br><span style="word-break:break-all;font-size:13px;color:#6b7280;">{link}</span>')
        + _p("If you didn't create a Helvex account, you can safely ignore this email.", muted=True)
    )
    footer = '<span style="color:#9ca3af;">This is a transactional email sent because you created a Helvex account.</span>'
    html = _layout(body, footer)
    text = f"Verify your Helvex email address\n\n{link}\n\nLink valid for 24 hours. If you didn't sign up, ignore this email.\n"
    send_email(to=to, subject=subject, html=html, text=text)


def send_password_reset_email(*, to: str, token: str) -> None:
    link = f"{settings.app_base_url}/reset-password?token={token}"
    subject = "Reset your Helvex password"
    body = (
        _h1("Reset your password")
        + _p("We received a request to reset the password for your Helvex account. Click the button below — the link expires in <strong>1 hour</strong>.")
        + _btn(link, "Reset password")
        + _p(f'Or copy this link:<br><span style="word-break:break-all;font-size:13px;color:#6b7280;">{link}</span>')
        + _p("If you didn't request a password reset, your account is safe — nothing has changed.", muted=True)
    )
    footer = '<span style="color:#9ca3af;">This is a transactional security email sent because a password reset was requested.</span>'
    html = _layout(body, footer)
    text = f"Reset your Helvex password\n\n{link}\n\nLink expires in 1 hour. If you didn't request this, ignore this email.\n"
    send_email(to=to, subject=subject, html=html, text=text)


def send_welcome_email(*, to: str) -> None:
    dashboard_url = f"{settings.app_base_url}/app/dashboard"
    subject = "Welcome to Helvex"
    body = (
        _h1("You're all set!")
        + _p("Your email address has been verified. You can now use all Helvex features.")
        + _btn(dashboard_url, "Open dashboard")
    )
    footer = '<span style="color:#9ca3af;">This is a transactional email sent because you verified your Helvex account.</span>'
    html = _layout(body, footer)
    text = f"Welcome to Helvex — your email is verified.\n\nOpen the dashboard: {dashboard_url}\n"
    send_email(to=to, subject=subject, html=html, text=text)


def send_invite_email(*, to: str, org_name: str, invited_by_email: str, token: str) -> None:
    link = f"{settings.app_base_url}/accept-invite?token={token}"
    subject = f"You've been invited to join {org_name} on Helvex"
    body = (
        _h1(f"You're invited to join {org_name}")
        + _p(f"<strong>{invited_by_email}</strong> has invited you to join <strong>{org_name}</strong> on Helvex.")
        + _p("Click below to accept the invitation. The link is valid for <strong>7 days</strong>.")
        + _btn(link, "Accept invitation")
        + _p("If you don't have a Helvex account yet, you'll be able to create one when you accept.", muted=True)
        + _p("If you weren't expecting this invitation, you can safely ignore this email.", muted=True)
    )
    footer = '<span style="color:#9ca3af;">This is a transactional email sent because someone invited you to Helvex.</span>'
    html = _layout(body, footer)
    text = (
        f"{invited_by_email} invited you to join {org_name} on Helvex.\n\n"
        f"Accept the invitation: {link}\n\n"
        "Link valid for 7 days. If you weren't expecting this, ignore it.\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_capture_failure_alert(*, to: str, failed_tx_ids: list[int]) -> None:
    """Alert the operator that Saferpay capture retries failed."""
    from datetime import datetime, timezone
    ids_str = ", ".join(f"#{i}" for i in failed_tx_ids)
    count = len(failed_tx_ids)
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    admin_url = f"{settings.app_base_url}/app/admin/billing"

    rows = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;font-family:monospace;font-size:14px;color:#111827;">#{tid}</td></tr>'
        for tid in failed_tx_ids
    )
    subject = f"[ACTION REQUIRED] {count} Saferpay capture(s) failed — {now_str}"
    body = (
        f'<h1 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#b91c1c;letter-spacing:-0.3px;">⚠ {count} capture(s) failed</h1>'
        + _p(f"The nightly billing renewal could not capture <strong>{count}</strong> Worldline/Saferpay transaction(s) at {now_str}.")
        + _p("These payments are <strong>authorized</strong> (funds reserved) but <strong>not yet settled</strong>. Without a capture, Saferpay will release the authorization after ~7 days.")
        + f'<table role="presentation" style="border-collapse:collapse;margin:12px 0 20px;">'
        + f'<tr><th style="text-align:left;padding:4px 16px 4px 0;font-size:13px;color:#6b7280;border-bottom:1px solid #e5e7eb;">Transaction IDs</th></tr>'
        + rows
        + "</table>"
        + _p("<strong>What to do:</strong>")
        + '<ol style="margin:0 0 16px;padding-left:20px;font-size:15px;line-height:1.8;color:#374151;">'
        + "<li>Open Admin → Billing and locate each transaction above.</li>"
        + "<li>Verify the Saferpay transaction ID (Provider tx ID) is present.</li>"
        + "<li>Manually trigger a capture via the Saferpay Back Office, or fix the configuration and redeploy.</li>"
        + "</ol>"
        + _btn(admin_url, "Open Admin → Billing")
        + _p(f"Transaction IDs: {ids_str}", muted=True)
    )
    footer = f'<span style="color:#9ca3af;">Sent by the nightly billing_renewal job · {now_str}</span>'
    html = _layout(body, footer)
    text = (
        f"ACTION REQUIRED: {count} Saferpay capture(s) failed during nightly billing renewal ({now_str}).\n\n"
        f"Transaction IDs: {ids_str}\n\n"
        f"Funds are reserved but not settled. Log in to the admin panel:\n{admin_url}\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_email_change_verification(*, to: str, token: str) -> None:
    link = f"{settings.app_base_url}/confirm-email-change?token={token}"
    subject = "Confirm your new Helvex email address"
    body = (
        _h1("Confirm your new email address")
        + _p("You requested to change your Helvex email address to this address.")
        + _p("Click below to confirm the change. The link expires in <strong>1 hour</strong>.")
        + _btn(link, "Confirm email change")
        + _p("If you didn't request this change, you can safely ignore this email — your current address is unchanged.", muted=True)
    )
    footer = '<span style="color:#9ca3af;">This is a transactional security email sent because an email change was requested.</span>'
    html = _layout(body, footer)
    text = f"Confirm your new Helvex email address:\n\n{link}\n\nLink expires in 1 hour.\n"
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
    subject = f"Low credit balance — {org_name}"
    body = (
        _h1("Your credit balance is low")
        + _p(f"The organisation <strong>{org_name}</strong> is running low on credits.")
        + f'<table role="presentation" style="background:#f9fafb;border-radius:8px;padding:16px 20px;margin:8px 0 20px;width:100%;box-sizing:border-box;">'
        + f'<tr><td style="font-size:28px;font-weight:700;color:#111827;letter-spacing:-0.5px;">{balance:,}<span style="font-size:16px;font-weight:500;color:#6b7280;"> credits</span></td></tr>'
        + f'<tr><td style="font-size:14px;color:#6b7280;margin-top:4px;">≈ CHF {balance_chf}</td></tr>'
        + "</table>"
        + _p("AI scoring and other credit-based features will stop working when the balance reaches zero.")
        + _btn(topup_url, "Top up credits")
    )
    settings_url = f"{settings.app_base_url}/app/billing"
    footer = (
        f'<a href="{settings_url}" style="color:#6b7280;text-decoration:none;">Manage notification settings</a>'
        f' &nbsp;·&nbsp; <a href="{settings.app_base_url}" style="color:#6b7280;text-decoration:none;">Helvex</a>'
    )
    html = _layout(body, footer)
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
    subject = "Your CSV export is ready"
    body = (
        _h1("Your export is ready")
        + _p(f"Your CSV export finished successfully with <strong>{row_count:,} rows</strong>.")
        + _p("The file is available for <strong>7 days</strong>.")
        + ((_btn(download_url, "Download CSV")) if download_url else "")
        + _p(f'You can also download it from the <a href="{jobs_url}" style="color:#2563eb;">Jobs page</a>.', muted=True)
    )
    settings_url = f"{settings.app_base_url}/app/billing"
    footer = (
        f'<a href="{settings_url}" style="color:#6b7280;text-decoration:none;">Manage notification settings</a>'
        f' &nbsp;·&nbsp; <a href="{settings.app_base_url}" style="color:#6b7280;text-decoration:none;">Helvex</a>'
    )
    html = _layout(body, footer)
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
    subject = f"Job failed: {label}"
    body = (
        _h1("A background job failed")
        + _p("One of your background jobs encountered an error and could not complete.")
        + f'<table role="presentation" style="border-collapse:collapse;margin:8px 0 20px;width:100%;">'
        + f'<tr><td style="padding:6px 0;font-size:13px;color:#6b7280;width:80px;">Job</td><td style="padding:6px 0;font-size:14px;font-weight:600;color:#111827;">{label}</td></tr>'
        + f'<tr><td style="padding:6px 0;font-size:13px;color:#6b7280;">Type</td><td style="padding:6px 0;font-size:14px;color:#374151;">{job_type}</td></tr>'
        + f'<tr><td style="padding:6px 0;font-size:13px;color:#6b7280;vertical-align:top;">Error</td><td style="padding:6px 0;font-size:14px;color:#b91c1c;">{summary}</td></tr>'
        + "</table>"
        + _btn(jobs_url, "View in Jobs")
    )
    settings_url = f"{settings.app_base_url}/app/billing"
    footer = (
        f'<a href="{settings_url}" style="color:#6b7280;text-decoration:none;">Manage notification settings</a>'
        f' &nbsp;·&nbsp; <a href="{settings.app_base_url}" style="color:#6b7280;text-decoration:none;">Helvex</a>'
    )
    html = _layout(body, footer)
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
    subject = f'{new_count} new {noun} {verb} "{view_name}"'
    body = (
        _h1(f"{new_count} new {noun} found")
        + _p(f"<strong>{new_count} new {noun}</strong> now {verb} your saved search <strong>&#8220;{view_name}&#8221;</strong>.")
        + f'<table role="presentation" style="background:#f9fafb;border-radius:8px;padding:14px 20px;margin:8px 0 20px;width:100%;box-sizing:border-box;">'
        + f'<tr><td style="font-size:13px;color:#6b7280;">Total matching</td></tr>'
        + f'<tr><td style="font-size:24px;font-weight:700;color:#111827;letter-spacing:-0.5px;">{previous_count + new_count:,} <span style="font-size:14px;font-weight:400;color:#6b7280;">(was {previous_count:,})</span></td></tr>'
        + "</table>"
        + _btn(view_url, "View results")
        + _p('Disable this alert by clicking the bell icon next to the saved search in the filter bar.', muted=True)
    )
    settings_url = f"{settings.app_base_url}/app/billing"
    footer = (
        f'<a href="{settings_url}" style="color:#6b7280;text-decoration:none;">Manage notification settings</a>'
        f' &nbsp;·&nbsp; <a href="{settings.app_base_url}" style="color:#6b7280;text-decoration:none;">Helvex</a>'
    )
    html = _layout(body, footer)
    text = (
        f"{new_count} new {noun} match your saved search \"{view_name}\".\n\n"
        f"Total: {previous_count + new_count:,} (was {previous_count:,})\n\n"
        f"View results: {view_url}\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)
