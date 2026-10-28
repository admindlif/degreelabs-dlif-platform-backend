"""
Pydantic schemas for the authentication and user management endpoints.

Separating request/response schemas from SQLAlchemy models keeps the
API contract decoupled from the database layer.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.user import AccountStatus, UserRole


# ---------------------------------------------------------------------------
# Admin: create Fellow
# ---------------------------------------------------------------------------


class CreateFellowRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr


class CreateFellowResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: EmailStr
    role: UserRole
    account_status: AccountStatus
    created_at: datetime
    invitation_sent: bool = True
    message: str = "Invitation email sent."

    model_config = {"from_attributes": True}


class InvitationDeliveryResponse(BaseModel):
    invitation_sent: bool
    message: str


# Backwards-compatible aliases
CreateStudentRequest = CreateFellowRequest
CreateStudentResponse = CreateFellowResponse


# ---------------------------------------------------------------------------
# Account activation
# ---------------------------------------------------------------------------


class ActivateAccountRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_must_match(self) -> "ActivateAccountRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class ActivateAccountResponse(BaseModel):
    message: str
    user_id: UUID
    email: EmailStr


class OnboardingResumeRequest(BaseModel):
    email: EmailStr
    password: str


class OnboardingResumeResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# 2FA setup
# ---------------------------------------------------------------------------


class TwoFASetupResponse(BaseModel):
    message: str
    qr_code_url: str


class ConfirmTwoFARequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class ConfirmTwoFAResponse(BaseModel):
    message: str
    recovery_codes: list[str]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    requires_2fa: bool
    requires_2fa_setup: bool = False


class VerifyTOTPRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class AuthenticationResponse(BaseModel):
    message: str


class SessionRefreshResponse(BaseModel):
    message: str = "Session refreshed successfully."


class SessionStatusResponse(BaseModel):
    authenticated: bool
    inactivity_expires_at: datetime | None = None
    absolute_expires_at: datetime | None = None
    effective_expires_at: datetime | None = None
    access_expires_at: datetime | None = None
    server_time: datetime


class SessionActivityResponse(BaseModel):
    inactivity_expires_at: datetime
    absolute_expires_at: datetime
    effective_expires_at: datetime
    server_time: datetime


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: EmailStr
    role: UserRole
    account_status: AccountStatus
    is_active: bool
    two_factor_enabled: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Legacy — kept temporarily for backward compatibility during transition
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """
    Deprecated: students no longer self-register.
    This schema is kept only to avoid import errors in any lingering
    references and will be removed in a future cleanup.
    """

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# Password Reset / Self-Service Recovery
# ---------------------------------------------------------------------------


class PasswordResetRequest(BaseModel):
    email: EmailStr
    portal: str = Field(default="fellow", pattern="^(fellow|mentor)$")


class PasswordResetRequestResponse(BaseModel):
    message: str
    recovery_request_id: UUID


class PasswordResetVerifyRequest(BaseModel):
    recovery_request_id: UUID
    code: str = Field(min_length=8, max_length=8, pattern="^[0-9]{8}$")


class PasswordResetVerifyResponse(BaseModel):
    reset_authorization: str


class PasswordResetResendRequest(BaseModel):
    recovery_request_id: UUID


class PasswordResetConfirmRequest(BaseModel):
    reset_authorization: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_must_match(self) -> "PasswordResetConfirmRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class PasswordResetConfirmResponse(BaseModel):
    message: str
