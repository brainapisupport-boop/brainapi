from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import requests
from sqlalchemy import and_, desc, func, select

from .config import settings
from .db import SessionLocal
from .email_validation import normalize_email, validate_email_address
from .launch import support_email_value
from .models import APIKey, EmailEvent, SignupLead, UserAccount

logger = logging.getLogger("brainapi.email")


class EmailError(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_resend_configured() -> bool:
    return bool(settings.resend_api_key and settings.email_from_address)


def _is_smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.email_from_address)


def _is_email_configured() -> bool:
    if settings.email_provider == "resend":
        return _is_resend_configured()
    return _is_smtp_configured()


def _should_skip_delivery() -> bool:
    environment = (settings.environment or "").strip().lower()
    return bool(settings.skip_email_in_development and environment in {"development", "dev", "test", "local"})


def email_delivery_health() -> dict:
    environment = (settings.environment or "").strip().lower()
    configured = _is_email_configured()
    skip_delivery = _should_skip_delivery()
    return {
        "environment": environment or "unknown",
        "configured": configured,
        "email_provider": settings.email_provider,
        "smtp_host_configured": bool(settings.smtp_host),
        "resend_configured": _is_resend_configured(),
        "from_address_configured": bool(settings.email_from_address),
        "delivery_enabled": configured and not skip_delivery,
        "skip_in_development": bool(settings.skip_email_in_development),
    }


def _email_result(
    *,
    success: bool,
    status: str,
    message: str,
    error: str | None = None,
    event_id: str | None = None,
    dedupe: bool = False,
) -> dict:
    return {
        "success": success,
        "status": status,
        "message": message,
        "error": error,
        "id": event_id,
        "dedupe": dedupe,
    }


def _validate_recipient(recipient_email: str) -> tuple[str | None, str | None]:
    result = validate_email_address(recipient_email, set(settings.blocked_email_domains_list))
    return (result.normalized_email, result.error)


