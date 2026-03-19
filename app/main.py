import hmac
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import (
    authenticate_user,
    create_db_api_key,
    create_password_reset_token,
    create_session_token,
    create_user_account,
    deactivate_db_api_key,
    get_user_by_email,
    list_db_api_keys,
    reset_password_with_token,
    rotate_db_api_key,
    rotate_user_api_key,
    revoke_user_api_key,
    set_db_api_key_paid,
    verify_session_token,
    verify_user_api_key,
)
from .billing import BillingError, create_razorpay_order, handle_razorpay_webhook, verify_and_mark_paid
from .config import settings
from .db import init_db
from .emails import (
    get_lead_contact_for_api_key,
    queue_payment_success_email,
    queue_welcome_email,
        queue_password_reset_email,
        queue_invoice_email,
        send_transactional_email,
    schedule_trial_reminder_emails,
    send_pending_emails,
)
from .leads import SignupError, create_trial_signup
from .metering import per_key_usage_summary, record_usage_event, usage_summary
from .schemas import (
    AdminCreateApiKeyRequest,
    AdminCreateRazorpayOrderRequest,
    AdminUpdateApiKeyBillingRequest,
    AuthLoginRequest,
    AuthLoginResponse,
    AuthRequestResetRequest,
    AuthRequestResetResponse,
    AuthResetPasswordRequest,
    AuthResetPasswordResponse,
    AuthSignupRequest,
    AuthSignupResponse,
    AutomationRunRequest,
    AutomationRunResponse,
    BillingCheckoutRequest,
    ImageGenerateRequest,
    ImageGenerateResponse,
    PublicPlansResponse,
    PublicTrialSignupRequest,
    PublicTrialSignupResponse,
    RazorpayOrderResponse,
    RazorpayVerifyPaymentRequest,
    RazorpayVerifyPaymentResponse,
    StepResult,
    TextGenerateRequest,
    TextGenerateResponse,
    TranscriptionResponse,
)
from .security import InMemoryRateLimiter, RedisRateLimiter, extract_api_key_from_request, require_api_key
from .services import generate_image, generate_text, run_automation_steps, transcribe_audio

app = FastAPI(
    title="BrainAPI",
    description="Unified AI API for text, image, speech and automation",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("brainapi")

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(static_dir)), name="ui")

in_memory_rate_limiter = InMemoryRateLimiter(max_requests=settings.rate_limit_per_minute)
redis_rate_limiter = RedisRateLimiter(settings.redis_url) if settings.redis_url else None

PUBLIC_PLAN_CATALOG = [
    {"name": "Free", "price_usd": 0, "amount_inr": 0, "token_limit": "50k tokens/month", "best_for": "testing"},
    {"name": "Starter", "price_usd": 4, "amount_inr": 349, "token_limit": "1M tokens", "best_for": "developers"},
    {"name": "Pro", "price_usd": 9, "amount_inr": 799, "token_limit": "3M tokens", "best_for": "startups"},
    {"name": "Business", "price_usd": 19, "amount_inr": 1699, "token_limit": "10M tokens", "best_for": "SaaS apps"},
]


def _provider_exception_status_code(exc: Exception) -> int:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return int(exc.response.status_code)

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and 400 <= status_code <= 599:
        return status_code

    return 500


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_public_path(path: str) -> bool:
    if path == "/":
        return True

    for public_path in settings.public_path_list:
        if path == public_path or path.startswith(f"{public_path}/"):
            return True

    return False


def _extract_admin_key(request: Request) -> str:
    header_key = request.headers.get("x-admin-key", "")
    if header_key:
        return header_key

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:]

    return ""


