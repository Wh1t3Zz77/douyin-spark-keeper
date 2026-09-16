import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import EmailOutbox, utcnow


settings = get_settings()


def enqueue_email(
    db: Session,
    recipient: str,
    subject: str,
    body: str,
    user_id: str | None = None,
    dedup_key: str | None = None,
) -> EmailOutbox:
    if dedup_key:
        existing = db.scalar(select(EmailOutbox).where(EmailOutbox.dedup_key == dedup_key))
        if existing:
            return existing
    item = EmailOutbox(user_id=user_id, recipient=recipient, subject=subject, body=body, dedup_key=dedup_key)
    db.add(item)
    db.flush()
    return item


def send_one(item: EmailOutbox) -> None:
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("SMTP 尚未配置")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = item.recipient
    message["Subject"] = item.subject
    message.set_content(item.body)
    smtp_cls = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
    with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=20) as client:
        if not settings.smtp_ssl:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)


def process_outbox(db: Session, limit: int = 10) -> int:
    now = utcnow()
    items = db.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.status.in_(["pending", "retry"]), EmailOutbox.next_attempt_at <= now)
        .order_by(EmailOutbox.created_at)
        .limit(limit)
    ).all()
    sent = 0
    for item in items:
        try:
            send_one(item)
            item.status = "sent"
            item.last_error = None
            sent += 1
        except Exception as exc:
            item.attempts += 1
            item.last_error = str(exc)[:500]
            item.status = "failed" if item.attempts >= 5 else "retry"
            item.next_attempt_at = now + timedelta(minutes=min(60, 2 ** item.attempts))
    db.commit()
    return sent
