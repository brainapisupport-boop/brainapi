from __future__ import annotations
import os
import logging
from datetime import datetime, timezone

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

from sqlalchemy import select

from .db import SessionLocal
from .models import EmailEvent
from .email_validation import normalize_email, validate_email_address
from .config import settings

logger = logging.getLogger("brainapi.email")

# -------------------------
# Helpers
# -------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _text_to_html(text: str) -> str:
    import html
    safe_text = html.escape(text or "")
    return f"<p>{safe_text.replace(chr(10), '<br/>')}</p>"

# -------------------------
# Brevo Email Sender
# -------------------------

def send_email(to_email: str, subject: str, html_content: str):
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = os.getenv("BREVO_API_KEY")

    if not configuration.api_key['api-key']:
        raise Exception("BREVO_API_KEY not set")

    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        sender={
            "email": "support@brainapi.site",
            "name": "Brain API"
        },
        subject=subject,
        html_content=html_content
    )

    try:
        api_instance.send_transac_email(email)
        logger.info("email_sent recipient=%s subject=%s", to_email, subject)
    except ApiException as e:
        logger.error("brevo_error %s", str(e))
        raise

# -------------------------
# Queue Email
# -------------------------

def queue_email_event(
    *,
    event_type: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    html_body: str | None = None,
    dedupe_key: str | None = None,
):
    normalized_email = normalize_email(recipient_email)

    with SessionLocal() as db:
        if dedupe_key:
            existing = db.scalar(select(EmailEvent).where(EmailEvent.dedupe_key == dedupe_key))
            if existing:
                return {"status": existing.status, "id": existing.id}

        row = EmailEvent(
            event_type=event_type,
            recipient_email=normalized_email,
            subject=subject,
            body_text=body_text,
            html_body=html_body,
            status="queued",
            dedupe_key=dedupe_key,
            created_at=_utc_now(),
        )

        db.add(row)
        db.commit()
        db.refresh(row)

        return {"status": "queued", "id": row.id}

# -------------------------
# Send Email Immediately
# -------------------------

def send_transactional_email(event_id: str):
    with SessionLocal() as db:
        row = db.get(EmailEvent, event_id)

        if not row:
            return {"status": "not_found"}

        html = row.html_body or _text_to_html(row.body_text)

        try:
            send_email(
                to_email=row.recipient_email,
                subject=row.subject,
                html_content=html
            )

            row.status = "sent"
            row.sent_at = _utc_now()

        except Exception as e:
            row.status = "failed"
            row.error_message = str(e)

        db.commit()

        return {"status": row.status}

# -------------------------
# High-level APIs
# -------------------------

def send_custom_email(*, recipient_email: str, subject: str, body_text: str):
    event = queue_email_event(
        event_type="custom",
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
    )

    return send_transactional_email(event["id"])

def queue_password_reset_email(*, email: str, reset_token: str):
    reset_url = f"{settings.public_base_url}/reset?token={reset_token}"

    body = f"""
    Click below to reset password:
    {reset_url}
    """

    html = f"""
    <h2>Password Reset</h2>
    <p>Click below:</p>
    <a href="{reset_url}">Reset Password</a>
    """

    return queue_email_event(
        event_type="password_reset",
        recipient_email=email,
        subject="Reset your password",
        body_text=body,
        html_body=html,
        dedupe_key=f"reset:{email}",
    )

# -------------------------
# Email Delivery Health
# -------------------------

def email_delivery_health():
    return {
        "configured": bool(os.getenv("SMTP_HOST")),
        "delivery_enabled": not settings.skip_email_in_development,
        "environment": settings.environment,
        "skip_in_development": settings.skip_email_in_development,
    }
def get_lead_contact_for_api_key(api_key: str):
    """
    Dummy implementation for tests / fallback.
    Replace with real DB lookup later.
    """
    return {
        "email": "test@example.com",
        "name": "Test User",
        "api_key": api_key,
    }