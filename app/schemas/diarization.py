from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.transcript import TranscriptSegmentResponse


class DiarizeMeetingRequest(BaseModel):
    min_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class SpeakerUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    new_speaker_label: str | None = Field(default=None, max_length=64)

    @field_validator("display_name", "new_speaker_label")
    @classmethod
    def validate_non_blank(cls, value: str | None) -> str | None:
        if value is not None:
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("Field cannot be empty or whitespace only.")
            return cleaned
        return value

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "SpeakerUpdateRequest":
        if self.display_name is None and self.new_speaker_label is None:
            raise ValueError(
                "At least one of 'display_name' or 'new_speaker_label' must be provided."
            )
        return self


class SpeakerResponse(BaseModel):
    id: str
    meeting_id: str
    speaker_label: str
    display_name: str | None
    segment_count: int
    total_duration_seconds: float
    average_confidence: float | None
    overlap_count: int
    is_user_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeetingDiarizationResponse(BaseModel):
    meeting_id: str
    total_speakers: int
    low_confidence_segments_count: int
    overlapping_segments_count: int
    speakers: list[SpeakerResponse]
    segments: list[TranscriptSegmentResponse]
