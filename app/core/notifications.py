"""Notification creation + optional email delivery.

Email is OPT-IN: if SMTP_* env vars aren't set, we skip the send and only
record the in-app notification. This way the bell still works without any
external setup.

Functions:
- create_notification(...)  → main entry. Creates row, optionally emails.
- send_email_async(...)     → fire-and-forget email via asyncio.
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.notification import Notification
from app.models.user import User


log = logging.getLogger("careai.notifications")


def create_notification(
    db: Session,
    *,
    user_id: str,
    type_: str,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    source_user: Optional[User] = None,
    send_email: bool = True,
) -> Notification:
    """Persist a notification. Email send is best-effort and async."""
    n = Notification(
        user_id=user_id, type=type_, title=title, body=body, link=link,
        source_user_id=source_user.id if source_user else None,
        source_user_name=source_user.name if source_user else None,
        source_user_role=source_user.role if source_user else None,
    )
    db.add(n)
    db.flush()  # populate n.id without committing

    if send_email and _email_configured():
        # Need recipient email — fetch the target user
        target = db.query(User).filter(User.id == user_id).first()
        if target and target.email:
            asyncio.create_task(_send_email_safe(target.email, title, body or "", link))
            n.emailed_at = datetime.utcnow()

    return n


def _email_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD)


async def _send_email_safe(to: str, subject: str, body: str, link: Optional[str]):
    """Wrap blocking SMTP in a thread executor; swallow + log errors."""
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, _send_email_blocking, to, subject, body, link
        )
        log.info("Email sent to %s: %s", to, subject)
    except Exception as e:
        log.warning("Email send failed for %s: %s", to, e)


def _send_email_blocking(to: str, subject: str, body: str, link: Optional[str]):
    """Synchronous SMTP send — runs in executor thread."""
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to
    msg["Subject"] = f"[CareAI] {subject}"

    text = body or ""
    if link:
        full_link = f"{settings.FRONTEND_URL.rstrip('/')}{link}" if link.startswith("/") else link
        text = f"{text}\n\nView in CareAI: {full_link}"
    text = (text or "Open CareAI to view this notification.").strip()
    msg.set_content(text)

    # SMTP_PORT default 587 (STARTTLS) — fall back to 465 (SSL) if specified
    port = settings.SMTP_PORT or 587
    if port == 465:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, port, timeout=15) as s:
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, port, timeout=15) as s:
            s.starttls()
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
