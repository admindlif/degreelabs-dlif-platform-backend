from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import EmailStr, Field, model_validator


class Settings(BaseSettings):
    app_name: str = "DLIF Platform API"
    environment: str = "development"

    # Database
    database_url: str = Field(min_length=1)

    # JWT
    secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=15, gt=0)

    # Server-managed authenticated sessions
    session_inactivity_timeout_minutes: int = Field(default=1440, gt=5)
    session_warning_minutes: int = Field(default=5, gt=0)
    session_absolute_fellow_hours: int = Field(default=168, gt=0)
    session_absolute_mentor_hours: int = Field(default=72, gt=0)
    session_absolute_privileged_hours: int = Field(default=24, gt=0)
    session_refresh_token_expire_hours: int = Field(default=24, gt=0)
    session_refresh_token_bytes: int = Field(default=48, ge=32, le=128)
    session_refresh_race_grace_seconds: int = Field(default=10, ge=1, le=60)
    session_activity_write_interval_seconds: int = Field(default=300, ge=30, le=3600)

    # Rollout controls. Privileged MFA can be enforced after Stage 2 ships
    # the enrollment UI. Sessionless access JWTs remain temporarily accepted
    # so already-issued tokens can expire naturally during deployment.
    privileged_mfa_enforcement_enabled: bool = False
    allow_legacy_sessionless_access_tokens: bool = True

    # Authentication failure throttles (shared through PostgreSQL).
    auth_login_attempt_limit: int = Field(default=5, ge=2, le=100)
    auth_login_window_minutes: int = Field(default=15, ge=1, le=1440)
    auth_two_fa_attempt_limit: int = Field(default=5, ge=2, le=100)
    auth_two_fa_window_minutes: int = Field(default=10, ge=1, le=1440)
    auth_recovery_attempt_limit: int = Field(default=10, ge=2, le=100)
    auth_recovery_window_minutes: int = Field(default=15, ge=1, le=1440)
    auth_refresh_attempt_limit: int = Field(default=30, ge=2, le=500)
    auth_refresh_window_minutes: int = Field(default=5, ge=1, le=1440)

    # Comma-separated proxy CIDRs whose Forwarded/X-Forwarded-For headers may
    # be trusted. Empty by default so direct clients cannot spoof rate-limit
    # or audit identities. Configure this only with verified ingress ranges.
    trusted_proxy_cidrs: str | None = None

    # Invitation
    invitation_token_expire_hours: int = Field(default=72, gt=0)

    # 2FA
    totp_issuer: str = "DegreeLabs DLIF"
    two_fa_challenge_expire_minutes: int = Field(default=10, gt=0)

    onboarding_token_expire_minutes: int = Field(default=20, gt=0)

    # Authentication cookies.  ``None`` makes Secure follow the environment:
    # enabled in production and disabled for local HTTP development.
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_cookie_domain: str | None = None

    # Frontend
    frontend_base_url: str = Field(min_length=1)
    mentor_frontend_base_url: str = Field(default="http://localhost:3003", min_length=1)
    admin_frontend_base_url: str = Field(default="http://localhost:3002", min_length=1)
    cors_allowed_origins: str | None = None

    # Email
    email_backend: Literal["logging", "smtp"] = "logging"

    email_from_address: EmailStr
    email_from_name: str = Field(min_length=1)

    smtp_host: str = Field(default="smtp.gmail.com", min_length=1)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_logo_url: str | None = None


    # Google Meet
    google_meet_enabled: bool = False
    google_meet_base_url: str = "https://meet.googleapis.com/v2"

    google_oauth_client_file: str | None = None
    google_oauth_token_file: str | None = None

    # Google Calendar
    google_calendar_enabled: bool = False
    google_calendar_timezone: str = "Asia/Kolkata"

    google_service_account_file: str | None = None
    google_workspace_organizer_email: str | None = None

    # tl;dv Integration
    tldv_api_key: str | None = None
    tldv_api_base_url: str = "https://pasta.tldv.io"
    tldv_mock_mode: bool = False
    # Shared secret used to verify incoming tl;dv webhook requests.
    # Set this in tl;dv dashboard and match it here via TLDV_WEBHOOK_SECRET env var.
    # If unset, the webhook endpoint will reject all incoming requests.
    tldv_webhook_secret: str | None = None

    # Cloudflare R2
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str | None = None
    r2_endpoint: str | None = None
    r2_presigned_url_expiry_seconds: int = Field(
        default=900,
        ge=60,
        le=604800,
    )

    @property
    def allowed_origins(self) -> list[str]:
        configured = self.cors_allowed_origins
        if configured:
            candidates = configured.split(",")
        elif self.environment.strip().lower() == "production":
            candidates = [
                self.frontend_base_url,
                self.mentor_frontend_base_url,
                self.admin_frontend_base_url,
            ]
        else:
            candidates = [
                self.frontend_base_url,
                self.mentor_frontend_base_url,
                "http://localhost:3000",
                "http://localhost:3001",
                "http://localhost:3002",
                "http://localhost:3003",
            ]

        # Preserve order while removing whitespace, trailing slashes, and dupes.
        return list(
            dict.fromkeys(
                item.strip().rstrip("/")
                for item in candidates
                if item.strip()
            )
        )

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def effective_auth_cookie_secure(self) -> bool:
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.is_production

    @property
    def effective_auth_cookie_domain(self) -> str | None:
        if self.auth_cookie_domain is None:
            return None
        return self.auth_cookie_domain.strip() or None

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if not self.database_url.strip():
            raise ValueError("DATABASE_URL must not be blank.")

        for label, origin in [
            ("FRONTEND_BASE_URL", self.frontend_base_url),
            ("MENTOR_FRONTEND_BASE_URL", self.mentor_frontend_base_url),
            ("ADMIN_FRONTEND_BASE_URL", self.admin_frontend_base_url),
            *(("CORS_ALLOWED_ORIGINS", item) for item in self.allowed_origins),
        ]:
            parsed = urlsplit(origin)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or (parsed.path not in {"", "/"})
            ):
                raise ValueError(f"{label} must contain valid HTTP(S) origins.")

        is_production = self.is_production
        if is_production:
            if self.email_backend != "smtp":
                raise ValueError("EMAIL_BACKEND must be 'smtp' in production.")
            if any(
                urlsplit(origin).hostname in {"localhost", "127.0.0.1", "::1"}
                for origin in self.allowed_origins
            ):
                raise ValueError("Production CORS origins cannot target localhost.")
            if not self.effective_auth_cookie_secure:
                raise ValueError("AUTH_COOKIE_SECURE must be true in production.")

        if (
            self.auth_cookie_samesite == "none"
            and not self.effective_auth_cookie_secure
        ):
            raise ValueError(
                "AUTH_COOKIE_SAMESITE='none' requires AUTH_COOKIE_SECURE=true."
            )

        if self.session_warning_minutes >= self.session_inactivity_timeout_minutes:
            raise ValueError(
                "SESSION_WARNING_MINUTES must be shorter than the inactivity timeout."
            )

        if self.email_backend == "smtp" and not (
            self.smtp_username and self.smtp_password
        ):
            raise ValueError(
                "SMTP_USERNAME and SMTP_PASSWORD are required when "
                "EMAIL_BACKEND is 'smtp'."
            )

        if (
            self.google_meet_enabled
            or self.google_calendar_enabled
        ) and not self.google_oauth_token_file:
            raise ValueError(
                "Google Calendar/Meet integration requires "
                "GOOGLE_OAUTH_TOKEN_FILE."
            )


        return self

    model_config = SettingsConfigDict(
        env_file=(
            str(Path(__file__).resolve().parent.parent.parent / ".env"),
            str(Path(__file__).resolve().parent.parent.parent.parent / ".env"),
            ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
