"""
Schemas for the extended Fellow Checklist (review workflow)
and the new Checklist Feedback system.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Review status literal (mirrors ChecklistReviewStatus enum)
# ---------------------------------------------------------------------------

ReviewStatus = Literal["not_checked", "pending_review", "accepted", "rejected"]


# ---------------------------------------------------------------------------
# Fellow Checklist – submission modal
# ---------------------------------------------------------------------------


class ChecklistResponseSubmit(BaseModel):
    """Fellow submits/updates their contribution + reflection text."""

    contribution: str = Field(..., min_length=1, max_length=10000)
    reflection: str = Field(..., min_length=1, max_length=10000)
    my_thoughts: str | None = Field(default=None, max_length=10000)


class FellowChecklistCompletionDetail(BaseModel):
    """Full detail of a fellow's checklist response (returned to fellow & mentor)."""

    id: UUID
    checklist_item_id: UUID
    user_id: UUID

    is_completed: bool
    completed_at: datetime | None

    contribution: str | None
    reflection: str | None
    my_thoughts: str | None = None

    review_status: ReviewStatus
    reviewed_by_mentor_id: UUID | None
    reviewed_at: datetime | None
    rejection_reason: str | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Mentor review actions
# ---------------------------------------------------------------------------


class MentorChecklistReviewRequest(BaseModel):
    """Mentor accepts or rejects a fellow's checklist response."""

    action: Literal["accept", "reject"]
    rejection_reason: str | None = Field(
        default=None,
        max_length=2000,
        description="Required when action='reject'.",
    )


# ---------------------------------------------------------------------------
# Checklist Feedback Messages
# ---------------------------------------------------------------------------


class ChecklistFeedbackMessageCreate(BaseModel):
    """Post a new message in the checklist feedback thread."""

    message: str = Field(..., min_length=1, max_length=5000)


class ChecklistFeedbackMessageResponse(BaseModel):
    id: UUID
    checklist_item_id: UUID
    fellow_user_id: UUID
    author_user_id: UUID
    author_role: str
    author_name: str | None = None
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChecklistFeedbackThreadResponse(BaseModel):
    """All messages for a (checklist_item, fellow) thread."""

    checklist_item_id: UUID
    fellow_user_id: UUID
    messages: list[ChecklistFeedbackMessageResponse]


class MentorNotificationResponse(BaseModel):
    id: UUID
    type: str
    title: str
    message: str
    is_read: bool
    created_at: datetime
    target_url: str | None = None
    meta: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class MentorNotificationsResponse(BaseModel):
    total_count: int
    unread_count: int
    notifications: list[MentorNotificationResponse]


# ---------------------------------------------------------------------------
# Mentor Team Assignment
# ---------------------------------------------------------------------------


class MentorTeamAssignmentCreate(BaseModel):
    mentor_user_id: UUID
    team_id: UUID


class MentorTeamAssignmentResponse(BaseModel):
    id: UUID
    mentor_user_id: UUID
    team_id: UUID
    cohort_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Mentor Portal – summary views
# ---------------------------------------------------------------------------


class MentorAssignedTeamSummary(BaseModel):
    team_id: UUID
    team_name: str
    cohort_id: UUID
    cohort_name: str


class MentorAssignedTeamsResponse(BaseModel):
    teams: list[MentorAssignedTeamSummary]


class MentorFellowChecklistSummary(BaseModel):
    """One row per fellow per checklist item in a team."""

    fellow_user_id: UUID
    fellow_name: str
    checklist_item_id: UUID
    checklist_item_title: str
    review_status: ReviewStatus
    submission_present: bool  # was the required document submitted?


class MentorTeamChecklistResponse(BaseModel):
    team_id: UUID
    team_name: str
    items: list[MentorFellowChecklistSummary]


# ---------------------------------------------------------------------------
# Admin – mentor account creation (reuses CreateFellowRequest pattern)
# ---------------------------------------------------------------------------


class CreateMentorRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)


class UpdateMentorRequest(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    email: str | None = Field(None, min_length=3, max_length=255)
    role: str | None = Field(None)
    account_status: str | None = Field(None)


class MentorProfileResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: str
    role: str

    model_config = {"from_attributes": True}


class MentorSessionDetailResponse(BaseModel):
    id: UUID
    cohort_id: UUID
    cohort_name: str | None = None
    phase_id: UUID
    phase_name: str | None = None
    week_id: UUID | None = None
    week_number: int | None = None
    week_title: str | None = None
    team_id: UUID
    team_name: str

    session_number: int
    session_type: str

    title: str
    description: str | None = None

    start_at: datetime | None = None
    end_at: datetime | None = None
    unlock_at: datetime | None = None

    submission_enabled: bool = False

    meeting_url: str | None = None
    meeting_provider: str | None = None
    recording_url: str | None = None
    transcript_url: str | None = None

    status: str
    sequence: int

    is_unlocked: bool = True
    has_recording: bool = False
    has_transcript: bool = False

    model_config = {"from_attributes": True}
