import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import and_, desc, func, select

from .config import settings
from .db import SessionLocal
from .models import APIKey, EmailEvent, SignupLead


class EmailError(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _is_email_configured() -> bool:
    return bool(settings.smtp_host and settings.email_from_address)


def queue_email_event(
    *,
    event_type: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    dedupe_key: str | None = None,
    scheduled_for: datetime | None = None,
) -> dict:
    recipient = _normalize_email(recipient_email)
    if not recipient:
        raise EmailError("recipient_email is required")

    with SessionLocal() as db:
        if dedupe_key:
            existing = db.scalar(select(EmailEvent).where(EmailEvent.dedupe_key == dedupe_key))
            if existing is not None:
                return {
                    "id": existing.id,
                    "status": existing.status,
                    "dedupe": True,
                }

        row = EmailEvent(
            event_type=event_type,
            recipient_email=recipient,
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
        return {
            "id": row.id,
            "status": row.status,
            "dedupe": False,
        }


def queue_welcome_email(*, name: str, email: str, api_key: str, trial_ends_at: datetime | None) -> dict:
    ends_text = trial_ends_at.isoformat() if trial_ends_at else "N/A"
    body = (
        f"Hi {name},\n\n"
        "Welcome to BrainAPI! Your free trial API key is active.\n\n"
        f"API Key: {api_key}\n"
        f"Trial Ends At (UTC): {ends_text}\n\n"
        "Get started: /docs\n"
        "If you need help, reply to this email.\n\n"
        "- BrainAPI"
    )
    return queue_email_event(
        event_type="welcome",
        recipient_email=email,
        subject="Welcome to BrainAPI - Your Trial API Key",
        body_text=body,
        dedupe_key=f"welcome:{_normalize_email(email)}",
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
        dedupe_key=None,
    )


def queue_password_reset_email(*, email: str, reset_token: str) -> dict:
    reset_url = f"https://api.brainapi.site/ui/forgot-password.html?token={reset_token}"
    body = (
        f"Hi,\n\n"
        "You requested a password reset for your BrainAPI account.\n\n"
        "Click the link below to reset your password (valid for 30 minutes):\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email.\n\n"
        "- BrainAPI\n"
        "brainapisupport@gmail.com"
    )
    return queue_email_event(
        event_type="password_reset",
        recipient_email=email,
        subject="BrainAPI — Reset your password",
        body_text=body,
        dedupe_key=f"reset:{_normalize_email(email)}:{reset_token[:8]}",
    )


def queue_invoice_email(
    *, name: str | None, email: str, plan_name: str, amount_inr: float,
    razorpay_payment_id: str, razorpay_order_id: str
) -> dict:
    from datetime import date
    display_name = name or "there"
    invoice_date = date.today().strftime("%d %b %Y")
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
        "brainapisupport@gmail.com"
    )
    return queue_email_event(
        event_type="invoice",
        recipient_email=email,
        subject=f"BrainAPI Invoice \u2014 {plan_name} \u2014 \u20b9{amount_inr:.0f}",
        body_text=body,
        dedupe_key=f"invoice:{razorpay_payment_id}",
    )


def send_transactional_email(event_id: int) -> bool:
    """Try to send a specific queued email immediately. Returns True if sent."""
    now = _utc_now()
    with SessionLocal() as db:
        row = db.get(EmailEvent, event_id)
        if row is None or row.status != "queued":
            return False
        try:
            _send_smtp_email(
                recipient_email=row.recipient_email,
                subject=row.subject,
                body_text=row.body_text,
            )
            row.status = "sent"
            row.sent_at = now
            row.error_message = None
            db.commit()
            return True
        except Exception as exc:
            row.status = "failed"
            row.error_message = str(exc)[:500]
            db.commit()
            return False


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
                recipient_email=_normalize_email(lead.email),
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


def _send_smtp_email(*, recipient_email: str, subject: str, body_text: str) -> None:
    if not _is_email_configured():
        raise EmailError("SMTP is not configured. Set SMTP_HOST and EMAIL_FROM_ADDRESS")

    message = EmailMessage()
    message["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    message["To"] = recipient_email
    message["Subject"] = subject
    if settings.email_reply_to:
        message["Reply-To"] = settings.email_reply_to
    message.set_content(body_text)

   try:
    print("📧 STARTING EMAIL SEND")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        print("🔌 Connected to SMTP")

        if settings.smtp_use_tls:
            server.starttls()
            print("🔐 TLS started")

        if settings.smtp_username:
            print("🔑 Logging in...")
            server.login(settings.smtp_username, settings.smtp_password)
            print("✅ Login successful")

        print("📤 Sending email...")
        server.send_message(message)

        print("✅ EMAIL SENT SUCCESSFULLY")

except Exception as e:
    print("❌ EMAIL FAILED:", str(e))
    raise

def send_pending_emails(limit: int = 50) -> dict:
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    now = _utc_now()
    sent = 0
    failed = 0

    with SessionLocal() as db:
        pending_rows = db.scalars(
            select(EmailEvent)
            .where(EmailEvent.status == "queued")
            .where(
                (EmailEvent.scheduled_for.is_(None))
                | (EmailEvent.scheduled_for <= now)
            )
            .order_by(func.coalesce(EmailEvent.scheduled_for, EmailEvent.created_at), desc(EmailEvent.created_at))
            .limit(limit)
        ).all()

        for row in pending_rows:
            try:
                _send_smtp_email(
                    recipient_email=row.recipient_email,
                    subject=row.subject,
                    body_text=row.body_text,
                )
                row.status = "sent"
                row.sent_at = now
                row.error_message = None
                sent += 1
            except Exception as exc:
                row.status = "failed"
                row.error_message = str(exc)[:500]
                failed += 1

        db.commit()

    return {
        "attempted": len(pending_rows),
        "sent": sent,
        "failed": failed,
    }


def get_lead_contact_for_api_key(api_key_id: str) -> dict | None:
    with SessionLocal() as db:
        row = db.scalar(
            select(SignupLead)
            .where(and_(SignupLead.api_key_id == api_key_id, SignupLead.email.is_not(None)))
            .order_by(desc(SignupLead.created_at))
        )
        if row is None:
            return None
        return {
            "name": row.name,
            "email": row.email,
        }
