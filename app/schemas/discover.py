from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict

from app.schemas.fellow_context import CohortSummary, PhaseSummary


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_number: int

    session_type: str | None = None
    title: str
    description: str | None = None

    start_at: datetime | None = None
    end_at: datetime | None = None
    unlock_at: datetime | None = None

    submission_enabled: bool = False

    meeting_url: str | None = None
    recording_url: str | None = None
    transcript_url: str | None = None

    status: str
    sequence: int

    is_unlocked: bool = False
    has_recording: bool = False
    has_transcript: bool = False


class DiscoverProgress(BaseModel):
    current_week: int
    total_weeks: int
    percentage: int
    completed_sessions: int
    total_sessions: int


class DiscoverOverviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    phase: PhaseSummary
    cohort: CohortSummary
    progress: DiscoverProgress
    next_session: SessionSummary | None = None


class DiscoverWeekResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    week_number: int
    title: str
    strategic_question: str | None = None
    description: str | None = None
    sequence: int
    status: str  # active | upcoming | locked | completed
    status_badge: str
    sessions: list[SessionSummary]


class SessionDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cohort_id: UUID
    phase_id: UUID
    week_id: UUID | None = None

    session_number: int
    session_type: str

    title: str
    description: str | None = None

    start_at: datetime | None = None
    end_at: datetime | None = None
    unlock_at: datetime | None = None

    submission_enabled: bool = False

    meeting_url: str | None = None
    recording_url: str | None = None
    transcript_url: str | None = None

    status: str
    sequence: int

    is_unlocked: bool = False
    has_recording: bool = False
    has_transcript: bool = False

    week_title: str | None = None
    week_number: int | None = None