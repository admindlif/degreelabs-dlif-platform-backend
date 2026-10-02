from uuid import UUID

from pydantic import BaseModel


class TeamMemberResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    initials: str
    team_role: str  # "lead" | "member"

    model_config = {"from_attributes": True}


class TeamResponse(BaseModel):
    id: str
    name: str
    company_challenge: str | None
    company_name: str | None
    member_count: int
    members: list[TeamMemberResponse]

    model_config = {"from_attributes": True}


class ResourceResponse(BaseModel):
    id: str
    title: str
    subtitle: str | None
    resource_type: str
    url: str | None
    is_downloadable: bool
    sequence: int

    model_config = {"from_attributes": True}

class TeamChallengeResourceResponse(BaseModel):
    id: UUID
    title: str
    resource_type: str
    url: str | None
    is_downloadable: bool
    sequence: int

    model_config = {"from_attributes": True}


class CompanyChallengeResponse(BaseModel):
    team_id: UUID
    team_name: str

    company_name: str | None
    company_overview: str | None

    company_challenge: str | None
    challenge_description: str | None

    resources: list[TeamChallengeResourceResponse]

    model_config = {"from_attributes": True}
