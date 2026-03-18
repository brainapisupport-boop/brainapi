import hashlib
import hmac
import json
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import APIKey, PasswordResetToken, UserAccount


@dataclass
class AuthIdentity:
    key_label: str
    key_id: str | None
    rate_limit_per_minute: int | None
    is_paid: bool
    trial_ends_at: datetime | None
    requires_billing: bool


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    iterations = 310_000
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_hex, digest_hex = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (TypeError, ValueError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _serialize_user(user: UserAccount) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "api_key_id": user.api_key_id,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


def get_user_by_email(email: str) -> dict | None:
    normalized_email = _normalize_email(email)
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.email == normalized_email))
        if user is None:
            return None
        return _serialize_user(user)


def create_user_account(
    *,
    name: str,
    email: str,
    password: str,
    api_key_id: str | None,
    newsletter_opt_in: bool = False,
) -> dict:
    normalized_email = _normalize_email(email)
    with SessionLocal() as db:
        existing = db.scalar(select(UserAccount).where(UserAccount.email == normalized_email))
        if existing is not None:
            raise ValueError("An account already exists for this email")

        user = UserAccount(
            name=name.strip()[:120],
            email=normalized_email[:190],
            password_hash=hash_password(password),
            api_key_id=api_key_id,
            newsletter_opt_in=bool(newsletter_opt_in),
            is_active=True,
            created_at=_utc_now(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return _serialize_user(user)


def authenticate_user(email: str, password: str) -> dict | None:
    normalized_email = _normalize_email(email)
    with SessionLocal() as db:
        user = db.scalar(
            select(UserAccount)
            .where(UserAccount.email == normalized_email)
            .where(UserAccount.is_active.is_(True))
        )
        if user is None or not verify_password(password, user.password_hash):
            return None

        user.last_login_at = _utc_now()
        db.commit()
        db.refresh(user)
        return _serialize_user(user)


def create_password_reset_token(email: str, ttl_minutes: int) -> dict | None:
    normalized_email = _normalize_email(email)
    with SessionLocal() as db:
        user = db.scalar(
            select(UserAccount)
            .where(UserAccount.email == normalized_email)
            .where(UserAccount.is_active.is_(True))
        )
        if user is None:
            return None

        raw_token = secrets.token_urlsafe(32)
        now = _utc_now()
        entity = PasswordResetToken(
            user_id=user.id,
            token_hash=hash_api_key(raw_token),
            expires_at=now + timedelta(minutes=ttl_minutes),
            created_at=now,
        )
        db.add(entity)
        db.commit()

        return {
            "token": raw_token,
            "expires_at": entity.expires_at,
            "user": _serialize_user(user),
        }


def reset_password_with_token(token: str, new_password: str) -> dict | None:
    hashed_token = hash_api_key(token)
    now = _utc_now()
    with SessionLocal() as db:
        token_row = db.scalar(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == hashed_token)
            .where(PasswordResetToken.used_at.is_(None))
            .where(PasswordResetToken.expires_at >= now)
        )
        if token_row is None:
            return None

        user = db.get(UserAccount, token_row.user_id)
        if user is None or not user.is_active:
            return None

        user.password_hash = hash_password(new_password)
        token_row.used_at = now
        db.commit()
        db.refresh(user)
        return _serialize_user(user)


def _b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return urlsafe_b64decode((data + padding).encode("ascii"))


def create_session_token(*, user_id: str, email: str) -> str:
    expires_at = int((_utc_now() + timedelta(days=7)).timestamp())
    payload = {"sub": user_id, "email": email, "exp": expires_at, "typ": "session"}
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded_payload = _b64url_encode(payload_bytes)
    signature = hmac.new(
        settings.auth_token_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_b64url_encode(signature)}"


def verify_session_token(token: str) -> dict | None:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(
        settings.auth_token_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()

    try:
        actual_signature = _b64url_decode(encoded_signature)
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not hmac.compare_digest(actual_signature, expected_signature):
        return None

    if payload.get("typ") != "session":
        return None

    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at < int(_utc_now().timestamp()):
        return None

    return payload


def make_api_key() -> str:
    return f"brn_{secrets.token_urlsafe(32)}"


def key_prefix(raw_key: str) -> str:
    return raw_key[:12]


def verify_user_api_key(raw_key: str) -> AuthIdentity | None:
    static_keys = settings.api_key_list
    for index, key in enumerate(static_keys):
        if hmac.compare_digest(raw_key, key):
            return AuthIdentity(
                key_label=f"env-key-{index + 1}",
                key_id=None,
                rate_limit_per_minute=None,
                is_paid=True,
                trial_ends_at=None,
                requires_billing=False,
            )

    with SessionLocal() as db:
        candidates = db.scalars(
            select(APIKey).where(APIKey.is_active.is_(True), APIKey.key_prefix == key_prefix(raw_key))
        ).all()
        hashed = hash_api_key(raw_key)
        for candidate in candidates:
            if hmac.compare_digest(candidate.key_hash, hashed):
                return AuthIdentity(
                    key_label=f"db:{candidate.name}",
                    key_id=candidate.id,
                    rate_limit_per_minute=candidate.rate_limit_per_minute,
                    is_paid=bool(candidate.is_paid),
                    trial_ends_at=candidate.trial_ends_at,
                    requires_billing=True,
                )

    return None


def create_db_api_key(name: str, rate_limit_per_minute: int | None, trial_days: int = 30, is_paid: bool = False) -> dict:
    raw_key = make_api_key()
    effective_trial_ends_at = None
    if not is_paid and trial_days > 0:
        effective_trial_ends_at = datetime.now(timezone.utc) + timedelta(days=trial_days)

    entity = APIKey(
        name=name,
        key_prefix=key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
        is_active=True,
        is_paid=is_paid,
        rate_limit_per_minute=rate_limit_per_minute,
        trial_ends_at=effective_trial_ends_at,
        created_at=datetime.now(timezone.utc),
    )
    with SessionLocal() as db:
        db.add(entity)
        db.commit()
        db.refresh(entity)

    return {
        "id": entity.id,
        "name": entity.name,
        "api_key": raw_key,
        "key_prefix": entity.key_prefix,
        "is_active": entity.is_active,
        "is_paid": entity.is_paid,
        "rate_limit_per_minute": entity.rate_limit_per_minute,
        "trial_ends_at": entity.trial_ends_at,
        "created_at": entity.created_at,
    }


def list_db_api_keys() -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(APIKey).order_by(APIKey.created_at.desc())).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "key_prefix": row.key_prefix,
                "is_active": row.is_active,
                "is_paid": row.is_paid,
                "rate_limit_per_minute": row.rate_limit_per_minute,
                "trial_ends_at": row.trial_ends_at,
                "created_at": row.created_at,
            }
            for row in rows
        ]