def queue_email_event(
    *,
    event_type: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    dedupe_key: str | None = None,
    scheduled_for: datetime | None = None,
) -> dict:
    normalized_email, validation_error = _validate_recipient(recipient_email)
    if validation_error or not normalized_email:
        logger.warning(
            "email_queue_skipped type=%s recipient=%s reason=%s",
            event_type,
            recipient_email,
            validation_error,
        )
        return _email_result(
            success=False,
            status="skipped",
            message="Email skipped.",
            error=validation_error or "Email is required.",
        )

    with SessionLocal() as db:
        if dedupe_key:
            existing = db.scalar(select(EmailEvent).where(EmailEvent.dedupe_key == dedupe_key))
            if existing is not None:
                return _email_result(
                    success=existing.status in {"queued", "sent"},
                    status=existing.status,
                    message="Email already queued.",
                    event_id=existing.id,
                    dedupe=True,
                )

        row = EmailEvent(
            event_type=event_type,
            recipient_email=normalized_email,
            subject=subject,
            body_text=body_text,
            status="queued",
            dedupe_key=dedupe_key,
            scheduled_for=scheduled_for,
            created_at=_utc_now(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _email_result(
            success=True,
            status=row.status,
            message="Email queued.",
            event_id=row.id,
        )


def queue_welcome_email(*, name: str, email: str, api_key: str, trial_ends_at: datetime | None) -> dict:
    ends_text = trial_ends_at.isoformat() if trial_ends_at else "N/A"
    base_url = settings.public_base_url.rstrip("/")
    quickstart_url = f"{base_url}/ui/quickstart.html"
    dashboard_url = f"{base_url}/ui/dashboard.html#overview"
    status_url = f"{base_url}/status"
    support_email = support_email_value()
    body = (
        f"Hi {name},\n\n"
        "Welcome to BrainAPI. Your API key is active and ready to use.\n\n"
        f"API Key: {api_key}\n"
        f"Trial Ends At (UTC): {ends_text}\n\n"
        "Start here:\n"
        f"1. Quickstart docs: {quickstart_url}\n"
        f"2. Dashboard: {dashboard_url}\n"
        f"3. Status: {status_url}\n\n"
        "First request:\n"
        f"curl -X POST {base_url}/api/v1/ai \\\n"
        f"  -H \"X-API-Key: {api_key}\" \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  -d '{\"type\":\"text\",\"input\":\"Say hello from BrainAPI\",\"mode\":\"cheap\"}'\n\n"
        f"Need help? Reply to this email or write to {support_email}.\n\n"
        "- BrainAPI"
    )
    return queue_email_event(
        event_type="welcome",
        recipient_email=email,
        subject="Your BrainAPI API key is ready",
        body_text=body,
        dedupe_key=f"welcome:{normalize_email(email)}",
    )


def queue_payment_success_email(*, name: str | None, email: str, plan_name: str) -> dict:
    display_name = name or "there"
    body = (
        f"Hi {display_name},\n\n"
        f"Payment received successfully for {plan_name}.\n"
        "Your BrainAPI access is now marked as paid.\n\n"
        "Thank you for choosing BrainAPI.\n"
        "- BrainAPI"
    )
    return queue_email_event(
        event_type="payment_success",
        recipient_email=email,
        subject="BrainAPI Payment Successful",
        body_text=body,
    )


def queue_password_reset_email(*, email: str, reset_token: str) -> dict:
    reset_url = f"{settings.public_base_url.rstrip('/')}/ui/forgot-password.html?token={reset_token}"
    support_email = support_email_value()
    body = (
        "Hi,\n\n"
        "You requested a password reset for your BrainAPI account.\n\n"
        "Click the link below to reset your password (valid for 30 minutes):\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email.\n\n"
        "- BrainAPI\n"
        f"{support_email}"
    )
    normalized = normalize_email(email)
    return queue_email_event(
        event_type="password_reset",
        recipient_email=normalized,
        subject="BrainAPI - Reset your password",
        body_text=body,
        dedupe_key=f"reset:{normalized}:{reset_token[:8]}",
    )


def queue_invoice_email(
    *,
    name: str | None,
    email: str,
    plan_name: str,
    amount_inr: float,
    razorpay_payment_id: str,
    razorpay_order_id: str,
) -> dict:
    from datetime import date

    display_name = name or "there"
    invoice_date = date.today().strftime("%d %b %Y")
    support_email = support_email_value()
    body = (
        f"Hi {display_name},\n\n"
        "Thank you for your payment. Here is your invoice:\n\n"
        f"  Invoice Date : {invoice_date}\n"
        f"  Plan         : {plan_name}\n"
        f"  Amount       : \u20b9{amount_inr:.2f} INR\n"
        f"  Payment ID   : {razorpay_payment_id}\n"
        f"  Order ID     : {razorpay_order_id}\n\n"
        "Your BrainAPI subscription is now active.\n"
        "For any billing questions, reply to this email.\n\n"
        "- BrainAPI Team\n"
        f"{support_email}"
    )
    return queue_email_event(
        event_type="invoice",
        recipient_email=email,
        subject=f"BrainAPI Invoice - {plan_name} - \u20b9{amount_inr:.0f}",
        body_text=body,
        dedupe_key=f"invoice:{razorpay_payment_id}",
    )


def _send_resend_email(*, recipient_email: str, subject: str, body_text: str) -> None:
    if not _is_resend_configured():
        raise EmailError("Resend is not configured. Set RESEND_API_KEY and EMAIL_FROM_ADDRESS.")

    from_address = settings.email_from_name + f" <{settings.email_from_address}>" if settings.email_from_name else settings.email_from_address
    html_body = "".join(f"<p>{line}</p>" for line in body_text.splitlines()) if body_text else "<p></p>"

    payload = {
        "from": from_address,
        "to": [recipient_email],
        "subject": subject,
        "html": html_body,
    }
    if settings.email_reply_to:
        payload["reply_to"] = settings.email_reply_to

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
    )

    if response.status_code >= 400:
        raise EmailError(f"Resend API error {response.status_code}: {response.text[:200]}")

    logger.info("resend_email_sent recipient=%s subject=%s status=%s", recipient_email, subject, response.status_code)


def _send_smtp_email(*, recipient_email: str, subject: str, body_text: str) -> None:
    if not _is_smtp_configured():
        raise EmailError("SMTP is not configured. Set SMTP_HOST and EMAIL_FROM_ADDRESS.")

    message = EmailMessage()
    message["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    message["To"] = recipient_email
    message["Subject"] = subject

    if settings.email_reply_to:
        message["Reply-To"] = settings.email_reply_to

    message.set_content(body_text)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()

        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)

        server.send_message(message)


def _send_email(*, recipient_email: str, subject: str, body_text: str) -> None:
    if settings.email_provider == "resend":
        _send_resend_email(recipient_email=recipient_email, subject=subject, body_text=body_text)
        return
    _send_smtp_email(recipient_email=recipient_email, subject=subject, body_text=body_text)


def _deliver_email(*, recipient_email: str, subject: str, body_text: str) -> dict:
    normalized_email, validation_error = _validate_recipient(recipient_email)
    if validation_error or not normalized_email:
        return _email_result(
            success=False,
            status="skipped",
            message="Email skipped.",
            error=validation_error or "Email is required.",
        )

    if _should_skip_delivery():
        logger.info("email_delivery_skipped recipient=%s reason=development_mode", normalized_email)
        return _email_result(
            success=False,
            status="skipped",
            message="Email skipped in development mode.",
        )

    if not _is_email_configured():
        provider = settings.email_provider
        error_msg = (
            "Resend is not configured. Set RESEND_API_KEY and EMAIL_FROM_ADDRESS."
            if provider == "resend"
            else "SMTP is not configured. Set SMTP_HOST and EMAIL_FROM_ADDRESS."
        )
        return _email_result(
            success=False,
            status="failed",
            message="Email failed.",
            error=error_msg,
        )

    try:
        _send_email(
            recipient_email=normalized_email,
            subject=subject,
            body_text=body_text,
        )
        logger.info("email_sent provider=%s recipient=%s subject=%s", settings.email_provider, normalized_email, subject)
        return _email_result(
            success=True,
            status="sent",
            message="Email sent.",
        )
    except Exception as exc:
        logger.exception("email_delivery_failed provider=%s recipient=%s error=%s", settings.email_provider, normalized_email, exc)
        return _email_result(
            success=False,
            status="failed",
            message="Email failed.",
            error=str(exc)[:500],
        )


def send_transactional_email(event_id: str | None) -> dict:
    """Try to send a specific queued email immediately."""
    if not event_id:
        return _email_result(
            success=False,
            status="skipped",
            message="Email skipped.",
            error="Queued email event is missing.",
        )

    now = _utc_now()
    with SessionLocal() as db:
        row = db.get(EmailEvent, event_id)
        if row is None:
            return _email_result(
                success=False,
                status="skipped",
                message="Email skipped.",
                error="Queued email event not found.",
            )
        if row.status != "queued":
            return _email_result(
                success=row.status == "sent",
                status=row.status,
                message=f"Email {row.status}.",
                error=row.error_message,
                event_id=row.id,
            )

        result = _deliver_email(
            recipient_email=row.recipient_email,
            subject=row.subject,
            body_text=row.body_text,
        )
        row.status = result["status"]
        row.error_message = (result.get("error") or "")[:500] or None
        row.sent_at = now if result["status"] == "sent" else None
        db.commit()

        result["id"] = row.id
        return result


def dispatch_transactional_email(event_id: str | None) -> dict:
    try:
        result = send_transactional_email(event_id)
    except Exception as exc:
        logger.exception("transactional_email_dispatch_crashed event_id=%s error=%s", event_id, exc)
        return _email_result(
            success=False,
            status="failed",
            message="Email failed.",
            error=str(exc)[:500],
            event_id=event_id,
        )

    if result["status"] != "sent":
        logger.warning(
            "transactional_email_not_sent event_id=%s status=%s error=%s",
            event_id,
            result["status"],
            result.get("error"),
        )

    return result


def schedule_trial_reminder_emails() -> dict:
    now = _utc_now()
    reminder_days = {7, 3, 1, 0}

    queued = 0
    skipped = 0

    with SessionLocal() as db:
        rows = db.execute(
            select(SignupLead, APIKey)
            .join(APIKey, SignupLead.api_key_id == APIKey.id)
            .where(APIKey.is_active.is_(True))
            .where(APIKey.is_paid.is_(False))
            .where(APIKey.trial_ends_at.is_not(None))
        ).all()

        for lead, key in rows:
            if not lead.email or not key.trial_ends_at:
                skipped += 1
                continue

            normalized_email, validation_error = _validate_recipient(lead.email)
            if validation_error or not normalized_email:
                logger.warning(
                    "trial_email_skipped lead_id=%s recipient=%s reason=%s",
                    lead.id,
                    lead.email,
                    validation_error,
                )
                skipped += 1
                continue

            trial_end = key.trial_ends_at
            if trial_end.tzinfo is None:
                trial_end = trial_end.replace(tzinfo=timezone.utc)
            days_left = (trial_end.date() - now.date()).days

            if days_left not in reminder_days:
                continue

            if days_left > 0:
                subject = f"BrainAPI Trial: {days_left} day(s) left"
                body = (
                    f"Hi {lead.name},\n\n"
                    f"Your BrainAPI free trial ends in {days_left} day(s).\n"
                    "Upgrade to a paid plan to continue uninterrupted API access.\n\n"
                    "- BrainAPI"
                )
                event_type = "trial_reminder"
            else:
                subject = "BrainAPI Trial Ended - Upgrade to Continue"
                body = (
                    f"Hi {lead.name},\n\n"
                    "Your BrainAPI free trial has ended.\n"
                    "Complete payment to restore API access immediately.\n\n"
                    "- BrainAPI"
                )
                event_type = "trial_expired"

            dedupe_key = f"trial:{lead.id}:{key.id}:{days_left}"
            existing = db.scalar(select(EmailEvent).where(EmailEvent.dedupe_key == dedupe_key))
            if existing is not None:
                skipped += 1
                continue

            event = EmailEvent(
                event_type=event_type,
                recipient_email=normalized_email,
                subject=subject,
                body_text=body,
                status="queued",
                dedupe_key=dedupe_key,
                scheduled_for=now,
                created_at=now,
            )
            db.add(event)
            queued += 1

        db.commit()

    return {
        "queued": queued,
        "skipped": skipped,
    }


def send_pending_emails(limit: int = 50) -> dict:
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    now = _utc_now()
    sent = 0
    failed = 0
    skipped = 0

    with SessionLocal() as db:
        pending_rows = db.scalars(
            select(EmailEvent)
            .where(EmailEvent.status == "queued")
            .where((EmailEvent.scheduled_for.is_(None)) | (EmailEvent.scheduled_for <= now))
            .order_by(func.coalesce(EmailEvent.scheduled_for, EmailEvent.created_at), desc(EmailEvent.created_at))
            .limit(limit)
        ).all()

        for row in pending_rows:
            result = _deliver_email(
                recipient_email=row.recipient_email,
                subject=row.subject,
                body_text=row.body_text,
            )
            row.status = result["status"]
            row.error_message = (result.get("error") or "")[:500] or None
            row.sent_at = now if result["status"] == "sent" else None

            if result["status"] == "sent":
                sent += 1
            elif result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1

        db.commit()

    return {
        "attempted": len(pending_rows),
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
    }


def send_custom_email(*, recipient_email: str, subject: str, body_text: str) -> dict:
    event = queue_email_event(
        event_type="custom",
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
    )
    if not event.get("id"):
        return event
    return send_transactional_email(event["id"])


def get_lead_contact_for_api_key(api_key_id: str) -> dict | None:
    with SessionLocal() as db:
        lead_row = db.scalar(
            select(SignupLead)
            .where(and_(SignupLead.api_key_id == api_key_id, SignupLead.email.is_not(None)))
            .order_by(desc(SignupLead.created_at))
        )
        if lead_row is not None:
            return {
                "name": lead_row.name,
                "email": lead_row.email,
            }

        user_row = db.scalar(
            select(UserAccount)
            .where(and_(UserAccount.api_key_id == api_key_id, UserAccount.email.is_not(None)))
            .order_by(desc(UserAccount.created_at))
        )
        if user_row is None:
            return None
        return {
            "name": user_row.name,
            "email": user_row.email,
        }
