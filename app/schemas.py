from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, HttpUrl


class TextGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_output_tokens: int = Field(
        default=300,
        ge=1,
        le=4000,
        validation_alias=AliasChoices("max_output_tokens", "max_tokens"),
    )

class TextGenerateResponse(BaseModel):
    text: str
    model: str
    provider: str
    request_id: str | None = None


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    size: Literal["256x256", "512x512", "1024x1024", "1024x1536", "1536x1024"] = "1024x1024"


class ImageGenerateResponse(BaseModel):
    image_url: str | None = None
    image_b64: str | None = None
    model: str
    provider: str


class TranscriptionResponse(BaseModel):
    text: str
    model: str
    provider: str


class SendEmailRequest(BaseModel):
    email: str = Field(min_length=5, max_length=190)
    subject: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=4000)


class SendEmailResponse(BaseModel):
    success: bool
    message: str
    error: str | None = None


class AdminCreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1)
    rate_limit_per_minute: int | None = Field(default=None, gt=0)
    trial_days: int = Field(default=0, ge=0, le=365)


class AdminUpdateApiKeyBillingRequest(BaseModel):
    is_paid: bool


class AdminCreateRazorpayOrderRequest(BaseModel):
    api_key_id: str = Field(min_length=1)
    amount_inr: float = Field(gt=0)
    plan_name: str = Field(default="BrainAPI Pro", min_length=1, max_length=120)
    customer_name: str | None = Field(default=None, max_length=120)
    customer_email: str | None = Field(default=None, max_length=160)
    customer_phone: str | None = Field(default=None, max_length=32)


class RazorpayOrderResponse(BaseModel):
    order_id: str
    amount_inr: float
    amount_paise: int
    currency: str = "INR"
    key_id: str
    api_key_id: str
    plan_name: str


class RazorpayVerifyPaymentRequest(BaseModel):
    api_key_id: str = Field(min_length=1)
    razorpay_order_id: str = Field(min_length=1)
    razorpay_payment_id: str = Field(min_length=1)
    razorpay_signature: str = Field(min_length=1)
    plan_name: str = Field(default="BrainAPI Pro", min_length=1, max_length=120)
    amount_inr: float = Field(default=0.0, ge=0)


class RazorpayVerifyPaymentResponse(BaseModel):
    verified: bool
    marked_paid: bool


class BillingCheckoutRequest(BaseModel):
    plan_name: str = Field(min_length=1, max_length=120)
    customer_name: str | None = Field(default=None, max_length=120)
    customer_email: str | None = Field(default=None, max_length=160)
    customer_phone: str | None = Field(default=None, max_length=32)


class PublicPlanTier(BaseModel):
    name: str
    price_usd: float
    price_inr: float
    token_limit: str
    best_for: str
    cta_label: str | None = None
    popular: bool = False


class PublicPlansResponse(BaseModel):
    plan_name: str
    amount_inr: float
    trial_days: int
    includes: list[str]
    plans: list[PublicPlanTier]


class ProductReviewItem(BaseModel):
    id: str
    display_name: str
    role: str | None = None
    rating: int
    headline: str
    body_text: str
    verified_customer: bool = False
    created_at: datetime


class PublicReviewsResponse(BaseModel):
    items: list[ProductReviewItem]
    total_reviews: int
    average_rating: float


class SubmitReviewRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    headline: str = Field(min_length=4, max_length=140)
    body_text: str = Field(min_length=20, max_length=2000)
    role: str | None = Field(default=None, max_length=120)


class SubmitReviewResponse(BaseModel):
    success: bool = True
    message: str
    review_id: str
    status: str


class ReviewModerationRequest(BaseModel):
    status: Literal["approved", "rejected"]


class PublicTrialSignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=5, max_length=190)
    company: str | None = Field(default=None, max_length=160)
    use_case: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default="website", max_length=80)
    consent: bool = True


class PublicTrialSignupResponse(BaseModel):
    lead_id: str
    name: str
    email: str
    api_key_id: str
    api_key: str
    key_prefix: str
    trial_ends_at: str | None
    is_paid: bool
    rate_limit_per_minute: int | None
    dashboard_url: str | None = None
    quickstart_url: str | None = None
    support_email: str | None = None


class AuthSignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=5, max_length=190)
    password: str = Field(min_length=8, max_length=200)
    newsletter_opt_in: bool = False


class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=190)
    password: str = Field(min_length=8, max_length=200)


class AuthRequestResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=190)


class AuthResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=300)
    new_password: str = Field(min_length=8, max_length=200)


class AuthUserResponse(BaseModel):
    id: str
    name: str
    email: str
    api_key_id: str | None
    created_at: datetime
    last_login_at: datetime | None = None


class AuthLoginResponse(BaseModel):
    success: bool = True
    message: str
    token: str
    token_type: str = "bearer"
    user: AuthUserResponse


class AuthSignupResponse(AuthLoginResponse):
    api_key: str
    key_prefix: str
    trial_ends_at: datetime | None = None
    dashboard_url: str | None = None
    quickstart_url: str | None = None
    support_email: str | None = None


class AuthRequestResetResponse(BaseModel):
    success: bool = True
    message: str
    reset_token: str | None = None
    expires_at: datetime | None = None


class AuthResetPasswordResponse(BaseModel):
    success: bool = True
    message: str


class WebhookStep(BaseModel):
    type: Literal["webhook"]
    url: HttpUrl
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None


class DelayStep(BaseModel):
    type: Literal["delay"]
    seconds: float = Field(ge=0.0, le=60.0)


AutomationStep = WebhookStep | DelayStep


class AutomationRunRequest(BaseModel):
    name: str = Field(min_length=1)
    steps: list[AutomationStep] = Field(min_length=1)


class StepResult(BaseModel):
    step_index: int
    type: str
    status_code: int | None = None
    detail: str


class AutomationRunResponse(BaseModel):
    name: str
    success: bool
    results: list[StepResult]
