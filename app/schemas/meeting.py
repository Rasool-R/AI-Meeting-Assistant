from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import LanguageCode
from app.models.meeting import MeetingStatus


class MeetingCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    primary_language: LanguageCode = Field(default=LanguageCode.MIXED)
    output_language: LanguageCode | None = Field(default=None)

    @field_validator("title")
    @classmethod
    def validate_non_blank_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError(
                "Meeting title must not be blank or whitespace only.")
        return stripped

    @model_validator(mode="after")
    def set_default_output_language(self) -> "MeetingCreate":
        if self.output_language is None:
            self.output_language = self.primary_language
        return self


class MeetingResponse(BaseModel):
    id: str
    title: str
    description: str | None
    primary_language: LanguageCode
    output_language: LanguageCode
    status: MeetingStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    total: int
