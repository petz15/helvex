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
    if not settings.smtp_host or not settings.smtp_from:
        logger.warning("SMTP not configured — skipping email to %s (subject: %s)", to, subject)
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

def send_verification_email(*, to: str, username: str, token: str) -> None:
    link = f"{settings.app_base_url}/verify-email?token={token}"
    subject = "Verify your Helvex email address"
    html = f"""
    <p>Hi {username},</p>
    <p>Please verify your email address by clicking the link below.
    The link is valid for 24 hours.</p>
    <p><a href="{link}">{link}</a></p>
    <p>If you did not create a Helvex account, you can ignore this email.</p>
    """
    text = (
        f"Hi {username},\n\n"
        f"Verify your email: {link}\n\n"
        "Link valid for 24 hours.\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_password_reset_email(*, to: str, username: str, token: str) -> None:
    link = f"{settings.app_base_url}/reset-password?token={token}"
    subject = "Reset your Helvex password"
    html = f"""
    <p>Hi {username},</p>
    <p>You requested a password reset. Click the link below to set a new password.
    The link expires in <strong>1 hour</strong>.</p>
    <p><a href="{link}">{link}</a></p>
    <p>If you did not request this, you can safely ignore this email — your password has not changed.</p>
    """
    text = (
        f"Hi {username},\n\n"
        f"Reset your password: {link}\n\n"
        "Link expires in 1 hour.\n"
    )
    send_email(to=to, subject=subject, html=html, text=text)


def send_welcome_email(*, to: str, username: str) -> None:
    subject = "Welcome to Helvex"
    html = f"""
    <p>Hi {username},</p>
    <p>Your email address has been verified. Welcome to Helvex!</p>
    <p><a href="{settings.app_base_url}/app/dashboard">Open the dashboard →</a></p>
    """
    text = f"Hi {username},\n\nYour email is verified. Open the dashboard: {settings.app_base_url}/app/dashboard\n"
    send_email(to=to, subject=subject, html=html, text=text)
