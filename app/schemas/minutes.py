from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.core.config import LanguageCode

UNKNOWN_PLACEHOLDERS = {
    "",
    "unknown",
    "none",
    "null",
    "n/a",
    "na",
    "tbd",
    "unspecified",
    "نامشخص",
    "مشخص نشده",
    "ندارد",
}


class CommitmentType(StrEnum):
    CONFIRMED = "confirmed"
    SUGGESTION = "suggestion"


class EvidenceReferenceSchema(BaseModel):
    segment_id: str = Field(..., min_length=1)
    start_time_seconds: float = Field(..., ge=0.0)
    end_time_seconds: float = Field(..., ge=0.0)
    speaker_label: str | None = Field(default=None)
    excerpt: str = Field(..., min_length=1)


class TopicSummarySchema(BaseModel):
    topic: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    evidence: list[EvidenceReferenceSchema] = Field(default_factory=list)


class DecisionSchema(BaseModel):
    description: str = Field(..., min_length=1)
    commitment_type: CommitmentType = Field(default=CommitmentType.CONFIRMED)
    evidence: list[EvidenceReferenceSchema] = Field(..., min_length=1)


class ActionItemSchema(BaseModel):
    description: str = Field(..., min_length=1)
    owner: str | None = Field(default=None)
    deadline: str | None = Field(default=None)
    commitment_type: CommitmentType = Field(default=CommitmentType.CONFIRMED)
    evidence: list[EvidenceReferenceSchema] = Field(..., min_length=1)

    @field_validator("owner", "deadline", mode="before")
    @classmethod
    def normalize_unknown_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if cleaned.lower() in UNKNOWN_PLACEHOLDERS:
            return None
        return cleaned


class OpenQuestionSchema(BaseModel):
    question: str = Field(..., min_length=1)
    context: str | None = Field(default=None)
    evidence: list[EvidenceReferenceSchema] = Field(default_factory=list)


class ContradictionSchema(BaseModel):
    topic: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    evidence: list[EvidenceReferenceSchema] = Field(default_factory=list)


class StructuredMeetingAnalysis(BaseModel):
    overall_summary: str = Field(..., min_length=1)
    topic_summaries: list[TopicSummarySchema] = Field(default_factory=list)
    key_discussion_points: list[str] = Field(default_factory=list)
    decisions: list[DecisionSchema] = Field(default_factory=list)
    action_items: list[ActionItemSchema] = Field(default_factory=list)
    unresolved_questions: list[OpenQuestionSchema] = Field(default_factory=list)
    disagreements_or_contradictions: list[ContradictionSchema] = Field(default_factory=list)
    follow_up_items: list[str] = Field(default_factory=list)


class AnalyzeMeetingRequest(BaseModel):
    output_language: LanguageCode | None = Field(default=None)
    force_reanalyze: bool = Field(default=False)


class MeetingMinutesUpdateRequest(BaseModel):
    overall_summary: str | None = Field(default=None, min_length=1)
    topic_summaries: list[TopicSummarySchema] | None = Field(default=None)
    key_discussion_points: list[str] | None = Field(default=None)
    decisions: list[DecisionSchema] | None = Field(default=None)
    action_items: list[ActionItemSchema] | None = Field(default=None)
    unresolved_questions: list[OpenQuestionSchema] | None = Field(default=None)
    disagreements_or_contradictions: list[ContradictionSchema] | None = Field(default=None)
    follow_up_items: list[str] | None = Field(default=None)
    is_approved: bool | None = Field(default=None)


class MeetingMinutesResponse(BaseModel):
    id: str
    meeting_id: str
    output_language: LanguageCode
    overall_summary: str
    topic_summaries: list[TopicSummarySchema]
    key_discussion_points: list[str]
    decisions: list[DecisionSchema]
    action_items: list[ActionItemSchema]
    unresolved_questions: list[OpenQuestionSchema]
    disagreements_or_contradictions: list[ContradictionSchema]
    follow_up_items: list[str]
    model_name: str
    prompt_version: str
    repair_attempts_used: int
    is_user_edited: bool
    is_approved: bool
    created_at: datetime
    updated_at: datetime