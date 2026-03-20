from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "BrainAPI"
    provider: str = Field(default="openai", validation_alias=AliasChoices("PROVIDER", "BRAINAPI_PROVIDER"))
    provider_fallback_order: str = "local,ollama,groq,gemini,huggingface,pollinations,together,openai"
    environment: str = "development"
    public_base_url: str = "http://localhost:8000"

    ollama_base_url: str = "http://localhost:11434"
    ollama_text_model: str = "llama3.2:3b"

    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_text_model: str = "llama-3.1-8b-instant"

    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_text_model: str = "gemini-1.5-flash"

    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_text_model: str = "claude-3-5-sonnet-latest"

    openai_api_key: str = ""
    openai_text_model: str = "gpt-4.1-mini"
    openai_image_model: str = "gpt-image-1"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    pollinations_base_url: str = "https://image.pollinations.ai"
    pollinations_image_model: str = "flux"

    huggingface_api_key: str = ""
    huggingface_image_model: str = "stabilityai/stable-diffusion-xl-base-1.0"

    together_api_key: str = ""
    together_base_url: str = "https://api.together.xyz/v1"
    together_text_model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"
    together_image_model: str = "black-forest-labs/FLUX.1-schnell-Free"
    together_transcription_model: str = "openai/whisper-large-v3"

    api_keys: str = ""
    require_api_key: bool = True
    rate_limit_per_minute: int = 60
    max_tokens_per_request: int = 4000
    max_tokens_per_minute: int = 40000
    public_paths: str = "/,/ui,/health,/docs,/openapi.json,/redoc,/robots.txt,/sitemap.xml,/api/v1/billing/razorpay/webhook,/api/v1/public/plans,/api/v1/public/reviews,/api/v1/public/signup-trial,/api/v1/auth/signup,/api/v1/auth/login,/api/v1/auth/request-reset,/api/v1/auth/reset-password"
    admin_api_key: str = ""
    routing_config_path: str = "brainapi.config.json"

    database_url: str = "sqlite:///./brainapi.db"
    auto_create_tables: bool = True

    redis_url: str = ""
    enable_usage_metering: bool = True

    cors_allow_origins: str = "http://localhost:3000,http://localhost:8000"
    cors_allow_methods: str = "GET,POST,PUT,DELETE,OPTIONS"
    cors_allow_headers: str = "Content-Type,Authorization,X-API-Key"

    automation_allowed_hosts: str = ""
    allow_private_webhook_targets: bool = False
    automation_max_steps: int = 20

    default_webhook_timeout_seconds: int = 15

    # File upload constraints
    max_upload_file_size_mb: int = 25  # 25 MB max file size
    allowed_audio_file_types: str = "audio/mpeg,audio/wav,audio/ogg,audio/flac,audio/m4a,audio/x-m4a"

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    default_plan_amount_inr: float = 499.0
    default_plan_name: str = "BrainAPI Pro"
    trial_signup_enabled: bool = True
    trial_default_days: int = 7
    trial_default_rate_limit_per_minute: int = 60

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from_address: str = ""
    email_from_name: str = "BrainAPI"
    email_reply_to: str = ""
    support_email: str = "brainapisupport@gmail.com"
    founder_name: str = "BrainAPI founder"
    blocked_email_domains: str = "example.com,test.com,fake.com"
    skip_email_in_development: bool = True
    auth_token_secret: str = "dev-brainapi-auth-secret"
    auth_token_secret_previous: str = ""
    password_reset_token_ttl_minutes: int = 30

    def csv_to_list(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def api_key_list(self) -> list[str]:
        return self.csv_to_list(self.api_keys)

    @property
    def public_path_list(self) -> list[str]:
        return self.csv_to_list(self.public_paths)

    @property
    def cors_allow_origins_list(self) -> list[str]:
        values = self.csv_to_list(self.cors_allow_origins)
        return values or ["*"]

    @property
    def cors_allow_methods_list(self) -> list[str]:
        values = self.csv_to_list(self.cors_allow_methods)
        return values or ["*"]

    @property
    def cors_allow_headers_list(self) -> list[str]:
        values = self.csv_to_list(self.cors_allow_headers)
        return values or ["*"]

    @property
    def automation_allowed_hosts_list(self) -> list[str]:
        return [host.lower() for host in self.csv_to_list(self.automation_allowed_hosts)]

    @property
    def allowed_audio_file_types_list(self) -> list[str]:
        return self.csv_to_list(self.allowed_audio_file_types)

    @property
    def blocked_email_domains_list(self) -> list[str]:
        return [item.lower() for item in self.csv_to_list(self.blocked_email_domains)]

    @property
    def normalized_database_url(self) -> str:
        url = (self.database_url or "").strip()
        lower_url = url.lower()
        if lower_url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://"):]
        if lower_url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @property
    def provider_name(self) -> str:
        return self.provider.strip().lower()

    @property
    def provider_fallback_order_list(self) -> list[str]:
        return [item.strip().lower() for item in self.provider_fallback_order.split(",") if item.strip()]

    def _provider_has_credentials(self, provider: str) -> bool:
        if provider == "openai":
            return bool(self.openai_api_key.strip())
        if provider == "together":
            return bool(self.together_api_key.strip())
        if provider == "groq":
            return bool(self.groq_api_key.strip())
        if provider == "gemini":
            return bool(self.gemini_api_key.strip())
        if provider == "anthropic":
            return bool(self.anthropic_api_key.strip())
        if provider == "ollama":
            return bool(self.ollama_base_url.strip())
        if provider == "mock":
            return True
        return False

    @property
    def provider_ready(self) -> bool:
        provider = self.provider_name
        if provider == "auto":
            return any(self._provider_has_credentials(item) for item in self.provider_fallback_order_list)
        return self._provider_has_credentials(provider)


settings = Settings()
