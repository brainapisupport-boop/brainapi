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
os.environ.setdefault("AUTOMATION_ALLOWED_HOSTS", "httpbin.org")
os.environ.setdefault("ALLOW_PRIVATE_WEBHOOK_TARGETS", "false")
os.environ.setdefault("TRIAL_SIGNUP_ENABLED", "true")

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.db import init_db  # noqa: E402

init_db()

client = TestClient(app)


def unique_email(prefix: str = "test") -> str:
    domain = os.environ.get("TEST_EMAIL_DOMAIN", "brainapi.site")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}-{timestamp}@{domain}"


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


def test_protected_endpoint_requires_api_key():
    response = client.post("/api/v1/text/generate", json={"prompt": "hello"})
    assert response.status_code == 401


def test_auth_signup_login_reset_flow():
    email = unique_email("auth")

    signup_response = client.post(
        "/api/v1/auth/signup",
        json={
            "name": "Auth Test",
            "email": email,
            "password": "Start123!",
            "newsletter_opt_in": True,
        },
    )
    assert signup_response.status_code == 200
    signup_body = signup_response.json()
    assert signup_body["success"] is True
    assert signup_body["token"]
    assert signup_body["api_key"].startswith("brn_")

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Start123!"},
    )
    assert login_response.status_code == 200
    login_body = login_response.json()
    assert login_body["success"] is True
    assert login_body["token"]

    request_reset_response = client.post(
        "/api/v1/auth/request-reset",
        json={"email": email},
    )
    assert request_reset_response.status_code == 200
    reset_body = request_reset_response.json()
    assert reset_body["success"] is True
    assert reset_body.get("reset_token")

    reset_response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_body["reset_token"], "new_password": "Updated123!"},
    )
    assert reset_response.status_code == 200

    relogin_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Updated123!"},
    )
    assert relogin_response.status_code == 200


def test_auth_routes_are_publicly_accessible_in_config():
    expected_public_routes = {
        "/api/v1/auth/signup",
        "/api/v1/auth/login",
        "/api/v1/auth/request-reset",
        "/api/v1/auth/reset-password",
    }

    assert expected_public_routes.issubset(set(settings.public_path_list))


def test_user_api_key_revoke_and_regenerate_flow():
    email = unique_email("user-key")

    signup_response = client.post(
        "/api/v1/auth/signup",
        json={
            "name": "Key User",
            "email": email,
            "password": "Start123!",
            "newsletter_opt_in": False,
        },
    )
    assert signup_response.status_code == 200
    signup_body = signup_response.json()
    token = signup_body["token"]
    original_key = signup_body["api_key"]

    revoke_response = client.delete(
        "/api/v1/me/api-key",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["success"] is True

    revoked_use_response = client.post(
        "/api/v1/text/generate",
        headers={"X-API-Key": original_key},
        json={"prompt": "hello after revoke"},
    )
    assert revoked_use_response.status_code == 401

    rotate_response = client.post(
        "/api/v1/me/api-key/rotate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rotate_response.status_code == 200
    new_key = rotate_response.json()["api_key"]

    use_new_key_response = client.post(
        "/api/v1/text/generate",
        headers={"X-API-Key": new_key},
        json={"prompt": "hello with new key"},
    )
    assert use_new_key_response.status_code == 200


def test_admin_api_key_lifecycle_with_pagination():
    headers = {"X-Admin-Key": "test-admin-key"}

    create_response = client.post(
        "/api/v1/admin/api-keys",
        headers=headers,
        json={"name": "pytest-key", "rate_limit_per_minute": 25, "trial_days": 7},
    )
    assert create_response.status_code == 200
    key_id = create_response.json()["id"]

    list_response = client.get(
        "/api/v1/admin/api-keys?page=1&page_size=5",
        headers=headers,
    )
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert "items" in list_body
    assert "pagination" in list_body

    deactivate_response = client.delete(f"/api/v1/admin/api-keys/{key_id}", headers=headers)
    assert deactivate_response.status_code == 200
    assert deactivate_response.json()["success"] is True
