from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, func, select

from .db import SessionLocal
from .models import ProductReview, UserAccount


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def submit_product_review(
    *,
    user_id: str,
    rating: int,
    headline: str,
    body_text: str,
    role: str | None,
) -> dict:
    now = _utc_now()
    clean_role = (role or "").strip() or None

    with SessionLocal() as db:
        user = db.get(UserAccount, user_id)
        if user is None or not user.is_active:
            raise ValueError("Active user account required to submit a review.")

        existing = db.scalar(select(ProductReview).where(ProductReview.user_id == user_id))
        if existing is None:
            review = ProductReview(
                user_id=user.id,
                display_name=user.name,
                email=user.email,
                role=clean_role,
                rating=rating,
                headline=headline.strip(),
                body_text=body_text.strip(),
                status="pending",
                verified_customer=bool(user.api_key_id),
                created_at=now,
                updated_at=now,
            )
            db.add(review)
        else:
            review = existing
            review.display_name = user.name
            review.email = user.email
            review.role = clean_role
            review.rating = rating
            review.headline = headline.strip()
            review.body_text = body_text.strip()
            review.status = "pending"
            review.verified_customer = bool(user.api_key_id)
            review.approved_at = None
            review.updated_at = now

        db.commit()
        db.refresh(review)

        return {
            "id": review.id,
            "status": review.status,
            "message": "Review submitted. It will appear on the homepage after approval.",
        }


def list_public_reviews(limit: int = 6) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(ProductReview)
            .where(ProductReview.status == "approved")
            .order_by(desc(ProductReview.approved_at), desc(ProductReview.created_at))
            .limit(limit)
        ).scalars().all()

        total_reviews = db.scalar(
            select(func.count(ProductReview.id)).where(ProductReview.status == "approved")
        ) or 0

        average_rating = db.scalar(
            select(func.avg(ProductReview.rating)).where(ProductReview.status == "approved")
        )

    return {
        "items": [
            {
                "id": row.id,
                "display_name": row.display_name,
                "role": row.role,
                "rating": int(row.rating),
                "headline": row.headline,
                "body_text": row.body_text,
                "verified_customer": bool(row.verified_customer),
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "total_reviews": int(total_reviews),
        "average_rating": round(float(average_rating or 0.0), 2),
    }


def list_admin_reviews(status: str = "pending", limit: int = 50) -> dict:
    normalized_status = (status or "pending").strip().lower()
    if normalized_status not in {"pending", "approved", "rejected", "all"}:
        raise ValueError("Unsupported review status filter.")

    with SessionLocal() as db:
        query = select(ProductReview).order_by(desc(ProductReview.updated_at), desc(ProductReview.created_at)).limit(limit)
        if normalized_status != "all":
            query = query.where(ProductReview.status == normalized_status)
        rows = db.execute(query).scalars().all()

    return {
        "items": [
            {
                "id": row.id,
                "display_name": row.display_name,
                "email": row.email,
                "role": row.role,
                "rating": int(row.rating),
                "headline": row.headline,
                "body_text": row.body_text,
                "status": row.status,
                "verified_customer": bool(row.verified_customer),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "approved_at": row.approved_at,
            }
            for row in rows
        ]
    }


def moderate_review(*, review_id: str, status: str) -> dict | None:
    normalized_status = (status or "").strip().lower()
    if normalized_status not in {"approved", "rejected"}:
        raise ValueError("Review status must be 'approved' or 'rejected'.")

    now = _utc_now()
    with SessionLocal() as db:
        review = db.get(ProductReview, review_id)
        if review is None:
            return None

        review.status = normalized_status
        review.approved_at = now if normalized_status == "approved" else None
        review.updated_at = now
        db.commit()
        db.refresh(review)

        return {
            "id": review.id,
            "status": review.status,
            "approved_at": review.approved_at,
        }
