from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import LanguageCode


class TranscribeMeetingRequest(BaseModel):
    force_retranscribe: bool = Field(default=False)
    language_override: LanguageCode | None = Field(default=None)


class TranscriptSegmentCreate(BaseModel):
    segment_index: int = Field(..., ge=0)
    start_time_seconds: float = Field(..., ge=0.0)
    end_time_seconds: float = Field(..., ge=0.0)
    text: str = Field(..., min_length=1)
    language: LanguageCode = Field(default=LanguageCode.MIXED)
    speaker_label: str | None = Field(default=None, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TranscriptSegmentCreate":
        if self.end_time_seconds < self.start_time_seconds:
            raise ValueError(
                "end_time_seconds must be greater than or equal to start_time_seconds.")
        return self


class TranscriptSegmentResponse(BaseModel):
    id: str
    meeting_id: str
    media_asset_id: str | None
    segment_index: int
    start_time_seconds: float
    end_time_seconds: float
    text: str
    language: LanguageCode
    speaker_label: str | None
    confidence: float | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeetingTranscriptResponse(BaseModel):
    meeting_id: str
    primary_language: LanguageCode
    total_segments: int
    segments: list[TranscriptSegmentResponse]
    full_text: str