def require_admin(request: Request) -> None:
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin APIs are disabled because ADMIN_API_KEY is not configured",
        )

    provided = _extract_admin_key(request)
    if not provided or not hmac.compare_digest(provided, settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key")


class ObservabilityAndSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        except Exception:
            logger.exception("Unhandled error request_id=%s path=%s", request_id, request.url.path)
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://checkout.razorpay.com https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https:; "
            "frame-src 'self' https://checkout.razorpay.com https://api.razorpay.com; "
            "upgrade-insecure-requests; "
            "block-all-mixed-content"
        )

        if settings.environment.lower() == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        auth_identity = getattr(request.state, "auth_identity", None)
        if auth_identity and settings.enable_usage_metering:
            try:
                record_usage_event(
                    api_key_id=auth_identity.key_id,
                    api_key_label=auth_identity.key_label,
                    endpoint=request.url.path,
                    method=request.method,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                logger.warning("Usage metering failed request_id=%s error=%s", request_id, exc)

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_public_path(request.url.path):
            return await call_next(request)

        auth_identity = getattr(request.state, "auth_identity", None)
        if auth_identity is None:
            raw_api_key = extract_api_key_from_request(request)
            if not raw_api_key:
                return await call_next(request)

            auth_identity = verify_user_api_key(raw_api_key)
            if auth_identity is None:
                return await call_next(request)

            request.state.auth_identity = auth_identity

        if auth_identity.requires_billing and not auth_identity.is_paid and auth_identity.trial_ends_at is not None:
            if _as_utc(auth_identity.trial_ends_at) < _utc_now():
                return JSONResponse(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    content={"detail": "Trial expired. Please upgrade to continue."},
                )

        rate_limit = auth_identity.rate_limit_per_minute or settings.rate_limit_per_minute

        try:
            if redis_rate_limiter:
                allowed, retry_after = await redis_rate_limiter.is_allowed(
                    key=auth_identity.key_label,
                    max_requests=rate_limit,
                    window_seconds=60,
                )
            else:
                allowed, retry_after = in_memory_rate_limiter.is_allowed(
                    key=auth_identity.key_label,
                    max_requests=rate_limit,
                )
        except Exception as exc:
            logger.warning("Redis limiter failed; falling back to in-memory: %s", exc)
            allowed, retry_after = in_memory_rate_limiter.is_allowed(
                key=auth_identity.key_label,
                max_requests=rate_limit,
            )

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": f"Rate limit exceeded. Try again in {retry_after} seconds."},
                headers={"Retry-After": str(retry_after)},
            )

        request.state.rate_limit_remaining = max(rate_limit - 1, 0)
        return await call_next(request)


cors_origins = settings.cors_allow_origins_list
allow_credentials = "*" not in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_credentials,
    allow_methods=settings.cors_allow_methods_list,
    allow_headers=settings.cors_allow_headers_list,
)
app.add_middleware(ObservabilityAndSecurityMiddleware)
app.add_middleware(RateLimitMiddleware)


@app.on_event("startup")
def startup_event():
    if settings.auto_create_tables:
        init_db()

    if settings.environment.lower() == "production":
        token_secret = (settings.auth_token_secret or "").strip()
        if not token_secret or len(token_secret) < 32:
            raise RuntimeError("AUTH_TOKEN_SECRET must be set and secure in production")

    if settings.require_api_key and not settings.api_key_list:
        logger.warning("REQUIRE_API_KEY is enabled but API_KEYS is empty; only DB-managed keys can authenticate")

from fastapi.responses import FileResponse

@app.get("/")
def web_ui():
    return FileResponse("app/static/index.html")


@app.get("/favicon.ico")
def favicon_ico():
    return FileResponse("app/static/favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.svg")
def favicon_svg():
    return FileResponse("app/static/favicon.svg", media_type="image/svg+xml")


@app.get("/google837a0fffd89d0450.html")
def google_site_verification():
    return FileResponse("app/static/google837a0fffd89d0450.html", media_type="text/html")

@app.get("/health")
def health_check():
    is_sqlite_database = settings.database_url.strip().lower().startswith("sqlite")
    return {
        "status": "ok",
        "provider": settings.provider_name,
        "provider_ready": settings.provider_ready,
        "environment": settings.environment,
        "database_persistent": not is_sqlite_database,
    }


@app.get("/api/v1/metrics")
def metrics(request: Request):
    return {
        "status": "ok",
        "request_id": getattr(request.state, "request_id", None),
        "provider_ready": settings.provider_ready,
        "usage_metering_enabled": settings.enable_usage_metering,
        "redis_rate_limiter_enabled": bool(settings.redis_url),
    }


