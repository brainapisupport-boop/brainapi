import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

os.environ.setdefault("PROVIDER", "mock")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REQUIRE_API_KEY", "true")
os.environ.setdefault("API_KEYS", "test-user-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_brainapi.db")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("ENABLE_USAGE_METERING", "true")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("TRIAL_SIGNUP_ENABLED", "true")

from app.main import app  # noqa: E402
from app.db import init_db  # noqa: E402


init_db()
client = TestClient(app)


def unique_email(prefix: str = "review") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}-{timestamp}@brainapi.site"


def test_public_reviews_start_empty_or_without_pending_reviews():
    response = client.get("/api/v1/public/reviews")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total_reviews" in body
    assert "average_rating" in body


def test_review_submission_requires_signed_in_user():
    response = client.post(
        "/api/v1/reviews",
        json={
            "rating": 5,
            "headline": "Fast integration",
            "body_text": "This made it easy to get my first request working in one session.",
            "role": "Founder",
        },
    )
    assert response.status_code == 401


def test_submit_review_then_approve_and_publish():
    email = unique_email("public-review")
    signup = client.post(
        "/api/v1/auth/signup",
        json={
            "name": "Review User",
            "email": email,
            "password": "StrongPass123!",
            "newsletter_opt_in": False,
        },
    )
    assert signup.status_code == 200
    token = signup.json()["token"]

    submit = client.post(
        "/api/v1/reviews",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "rating": 5,
            "headline": "Fast integration",
            "body_text": "This made it easy to get my first request working in one session and the onboarding flow felt clear.",
            "role": "Founder, side project",
        },
    )
    assert submit.status_code == 200
    submit_body = submit.json()
    assert submit_body["status"] == "pending"

    pending = client.get(
        "/api/v1/admin/reviews?status=pending",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert pending.status_code == 200
    pending_items = pending.json()["items"]
    matching = [item for item in pending_items if item["id"] == submit_body["review_id"]]
    assert matching
    assert matching[0]["headline"] == "Fast integration"

    approve = client.patch(
        f"/api/v1/admin/reviews/{submit_body['review_id']}",
        headers={"X-Admin-Key": "test-admin-key"},
        json={"status": "approved"},
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    public_reviews = client.get("/api/v1/public/reviews")
    assert public_reviews.status_code == 200
    public_items = public_reviews.json()["items"]
    approved = [item for item in public_items if item["id"] == submit_body["review_id"]]
    assert approved
    assert approved[0]["display_name"] == "Review User"
    assert approved[0]["verified_customer"] is True
