"""
Outbound email — currently just password-reset links.

Falls back to logging the message instead of sending when SMTP_HOST isn't
configured, so the full reset flow is testable locally (and in CI) without
real credentials. Set SMTP_* in .env to actually deliver mail.
"""
import logging
from email.message import EmailMessage

import aiosmtplib

from ..config import settings

logger = logging.getLogger(__name__)


async def send_password_reset_email(to_email: str, reset_link: str) -> None:
    """
    Send (or log) a password-reset email.

    Never raises on delivery failure — the caller must not let email
    problems change what the API tells the client (see forgot-password
    route: same response whether or not the account exists).
    """
    subject = "Reset your Blundr password"
    body = (
        "We got a request to reset your Blundr password.\n\n"
        f"Reset it here (expires in {settings.PASSWORD_RESET_TOKEN_EXPIRES_MINUTES} minutes):\n"
        f"{reset_link}\n\n"
        "If you didn't request this, you can ignore this email."
    )

    if not settings.SMTP_HOST:
        logger.info(f"[email-service] SMTP not configured — logging instead of sending.\n"
                    f"To: {to_email}\nSubject: {subject}\n{body}")
        return

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USERNAME or None,
            password=settings.SMTP_PASSWORD or None,
            start_tls=settings.SMTP_USE_TLS,
        )
    except Exception:
        logger.exception(f"Failed to send password-reset email to {to_email}")