@app.post("/api/v1/auth/signup", response_model=AuthSignupResponse)
def auth_signup(payload: AuthSignupRequest):
    if not settings.trial_signup_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signup is currently disabled")

    if get_user_by_email(payload.email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account already exists for this email")

    try:
        signup = create_trial_signup(
            name=payload.name,
            email=payload.email,
            company=None,
            use_case=None,
            source="auth-signup",
            trial_days=settings.trial_default_days,
            rate_limit_per_minute=settings.trial_default_rate_limit_per_minute,
        )
    except SignupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        user = create_user_account(
            name=payload.name,
            email=payload.email,
            password=payload.password,
            api_key_id=signup["api_key_id"],
            newsletter_opt_in=payload.newsletter_opt_in,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    try:
        queue_welcome_email(
            name=user["name"],
            email=user["email"],
            api_key=signup["api_key"],
            trial_ends_at=signup["trial_ends_at"],
        )
        schedule_trial_reminder_emails()
    except Exception as exc:
        logger.warning("Auth signup email queueing failed: %s", exc)
    

    token = create_session_token(user_id=user["id"], email=user["email"])

    return AuthSignupResponse(
        message="Account created successfully.",
        token=token,
        user=user,
        api_key=signup["api_key"],
        key_prefix=signup["key_prefix"],
        trial_ends_at=signup["trial_ends_at"],
    )


@app.post("/api/v1/auth/login", response_model=AuthLoginResponse)
def auth_login(payload: AuthLoginRequest):
    user = authenticate_user(payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_session_token(user_id=user["id"], email=user["email"])
    return AuthLoginResponse(message="Login successful.", token=token, user=user)


@app.post("/api/v1/auth/request-reset", response_model=AuthRequestResetResponse)
def auth_request_reset(payload: AuthRequestResetRequest):
    reset_data = create_password_reset_token(payload.email, ttl_minutes=settings.password_reset_token_ttl_minutes)

    response = AuthRequestResetResponse(message="If that account exists, a reset link has been prepared.")
    if reset_data is not None and settings.environment != "production":
        response.reset_token = reset_data["token"]
        response.expires_at = reset_data["expires_at"]

    if reset_data is not None:
        try:
            evt = queue_password_reset_email(
                email=payload.email,
                reset_token=reset_data["token"],
            )
            send_transactional_email(evt["id"])
        except Exception as exc:
            logger.warning("Password reset email failed: %s", exc)

    return response


@app.post("/api/v1/auth/reset-password", response_model=AuthResetPasswordResponse)
def auth_reset_password(payload: AuthResetPasswordRequest):
    user = reset_password_with_token(payload.token, payload.new_password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    return AuthResetPasswordResponse(message="Password reset successfully.")


def _require_session(request: Request) -> dict:
    """
    FastAPI dependency for JWT session validation.
    
    Extracts and validates JWT token from Authorization: Bearer header.
    Returns token payload with user claims (sub=user_id, email, exp, iat, typ=session).
    
    Raises HTTP 401 if token is missing, invalid, or expired.
    Used on all protected routes requiring authenticated sessions.
    
    Token format: HS256 JWT with 7-day expiry, supports secret rotation via AUTH_TOKEN_SECRET_PREVIOUS.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token required in Authorization: Bearer header"
        )
    
    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session token is empty")
    
    payload = verify_session_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session token")
    
    # Payload contains: {sub: user_id, email, iat, exp, typ: "session"}
    return payload


@app.get("/api/v1/me")
def get_me(session: dict = Depends(_require_session)):
    """Return the current user's profile and API key information."""
    from sqlalchemy import select
    from .models import APIKey, UserAccount
    from .db import SessionLocal

    user_id = session.get("sub")
    with SessionLocal() as db:
        user = db.get(UserAccount, user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session is no longer valid. Please sign in again.",
            )

        key_info: dict | None = None
        if user.api_key_id:
            api_key = db.get(APIKey, user.api_key_id)
            if api_key:
                key_info = {
                    "id": api_key.id,
                    "name": api_key.name,
                    "key_prefix": api_key.key_prefix,
                    "is_active": api_key.is_active,
                    "is_paid": api_key.is_paid,
                    "rate_limit_per_minute": api_key.rate_limit_per_minute,
                    "trial_ends_at": api_key.trial_ends_at.isoformat() if api_key.trial_ends_at else None,
                    "created_at": api_key.created_at.isoformat(),
                }

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "created_at": user.created_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "api_key": key_info,
    }


@app.post("/api/v1/me/api-key/rotate")
def rotate_my_api_key(session: dict = Depends(_require_session)):
    user_id = session.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token")

    rotated = rotate_user_api_key(user_id)
    if rotated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    return rotated


@app.delete("/api/v1/me/api-key")
def revoke_my_api_key(session: dict = Depends(_require_session)):
    user_id = session.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token")

    revoked = revoke_user_api_key(user_id)
    if revoked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active API key found")

    return {"success": True, "message": "API key revoked."}


@app.post("/api/v1/auth/refresh")
def refresh_session_token(session: dict = Depends(_require_session)):
    """
    Refresh the current session token.
    
    Call this endpoint before token expiry (7 days) to extend the session.
    Frontend can automatically refresh tokens 1 day before expiry to maintain continuity.
    
    Request: Authorization: Bearer <current_token>
    Response: { token: <new_token> }
    
    New token has fresh 7-day expiry, same user claims and typ: session.
    """
    user_id = session.get("sub")
    email = session.get("email")
    
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token")
    if not isinstance(email, str) or not email.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token")
    
    # Generate fresh token with new expiry (7 days from now)
    new_token = create_session_token(user_id=user_id, email=email)
    
    return {
        "message": "Session refreshed successfully.",
        "token": new_token,
    }


@app.get("/api/v1/public/plans", response_model=PublicPlansResponse)
def public_plans():
    return PublicPlansResponse(
        plan_name=settings.default_plan_name,
        amount_inr=settings.default_plan_amount_inr,
        trial_days=settings.trial_default_days,
        includes=["Text", "Image", "Speech", "Automation"],
        plans=[
            {
                "name": item["name"],
                "price_usd": float(item["price_usd"]),
                "token_limit": item["token_limit"],
                "best_for": item["best_for"],
            }
            for item in PUBLIC_PLAN_CATALOG
        ],
    )


@app.post("/api/v1/public/signup-trial", response_model=PublicTrialSignupResponse)
def public_signup_trial(payload: PublicTrialSignupRequest):
    if not settings.trial_signup_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trial signup is disabled")

    try:
        result = create_trial_signup(
            name=payload.name,
            email=payload.email,
            company=payload.company,
            use_case=payload.use_case,
            source=payload.source,
            trial_days=settings.trial_default_days,
            rate_limit_per_minute=settings.trial_default_rate_limit_per_minute,
        )
    except SignupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        queue_welcome_email(
            name=result["name"],
            email=result["email"],
            api_key=result["api_key"],
            trial_ends_at=result["trial_ends_at"],
        )
    except Exception as exc:
        logger.warning("Public signup welcome email queue failed: %s", exc)
    try:
        welcome_evt = queue_welcome_email(
            name=result["name"],
            email=result["email"],
            api_key=result["api_key"],
            trial_ends_at=result["trial_ends_at"],
        )
        send_transactional_email(welcome_evt["id"])
    except Exception as exc:
        logger.warning("Public signup welcome email failed: %s", exc)

    return PublicTrialSignupResponse(**result)


@app.post("/api/v1/admin/api-keys")
def admin_create_api_key(payload: AdminCreateApiKeyRequest, request: Request):
    require_admin(request)
    return create_db_api_key(
        name=payload.name,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        trial_days=payload.trial_days,
        is_paid=False,
    )


@app.get("/api/v1/admin/api-keys")
def admin_list_api_keys(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    require_admin(request)
    rows = list_db_api_keys()
    total = len(rows)
    offset = (page - 1) * page_size
    paged = rows[offset: offset + page_size]
    return {
        "items": paged,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


@app.delete("/api/v1/admin/api-keys/{key_id}")
def admin_deactivate_api_key(key_id: str, request: Request):
    require_admin(request)
    deleted = deactivate_db_api_key(key_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    return {"success": True, "id": key_id}


@app.post("/api/v1/admin/api-keys/{key_id}/rotate")
def admin_rotate_api_key(key_id: str, request: Request):
    require_admin(request)
    result = rotate_db_api_key(key_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    return result


@app.patch("/api/v1/admin/api-keys/{key_id}/billing")
def admin_update_api_key_billing(key_id: str, payload: AdminUpdateApiKeyBillingRequest, request: Request):
    require_admin(request)
    updated = set_db_api_key_paid(key_id, payload.is_paid)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    return updated


@app.get("/api/v1/admin/usage")
def admin_usage_summary(request: Request, hours: int = Query(default=24, ge=1, le=24 * 30)):
    require_admin(request)
    return usage_summary(hours=hours)


@app.post("/api/v1/admin/emails/schedule-trial-reminders")
def admin_schedule_trial_reminder_emails(request: Request):
    require_admin(request)
    return schedule_trial_reminder_emails()


@app.post("/api/v1/admin/emails/send-pending")
def admin_send_pending_emails(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    require_admin(request)
    return send_pending_emails(limit=limit)


@app.post("/api/v1/admin/billing/razorpay/order", response_model=RazorpayOrderResponse)
def admin_create_razorpay_order(payload: AdminCreateRazorpayOrderRequest, request: Request):
    require_admin(request)
    try:
        order = create_razorpay_order(
            api_key_id=payload.api_key_id,
            amount_inr=payload.amount_inr,
            plan_name=payload.plan_name,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
            customer_phone=payload.customer_phone,
        )
    except BillingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return RazorpayOrderResponse(**order)


@app.post("/api/v1/admin/billing/razorpay/verify", response_model=RazorpayVerifyPaymentResponse)
def admin_verify_razorpay_payment(payload: RazorpayVerifyPaymentRequest, request: Request):
    require_admin(request)
    marked_paid = verify_and_mark_paid(
        api_key_id=payload.api_key_id,
        order_id=payload.razorpay_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
    )

    if marked_paid:
        contact = get_lead_contact_for_api_key(payload.api_key_id)
        if contact:
            try:
                queue_payment_success_email(
                    name=contact["name"],
                    email=contact["email"],
                    plan_name=settings.default_plan_name,
                )
            except Exception as exc:
                logger.warning("Payment success email queue failed: %s", exc)

    return RazorpayVerifyPaymentResponse(verified=marked_paid, marked_paid=marked_paid)


@app.post("/api/v1/billing/razorpay/webhook")
async def razorpay_webhook(request: Request):
    signature = request.headers.get("x-razorpay-signature", "")
    raw_body = await request.body()

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook JSON") from exc

    try:
        result = handle_razorpay_webhook(payload=payload, signature=signature, raw_body=raw_body)
    except BillingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if result.get("marked_paid") and result.get("api_key_id"):
        contact = get_lead_contact_for_api_key(result["api_key_id"])
        if contact:
            try:
                queue_payment_success_email(
                    name=contact["name"],
                    email=contact["email"],
                    plan_name=settings.default_plan_name,
                )
            except Exception as exc:
                logger.warning("Webhook payment email queue failed: %s", exc)

    return result


@app.post("/api/v1/billing/checkout")
def billing_checkout(payload: BillingCheckoutRequest, auth=Depends(require_api_key)):
    if not auth.requires_billing or not auth.key_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Checkout is available only for managed DB keys")

    # Look up the requested plan in the catalog to get the correct INR amount
    plan_entry = next(
        (p for p in PUBLIC_PLAN_CATALOG if p["name"].lower() == payload.plan_name.lower()),
        None,
    )
    if plan_entry is None or plan_entry.get("amount_inr", 0) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Plan '{payload.plan_name}' is not a purchasable plan.")
    amount_inr = plan_entry["amount_inr"]

    try:
        order = create_razorpay_order(
            api_key_id=auth.key_id,
            amount_inr=amount_inr,
            plan_name=payload.plan_name,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
            customer_phone=payload.customer_phone,
        )
    except BillingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return RazorpayOrderResponse(**order)


@app.post("/api/v1/billing/razorpay/verify", response_model=RazorpayVerifyPaymentResponse)
def billing_verify_razorpay_payment(payload: RazorpayVerifyPaymentRequest, auth=Depends(require_api_key)):
    if not auth.key_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Managed DB API key required for verification")

    if payload.api_key_id != auth.key_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key mismatch for payment verification")

    marked_paid = verify_and_mark_paid(
        api_key_id=auth.key_id,
        order_id=payload.razorpay_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
    )

    if marked_paid:
        contact = get_lead_contact_for_api_key(auth.key_id)
        if contact:
            try:
                queue_payment_success_email(
                    name=contact["name"],
                    email=contact["email"],
                    plan_name=settings.default_plan_name,
                )
            except Exception as exc:
                logger.warning("Payment success email queue failed: %s", exc)

            try:
                plan = payload.plan_name or settings.default_plan_name
                amount = payload.amount_inr if payload.amount_inr > 0 else settings.default_plan_amount_inr
                inv_evt = queue_invoice_email(
                    name=contact["name"],
                    email=contact["email"],
                    plan_name=plan,
                    amount_inr=amount,
                    razorpay_payment_id=payload.razorpay_payment_id,
                    razorpay_order_id=payload.razorpay_order_id,
                )
                send_transactional_email(inv_evt["id"])
            except Exception as exc:
                logger.warning("Invoice email failed: %s", exc)

    return RazorpayVerifyPaymentResponse(verified=marked_paid, marked_paid=marked_paid)


@app.post("/api/v1/text/generate", response_model=TextGenerateResponse)
def text_generate(request: Request, payload: TextGenerateRequest, auth=Depends(require_api_key)):
    try:
        text, model, provider = generate_text(
            prompt=payload.prompt,
            temperature=payload.temperature,
            max_output_tokens=payload.max_output_tokens,
        )

        return TextGenerateResponse(
            text=text,
            model=model,
            provider=provider,
            request_id=request.state.request_id,
        )
    except Exception as exc:
        logger.error("Text generation error for %s: %s", auth.key_label, exc, exc_info=True)
        raise HTTPException(
            status_code=_provider_exception_status_code(exc),
            detail="Text generation failed. Please try again later.",
        )


@app.post("/api/v1/image/generate", response_model=ImageGenerateResponse)
def image_generate(request: Request, payload: ImageGenerateRequest, auth=Depends(require_api_key)):
    try:
        image_url, image_b64, model, provider = generate_image(
            prompt=payload.prompt,
            size=payload.size,
        )

        return ImageGenerateResponse(
            image_url=image_url,
            image_b64=image_b64,
            model=model,
            provider=provider,
        )
    except Exception as exc:
        logger.error("Image generation error for %s: %s", auth.key_label, exc, exc_info=True)
        raise HTTPException(
            status_code=_provider_exception_status_code(exc),
            detail="Image generation failed. Please try again later.",
        )


@app.post("/api/v1/speech/transcribe", response_model=TranscriptionResponse)
async def speech_transcribe(request: Request, file: UploadFile = File(...), auth=Depends(require_api_key)):
    try:
        if file.content_type not in settings.allowed_audio_file_types_list:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type: {file.content_type}. Allowed types: {', '.join(settings.allowed_audio_file_types_list)}",
            )

        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

        max_size_bytes = settings.max_upload_file_size_mb * 1024 * 1024
        if file_size > max_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size: {settings.max_upload_file_size_mb}MB, got {file_size / (1024 * 1024):.2f}MB",
            )

        text, model, provider = transcribe_audio(
            file_obj=file.file,
            filename=file.filename,
            content_type=file.content_type,
        )

        return TranscriptionResponse(
            text=text,
            model=model,
            provider=provider,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Transcription error for %s: %s", auth.key_label, exc, exc_info=True)
        raise HTTPException(
            status_code=_provider_exception_status_code(exc),
            detail="Transcription failed. Please try again later.",
        )


@app.post("/api/v1/automation/run", response_model=AutomationRunResponse)
async def automation_run(request: Request, payload: AutomationRunRequest, auth=Depends(require_api_key)):
    try:
        steps = [step.model_dump() for step in payload.steps]
        results_raw = await run_automation_steps(steps)
        results = [StepResult(**item) for item in results_raw]

        success = all((r.status_code is None) or (200 <= r.status_code < 400) for r in results)

        return AutomationRunResponse(
            name=payload.name,
            success=success,
            results=results,
        )
    except Exception as exc:
        logger.error("Automation run error for %s: %s", auth.key_label, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Automation run failed. Please try again later.")


@app.get("/api/v1/me/usage")
def my_usage(auth=Depends(require_api_key), hours: int = Query(default=24, ge=1, le=24 * 30)):
    return per_key_usage_summary(
        key_id=auth.key_id,
        key_label=auth.key_label,
        hours=hours,
    )


@app.get("/robots.txt")
def robots_txt():
    return PlainTextResponse(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /docs\n"
        "Disallow: /redoc\n"
        f"Sitemap: {settings.public_base_url}/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
def sitemap_xml():
    base = settings.public_base_url.rstrip("/")
    urls = ["/", "/ui/index.html", "/ui/login.html", "/ui/signup.html", "/ui/developer.html"]
    xml_entries = "\n".join(
        f"  <url><loc>{base}{u}</loc><changefreq>weekly</changefreq></url>"
        for u in urls
    )
    return PlainTextResponse(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{xml_entries}\n"
        f"</urlset>\n",
        media_type="application/xml",
    )
