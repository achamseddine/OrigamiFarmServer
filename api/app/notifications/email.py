"""Outbound email, and an honest answer when there is none.

This deployment may or may not have a mail server. Rather than pretend —
the failure mode being an admin who believes a customer was emailed and a
customer who never sees anything — `send_email` reports what actually
happened, and every caller passes that back up to the console so the
screen can say "emailed to them" or "not sent, copy this link instead".

There is no queue and no retry: one SMTP attempt, synchronous, and a
failure is reported rather than raised, because an invitation whose email
bounces is still a valid invitation and the link is still on screen. If
this ever needs delivery guarantees it wants a worker, not a longer
timeout here.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.config.settings import Settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 10


@dataclass
class EmailResult:
    """What happened, in terms the console can show a person."""

    sent: bool
    # "email" when it left this process, "manual" when no mail server is
    # configured, "failed" when one is and the attempt did not work.
    delivery: str
    detail: str


def email_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.email_from)


def send_email(
    settings: Settings, *, to: str, subject: str, body: str
) -> EmailResult:
    if not email_configured(settings):
        return EmailResult(
            sent=False,
            delivery="manual",
            detail="No mail server is configured, so nothing was sent.",
        )

    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        if settings.smtp_use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=SMTP_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
            )
        with server:
            if settings.smtp_use_starttls and not settings.smtp_use_ssl:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 - reported, never raised: see module docstring
        # The address is logged, the body never is: an invitation link in a
        # log file is a credential in a log file.
        logger.warning("Email to %s failed: %s", to, exc)
        return EmailResult(
            sent=False,
            delivery="failed",
            detail=f"The mail server rejected the message ({exc.__class__.__name__}).",
        )

    return EmailResult(sent=True, delivery="email", detail=f"Emailed to {to}.")


def invitation_email(
    *, display_name: str, tenant_name: str, url: str, expires_in_days: int
) -> tuple[str, str]:
    """Subject and body for the one email this system sends."""
    subject = f"Your {tenant_name} account on Origami"
    body = (
        f"Hello {display_name},\n\n"
        f"An account has been created for you on Origami for {tenant_name}.\n\n"
        "Open the link below to choose your password and start using the app:\n\n"
        f"{url}\n\n"
        f"The link works once and expires in {expires_in_days} days.\n"
        "If you were not expecting this, you can ignore this message.\n"
    )
    return subject, body
