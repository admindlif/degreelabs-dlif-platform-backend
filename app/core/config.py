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
    access_token_expire_minutes: int = Field(default=30, gt=0)

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

    @property
    def allowed_origins(self) -> list[str]:
        configured = self.cors_allowed_origins
        if configured:
            candidates = configured.split(",")
        elif self.environment.strip().lower() == "production":
            candidates = [self.frontend_base_url]
        else:
            candidates = [
                self.frontend_base_url,
                "http://localhost:3000",
                "http://localhost:3002",
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