def deactivate_db_api_key(key_id: str) -> bool:
    with SessionLocal() as db:
        row = db.get(APIKey, key_id)
        if row is None:
            return False
        row.is_active = False
        db.commit()
        return True


def rotate_db_api_key(key_id: str) -> dict | None:
    """Generate a new secret for an existing API key, keeping the same id and metadata."""
    new_raw_key = make_api_key()
    with SessionLocal() as db:
        row = db.get(APIKey, key_id)
        if row is None:
            return None
        row.key_prefix = key_prefix(new_raw_key)
        row.key_hash = hash_api_key(new_raw_key)
        db.commit()
        db.refresh(row)
    return {
        "id": row.id,
        "name": row.name,
        "api_key": new_raw_key,
        "key_prefix": row.key_prefix,
        "is_active": row.is_active,
        "is_paid": row.is_paid,
        "rate_limit_per_minute": row.rate_limit_per_minute,
        "trial_ends_at": row.trial_ends_at,
        "created_at": row.created_at,
    }


def rotate_user_api_key(user_id: str) -> dict | None:
    normalized_user_id = (user_id or "").strip()
    if not normalized_user_id:
        return None

    with SessionLocal() as db:
        user = db.scalar(
            select(UserAccount)
            .where(UserAccount.id == normalized_user_id)
            .where(UserAccount.is_active.is_(True))
        )
        if user is None:
            return None

        current_key_id = user.api_key_id
        user_email = user.email

    if current_key_id:
        rotated = rotate_db_api_key(current_key_id)
        if rotated is not None:
            return rotated

    default_name = f"{user_email.split('@')[0]}-key" if user_email else "user-key"
    created = create_db_api_key(
        name=default_name[:120],
        rate_limit_per_minute=settings.trial_default_rate_limit_per_minute,
        trial_days=settings.trial_default_days,
        is_paid=False,
    )

    with SessionLocal() as db:
        user = db.get(UserAccount, normalized_user_id)
        if user is None or not user.is_active:
            return None
        user.api_key_id = created["id"]
        db.commit()

    return created


def revoke_user_api_key(user_id: str) -> bool | None:
    normalized_user_id = (user_id or "").strip()
    if not normalized_user_id:
        return None

    with SessionLocal() as db:
        user = db.scalar(
            select(UserAccount)
            .where(UserAccount.id == normalized_user_id)
            .where(UserAccount.is_active.is_(True))
        )
        if user is None:
            return None

        if not user.api_key_id:
            return False

        row = db.get(APIKey, user.api_key_id)
        if row is not None:
            row.is_active = False

        user.api_key_id = None
        db.commit()

    return True


def get_db_api_key(key_id: str) -> dict | None:
    with SessionLocal() as db:
        row = db.get(APIKey, key_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "key_prefix": row.key_prefix,
            "is_active": row.is_active,
            "is_paid": row.is_paid,
            "rate_limit_per_minute": row.rate_limit_per_minute,
            "trial_ends_at": row.trial_ends_at,
            "created_at": row.created_at,
        }


def set_db_api_key_paid(key_id: str, is_paid: bool) -> dict | None:
    with SessionLocal() as db:
        row = db.get(APIKey, key_id)
        if row is None:
            return None

        row.is_paid = is_paid
        if is_paid:
            row.trial_ends_at = None
        elif row.trial_ends_at is None:
            row.trial_ends_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(row)

        return {
            "id": row.id,
            "name": row.name,
            "key_prefix": row.key_prefix,
            "is_active": row.is_active,
            "is_paid": row.is_paid,
            "rate_limit_per_minute": row.rate_limit_per_minute,
            "trial_ends_at": row.trial_ends_at,
            "created_at": row.created_at,
        }
