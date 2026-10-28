"""
Pydantic schemas for Working Board and Working Board Insights.
"""

from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


def validate_google_docs_url(v: str) -> str:
    url = v.strip()
    if not url:
        raise ValueError("URL cannot be empty.")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Working Board URL must use secure HTTPS.")
    netloc = parsed.netloc.lower()
    allowed_hosts = {"docs.google.com", "drive.google.com"}
    if netloc not in allowed_hosts and not any(netloc.endswith("." + h) for h in allowed_hosts):
        raise ValueError(
            "Working Board URL must be a valid Google Docs or Google Drive link (docs.google.com or drive.google.com)."
        )
    return url


# ---------------------------------------------------------------------------
# Admin Schemas
# ---------------------------------------------------------------------------


class AdminWorkingBoardCreateRequest(BaseModel):
    session_id: UUID
    team_id: UUID
    title: str = "Working Board"
    working_board_url: str
    instructions: str | None = None
    is_published: bool = False

    @field_validator("working_board_url")
    @classmethod
    def check_url(cls, v: str) -> str:
        return validate_google_docs_url(v)

    @field_validator("title")
    @classmethod
    def check_title(cls, v: str) -> str:
        t = v.strip()
        if not t:
            return "Working Board"
        return t


class AdminWorkingBoardUpdateRequest(BaseModel):
    title: str | None = None
    working_board_url: str | None = None
    instructions: str | None = None
    is_published: bool | None = None

    @field_validator("working_board_url")
    @classmethod
    def check_url(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_google_docs_url(v)
        return v

    @field_validator("title")
    @classmethod
    def check_title(cls, v: str | None) -> str | None:
        if v is not None:
            t = v.strip()
            if not t:
                raise ValueError("Title cannot be blank.")
            return t
        return v


class AdminWorkingBoardPublicationPatchRequest(BaseModel):
    is_published: bool


class AdminWorkingBoardDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    team_id: UUID
    title: str
    working_board_url: str
    instructions: str | None = None
    is_published: bool
    created_by_user_id: UUID | None = None
    updated_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    # Enriched context
    session_title: str | None = None
    session_number: int | None = None
    team_name: str | None = None
    cohort_id: UUID | None = None
    cohort_name: str | None = None
    week_number: int | None = None


# ---------------------------------------------------------------------------
# Fellow Schemas
# ---------------------------------------------------------------------------


class FellowWorkingBoardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    team_id: UUID
    title: str
    working_board_url: str
    instructions: str | None = None
    is_published: bool
    updated_at: datetime
    cta_text: str = "Click here to open the Working Board and work collaboratively."


class FellowWorkingBoardInsightsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    working_board_id: UUID
    session_id: UUID
    team_id: UUID
    mentor_user_id: UUID
    mentor_name: str | None = None
    insights_text: str
    publication_status: str
    tagline: str = "Insights provided by the Team's Mentor."
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Mentor Schemas
# ---------------------------------------------------------------------------


class MentorWorkingBoardSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    session_title: str | None = None
    session_number: int | None = None
    team_id: UUID
    team_name: str | None = None
    cohort_id: UUID | None = None
    cohort_name: str | None = None
    title: str
    working_board_url: str
    instructions: str | None = None
    is_published: bool
    has_insights: bool = False
    insights_status: str | None = None
    updated_at: datetime


class MentorWorkingBoardInsightsUpsertRequest(BaseModel):
    insights_text: str
    publication_status: Literal["draft", "published"] = "draft"

    @field_validator("insights_text")
    @classmethod
    def check_text(cls, v: str) -> str:
        text = v.strip()
        if not text:
            raise ValueError("Insights text cannot be empty.")
        return text


class MentorWorkingBoardInsightsDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    working_board_id: UUID
    mentor_user_id: UUID
    mentor_name: str | None = None
    insights_text: str
    publication_status: str
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    revision_count: int = 1
