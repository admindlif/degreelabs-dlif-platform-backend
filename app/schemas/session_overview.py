from pydantic import BaseModel, ConfigDict, Field


class SessionOverviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    phase_code: str = Field(..., description="Canonical phase code (e.g. DISCOVER)")
    session_number: int = Field(..., description="Canonical session number (1..12)")
    purpose: str = Field(..., description="Exact handbook week purpose/objective")
    what_to_explore: str = Field(..., description="Exact handbook session focus")
    working_evidence: str = Field(..., description="Exact handbook required working evidence")
    source_document: str = Field(
        default="DLIF Fellow Handbook – DISCOVER",
        description="Source document name",
    )
    source_version: str = Field(default="1.0", description="Handbook version")
    source_page: int = Field(..., description="Page number in handbook")
