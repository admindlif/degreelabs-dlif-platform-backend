from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.url_validation import validate_external_url


ChecklistStatus = Literal["pending", "overdue", "completed"]


def validate_checklist_action_url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Action URL must be a valid portal path or HTTP(S) URL.")

    cleaned = value.strip()
    if not cleaned:
        return None
    if any(character.isspace() or ord(character) < 32 for character in cleaned):
        raise ValueError("Action URL must be a valid portal path or HTTP(S) URL.")

    if cleaned.startswith("/"):
        parsed = urlsplit(cleaned)
        if cleaned.startswith("//") or parsed.scheme or parsed.netloc:
            raise ValueError("Action URL must be a safe Fellow Portal path.")
        return cleaned

    return validate_external_url(cleaned, field_name="Action URL")


class ChecklistItemCreate(BaseModel):
    cohort_id: UUID | None = None
    phase_id: UUID | None = None
    week_id: UUID | None = None
    session_id: UUID | None = None
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=100)
    due_at: datetime | None = None
    action_label: str | None = Field(None, max_length=100)
    action_url: str | None = Field(None, max_length=1000)
    is_required: bool = True
    is_active: bool = True
    sequence: int = Field(0, ge=0)

    _validate_action_url = field_validator("action_url", mode="before")(
        validate_checklist_action_url
    )


class ChecklistItemUpdate(BaseModel):
    cohort_id: UUID | None = None
    phase_id: UUID | None = None
    week_id: UUID | None = None
    session_id: UUID | None = None
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=100)
    due_at: datetime | None = None
    action_label: str | None = Field(None, max_length=100)
    action_url: str | None = Field(None, max_length=1000)
    is_required: bool | None = None
    is_active: bool | None = None
    sequence: int | None = Field(None, ge=0)

    _validate_action_url = field_validator("action_url", mode="before")(
        validate_checklist_action_url
    )


class AdminChecklistItemResponse(ChecklistItemCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FellowChecklistItemResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    category: str | None
    due_at: datetime | None
    status: ChecklistStatus
    is_required: bool
    is_completed: bool
    completed_at: datetime | None
    action_label: str | None
    action_url: str | None
    sequence: int


class ChecklistSummaryResponse(BaseModel):
    total: int
    completed: int
    pending: int
    overdue: int
    percentage: int


class FellowChecklistResponse(BaseModel):
    summary: ChecklistSummaryResponse
    items: list[FellowChecklistItemResponse]


class ChecklistCompletionUpdate(BaseModel):
    is_completed: bool
