from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from carlos.channels.base import ChannelError, SendResult
from carlos.config import Settings


def send_email(settings: Settings, to: str, subject: str, body: str, reply_to: str = "") -> SendResult:
    if not settings.email_enabled:
        raise ChannelError("email not configured (SMTP_HOST / SMTP_FROM)")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(settings.smtp_from or "carlos.local").split("@")[-1])
    if reply_to or settings.email:
        msg["Reply-To"] = reply_to or settings.email
    msg.set_content(body)
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, 465, context=ssl.create_default_context(), timeout=30)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
            server.starttls(context=ssl.create_default_context())
        with server:
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise ChannelError(f"email to {to}: {e}") from e
    return SendResult(True, msg["Message-ID"])
