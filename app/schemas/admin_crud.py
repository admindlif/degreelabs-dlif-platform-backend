"""
Pydantic schemas for Admin CRUD operations.

Covers: Programs, Cohorts, Phases, Weeks, Sessions, Teams/Memberships, Resources, Fellows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.url_validation import validate_external_url


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


class ProgramCreate(BaseModel):
    name: str = Field(..., max_length=200)
    code: str = Field(..., max_length=50)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: bool = True


class ProgramUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    code: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: Optional[bool] = None


class ProgramResponse(BaseModel):
    id: UUID
    name: str
    code: str
    description: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


class PhaseCreate(BaseModel):
    program_id: UUID
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    development_role: str = Field(..., max_length=50)
    sequence: int = Field(..., ge=1)
    description: Optional[str] = Field(None, max_length=1000)
    duration_weeks: int = Field(4, ge=1)
    is_active: bool = True


class PhaseUpdate(BaseModel):
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, max_length=100)
    development_role: Optional[str] = Field(None, max_length=50)
    sequence: Optional[int] = Field(None, ge=1)
    description: Optional[str] = Field(None, max_length=1000)
    duration_weeks: Optional[int] = Field(None, ge=1)
    is_active: Optional[bool] = None


class PhaseResponse(BaseModel):
    id: UUID
    program_id: UUID
    code: str
    name: str
    development_role: str
    sequence: int
    description: Optional[str]
    duration_weeks: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------


class CohortCreate(BaseModel):
    program_id: UUID
    name: str = Field(..., max_length=200)
    code: str = Field(..., max_length=50)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: str = "active"


class CohortUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    code: Optional[str] = Field(None, max_length=50)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = None


class CohortResponse(BaseModel):
    id: UUID
    program_id: UUID
    name: str
    code: str
    start_date: Optional[date]
    end_date: Optional[date]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------


class WeekCreate(BaseModel):
    phase_id: UUID
    week_number: int = Field(..., ge=1, le=12)
    title: str = Field(..., max_length=200)
    strategic_question: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = Field(None, max_length=1000)
    sequence: int = Field(..., ge=1)
    unlock_at: Optional[datetime] = None


class WeekUpdate(BaseModel):
    week_number: Optional[int] = Field(None, ge=1, le=12)
    title: Optional[str] = Field(None, max_length=200)
    strategic_question: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = Field(None, max_length=1000)
    sequence: Optional[int] = Field(None, ge=1)
    unlock_at: Optional[datetime] = None


class WeekResponse(BaseModel):
    id: UUID
    phase_id: UUID
    week_number: int
    title: str
    strategic_question: Optional[str]
    description: Optional[str]
    sequence: int
    unlock_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    cohort_id: UUID
    phase_id: UUID
    week_id: Optional[UUID] = None
    session_number: int = Field(..., ge=0)
    session_type: str = "learn_work"
    title: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    meeting_url: Optional[str] = Field(None, max_length=500)
    recording_url: Optional[str] = Field(None, max_length=500)
    status: str = "scheduled"
    sequence: int = Field(..., ge=0)
    unlock_at: Optional[datetime] = None
    submission_enabled: bool = False
    transcript_url: Optional[str] = Field(
        None,
        max_length=500,
    )   

    @field_validator("meeting_url", "recording_url", "transcript_url", mode="before")
    @classmethod
    def validate_urls(cls, value: object) -> str | None:
        return validate_external_url(value)


class SessionUpdate(BaseModel):
    week_id: Optional[UUID] = None
    session_number: Optional[int] = Field(None, ge=0)
    session_type: Optional[str] = None
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    meeting_url: Optional[str] = Field(None, max_length=500)
    recording_url: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = None
    sequence: Optional[int] = Field(None, ge=0)
    unlock_at: Optional[datetime] = None
    submission_enabled: Optional[bool] = None
    transcript_url: Optional[str] = Field(
        None,
        max_length=500,
    )

    @field_validator("meeting_url", "recording_url", "transcript_url", mode="before")
    @classmethod
    def validate_urls(cls, value: object) -> str | None:
        return validate_external_url(value)


class SessionResponse(BaseModel):
    id: UUID
    cohort_id: UUID
    phase_id: UUID
    week_id: Optional[UUID]

    session_number: int
    session_type: str

    title: str
    description: Optional[str]

    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

    unlock_at: Optional[datetime] = None
    submission_enabled: bool = False

    meeting_url: Optional[str]
    recording_url: Optional[str]
    transcript_url: Optional[str] = None

    meeting_provider: Optional[str] = None
    google_meet_space_name: Optional[str] = None
    google_meet_code: Optional[str] = None
    google_calendar_event_id: Optional[str] = None
    google_calendar_event_url: Optional[str] = None

    status: str
    sequence: int

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionAccessStateResponse(BaseModel):
    id: UUID
    session_number: int
    is_unlocked: bool
    unlock_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


class TeamCreate(BaseModel):
    cohort_id: UUID
    name: str = Field(..., max_length=100)
    company_challenge: Optional[str] = Field(None, max_length=300)
    company_name: Optional[str] = Field(None, max_length=200)
    is_active: bool = True
    company_overview: Optional[str] = Field(
        None,
        max_length=3000,
    )

    challenge_description: Optional[str] = Field(
        None,
        max_length=5000,
    )


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(
        None,
        max_length=100,
    )
    company_challenge: Optional[str] = Field(
        None,
        max_length=300,
    )
    company_name: Optional[str] = Field(
        None,
        max_length=200,
    )
    company_overview: Optional[str] = Field(
        None,
        max_length=3000,
    )
    challenge_description: Optional[str] = Field(
        None,
        max_length=5000,
    )
    is_active: Optional[bool] = None

class TeamChallengeUpdate(BaseModel):
    company_name: Optional[str] = Field(
        None,
        max_length=200,
    )
    company_overview: Optional[str] = Field(
        None,
        max_length=3000,
    )
    company_challenge: Optional[str] = Field(
        None,
        max_length=300,
    )
    challenge_description: Optional[str] = Field(
        None,
        max_length=5000,
    )

class TeamMemberAdd(BaseModel):
    user_id: UUID
    team_role: Literal["lead", "member"] = "member"


class TeamLeadAssign(BaseModel):
    user_id: UUID

class TeamMemberResponse(BaseModel):
    id: UUID
    user_id: UUID
    team_role: str
    joined_at: datetime
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None

    model_config = {"from_attributes": True}


class TeamResponse(BaseModel):
    id: UUID
    cohort_id: UUID
    name: str
    company_challenge: Optional[str]
    company_name: Optional[str]
    company_overview: Optional[str]
    challenge_description: Optional[str]
    is_active: bool
    member_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TeamDetailResponse(TeamResponse):
    members: list[TeamMemberResponse] = Field(
        default_factory=list
    )

class TeamChallengeResourceCreate(BaseModel):
    title: str = Field(
        ...,
        max_length=300,
    )

    resource_type: Literal["link"] = "link"

    url: str = Field(
        ...,
        max_length=1000,
    )

    is_downloadable: bool = False

    sequence: int = Field(
        0,
        ge=0,
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: object) -> str | None:
        return validate_external_url(
            value,
            field_name="Company Challenge resource URL",
        )


class TeamChallengeResourceUpdate(BaseModel):
    title: Optional[str] = Field(
        None,
        max_length=300,
    )

    url: Optional[str] = Field(
        None,
        max_length=1000,
    )

    is_downloadable: Optional[bool] = None

    sequence: Optional[int] = Field(
        None,
        ge=0,
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: object) -> str | None:
        return validate_external_url(
            value,
            field_name="Company Challenge resource URL",
        )


class TeamChallengeResourceResponse(BaseModel):
    id: UUID
    team_id: UUID

    title: str
    resource_type: str

    url: Optional[str]

    is_downloadable: bool
    sequence: int

    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True,
    }

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class ResourceCreate(BaseModel):
    phase_id: UUID
    session_id: Optional[UUID] = None

    title: str = Field(..., max_length=300)
    subtitle: Optional[str] = Field(None, max_length=300)

    resource_type: str = "link"

    url: Optional[str] = Field(
        None,
        max_length=1000,
    )

    is_downloadable: bool = False
    is_active: bool = True

    sequence: int = Field(
        0,
        ge=0,
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: object) -> str | None:
        return validate_external_url(value, field_name="Resource URL")


class ResourceUpdate(BaseModel):
    session_id: Optional[UUID] = None

    title: Optional[str] = Field(
        None,
        max_length=300,
    )

    subtitle: Optional[str] = Field(
        None,
        max_length=300,
    )

    resource_type: Optional[str] = None

    url: Optional[str] = Field(
        None,
        max_length=1000,
    )

    is_downloadable: Optional[bool] = None
    is_active: Optional[bool] = None

    sequence: Optional[int] = Field(
        None,
        ge=0,
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: object) -> str | None:
        return validate_external_url(value, field_name="Resource URL")


class ResourceResponse(BaseModel):
    id: UUID

    phase_id: UUID
    session_id: Optional[UUID] = None

    title: str
    subtitle: Optional[str]

    resource_type: str
    url: Optional[str]

    is_downloadable: bool
    is_active: bool

    sequence: int

    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True
    }


# ---------------------------------------------------------------------------
# Fellows (admin update / detail)
# ---------------------------------------------------------------------------


class FellowUpdate(BaseModel):
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    account_status: Optional[str] = None
    role: Optional[str] = None
    two_factor_enabled: Optional[bool] = None


class FellowDetailResponse(BaseModel):
    id: UUID
    first_name: Optional[str]
    last_name: Optional[str]
    email: str
    role: str
    account_status: str
    two_factor_enabled: bool
    created_at: Optional[datetime]
    last_login_at: Optional[datetime]

    model_config = {"from_attributes": True}
