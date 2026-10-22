from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    speaker: str = Field(description="Name or identifier of the speaker")
    text: str = Field(description="Spoken text in this segment")
    start_time: float = Field(description="Start time in seconds", ge=0.0)
    end_time: float = Field(description="End time in seconds", ge=0.0)


class SessionRecordingPlaybackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    has_recording: bool
    status: str | None = Field(default=None, description="Recording status (e.g. ready, processing, not_available)")
    playback_url: str | None = Field(default=None, description="Direct signed playback URL for in-portal player")
    duration_seconds: int | None = Field(default=None, description="Total recording duration in seconds")
    expires_at: datetime | None = Field(default=None, description="Expiration time of signed playback URL")
    provider: str = Field(default="tldv", description="Recording provider")


class SessionTranscriptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    has_transcript: bool
    status: str | None = Field(default=None, description="Transcript status (e.g. ready, processing, not_available)")
    segments: list[TranscriptSegment] = Field(default_factory=list, description="Structured transcript segments")
    full_text: str | None = Field(default=None, description="Full concatenated plain text transcript")
    provider: str = Field(default="tldv", description="Transcript provider")
