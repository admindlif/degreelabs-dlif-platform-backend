from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class TeamPhaseProgressionUpdate(BaseModel):
    phase_code: str = Field(..., min_length=1, max_length=50)
    status: str = Field(..., min_length=1, max_length=50)
    decision: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class TeamPhaseProgressionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    team_id: UUID
    cohort_id: UUID
    phase_code: str
    status: str
    decision: str | None = None
    decided_at: datetime | None = None
    decided_by_user_id: UUID | None = None
    notes: str | None = None
