from uuid import UUID
from pydantic import BaseModel, ConfigDict


class FellowUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str
    email: str
    role: str


class ProgramSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str


class CohortSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    status: str


class PhaseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    development_role: str
    sequence: int


class FellowContextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fellow: FellowUserSummary
    program: ProgramSummary
    cohort: CohortSummary
    current_phase: PhaseSummary
