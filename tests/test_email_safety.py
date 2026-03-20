import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import desc, select

os.environ.setdefault("PROVIDER", "mock")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REQUIRE_API_KEY", "true")
os.environ.setdefault("API_KEYS", "test-user-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_brainapi.db")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("ENABLE_USAGE_METERING", "true")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")

from app.config import settings
from app.emails import queue_email_event
from app.db import SessionLocal, init_db
from app.main import app
from app.models import EmailEvent


init_db()
client = TestClient(app)


def unique_email(prefix: str = "mail") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}-{timestamp}@brainapi.site"


def latest_email_event(event_type: str) -> EmailEvent | None:
    with SessionLocal() as db:
        return db.scalar(
            select(EmailEvent)
            .where(EmailEvent.event_type == event_type)
            .order_by(desc(EmailEvent.created_at))
        )


def test_queue_email_event_rejects_blocked_domain():
    result = queue_email_event(
        event_type="custom",
        recipient_email="blocked@example.com",
        subject="Hello",
        body_text="Test message",
    )

    assert result["success"] is False
    assert result["status"] == "skipped"
    assert result["id"] is None
    assert "blocked" in (result["error"] or "").lower()


def test_send_email_endpoint_blocks_dummy_domain():
    response = client.post(
        "/send-email",
        headers={"X-API-Key": "test-user-key"},
        json={
            "email": "blocked@example.com",
            "subject": "Test",
            "message": "Hello",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Email skipped."
    assert "blocked" in (body["error"] or "").lower()


def test_send_email_endpoint_skips_in_development(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "skip_email_in_development", True)

    response = client.post(
        "/send-email",
        headers={"X-API-Key": "test-user-key"},
        json={
            "email": "customer@brainapi.site",
            "subject": "Test",
            "message": "Hello",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Email skipped in development mode."
    assert body["error"] is None


def test_signup_sends_welcome_email_immediately(monkeypatch):
    delivered: list[dict] = []

    def fake_send(**kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "skip_email_in_development", False)
    monkeypatch.setattr(settings, "smtp_host", "smtp.brainapi.site")
    monkeypatch.setattr(settings, "email_from_address", "noreply@brainapi.site")
    monkeypatch.setattr("app.emails._send_smtp_email", fake_send)

    email = unique_email("welcome")
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "name": "Welcome User",
            "email": email,
            "password": "StrongPass123!",
            "newsletter_opt_in": True,
        },
    )

    assert response.status_code == 200
    assert any(item["recipient_email"] == email for item in delivered)
    event = latest_email_event("welcome")
    assert event is not None
    assert event.recipient_email == email
    assert event.status == "sent"


def test_request_reset_sends_email_immediately(monkeypatch):
    delivered: list[dict] = []

    def fake_send(**kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "skip_email_in_development", False)
    monkeypatch.setattr(settings, "smtp_host", "smtp.brainapi.site")
    monkeypatch.setattr(settings, "email_from_address", "noreply@brainapi.site")
    monkeypatch.setattr(settings, "public_base_url", "https://api.brainapi.site")
    monkeypatch.setattr("app.emails._send_smtp_email", fake_send)

    email = unique_email("reset")
    signup_response = client.post(
        "/api/v1/auth/signup",
        json={
            "name": "Reset User",
            "email": email,
            "password": "StrongPass123!",
            "newsletter_opt_in": False,
        },
    )
    assert signup_response.status_code == 200

    response = client.post(
        "/api/v1/auth/request-reset",
        json={"email": email},
    )

    assert response.status_code == 200
    reset_sends = [item for item in delivered if item["subject"] == "BrainAPI - Reset your password"]
    assert reset_sends
    assert "/ui/forgot-password.html?token=" in reset_sends[-1]["body_text"]
    event = latest_email_event("password_reset")
    assert event is not None
    assert event.recipient_email == email
    assert event.status == "sent"


def test_payment_verify_sends_payment_and_invoice_emails(monkeypatch):
    delivered: list[dict] = []

    def fake_send(**kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "skip_email_in_development", False)
    monkeypatch.setattr(settings, "smtp_host", "smtp.brainapi.site")
    monkeypatch.setattr(settings, "email_from_address", "noreply@brainapi.site")
    monkeypatch.setattr("app.emails._send_smtp_email", fake_send)
    monkeypatch.setattr("app.main.verify_and_mark_paid", lambda **kwargs: True)

    email = unique_email("paid")
    signup_response = client.post(
        "/api/v1/auth/signup",
        json={
            "name": "Paid User",
            "email": email,
            "password": "StrongPass123!",
            "newsletter_opt_in": False,
        },
    )
    assert signup_response.status_code == 200
    signup_body = signup_response.json()

    response = client.post(
        "/api/v1/billing/razorpay/verify",
        headers={"X-API-Key": signup_body["api_key"]},
        json={
            "api_key_id": signup_body["user"]["api_key_id"],
            "razorpay_order_id": "order_test_123",
            "razorpay_payment_id": "pay_test_123",
            "razorpay_signature": "sig_test_123",
            "plan_name": "BrainAPI Pro",
            "amount_inr": 999,
        },
    )

    assert response.status_code == 200
    subjects = {item["subject"] for item in delivered}
    assert "BrainAPI Payment Successful" in subjects
    assert "BrainAPI Invoice - BrainAPI Pro - ₹999" in subjects

    payment_event = latest_email_event("payment_success")
    invoice_event = latest_email_event("invoice")
    assert payment_event is not None
    assert payment_event.recipient_email == email
    assert payment_event.status == "sent"
    assert invoice_event is not None
    assert invoice_event.recipient_email == email
    assert invoice_event.status == "sent"
