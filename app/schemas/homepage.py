from uuid import UUID
from pydantic import BaseModel, ConfigDict


class FellowHomepageUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    display_name: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    role: str | None = None


class PhaseRoadmapItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    principle: str
    duration: str
    access_status: str
    can_enter: bool
    lock_reason: str | None = None


class FellowHomepageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fellow: FellowHomepageUser
    phases: list[PhaseRoadmapItem]


class PhaseAccessVerificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    phase: str
    status: str
    principle: str
    message: str
