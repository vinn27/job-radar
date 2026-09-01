"""Gmail SMTP sender with a helpful failure message for the app-password dance."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("jobradar.mailer")

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465


def send(settings, subject: str, text: str, html: str) -> None:
    to_addr = settings.email.get("to", "")
    if not to_addr or "YOUR_EMAIL" in to_addr:
        raise SystemExit(
            "Set \"to\" in config.json to your email address first."
        )
    if not settings.smtp_user or not settings.smtp_password:
        raise SystemExit(
            "SMTP credentials missing. Create C:\\Users\\vinit\\jobradar\\.env with:\n"
            "  JOB_RADAR_SMTP_USER=you@gmail.com\n"
            "  JOB_RADAR_SMTP_PASSWORD=<16-char app password>\n"
            "An app password needs 2FA on your Google account:\n"
            "  myaccount.google.com -> Security -> 2-Step Verification -> App passwords"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = to_addr
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(),
                              timeout=30) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise SystemExit(
            "Gmail rejected the login. Check the app password in .env (no spaces, "
            "16 characters). App passwords require 2FA enabled on the Google account:\n"
            "  myaccount.google.com -> Security -> 2-Step Verification -> App passwords"
        ) from exc
    log.info("email sent to %s: %s", to_addr, subject)
