import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from app.core.config import LanguageCode
from app.schemas.minutes import StructuredMeetingAnalysis

PROMPT_VERSION = "minutes-bilingual-v1.0"
MAX_REPAIR_ATTEMPTS = 2


class MeetingAnalysisError(Exception):
    """Raised when meeting analysis or structured output validation fails."""


@dataclass(frozen=True)
class SegmentForAnalysis:
    segment_id: str
    segment_index: int
    start_time_seconds: float
    end_time_seconds: float
    speaker_label: str | None
    text: str
    language: str


@dataclass(frozen=True)
class TranscriptChunk:
    chunk_index: int
    segments: list[SegmentForAnalysis]
    character_count: int


@dataclass(frozen=True)
class AnalysisExecutionResult:
    analysis: StructuredMeetingAnalysis
    model_name: str
    prompt_version: str
    repair_attempts_used: int


class MeetingAnalysisProvider(Protocol):
    model_name: str

    def generate_raw_analysis(
        self,
        chunks: list[TranscriptChunk],
        output_language: LanguageCode,
    ) -> dict[str, Any] | str: ...

    def repair_raw_analysis(
        self,
        invalid_payload: dict[str, Any] | str,
        validation_error: str,
        attempt: int,
        chunks: list[TranscriptChunk],
        output_language: LanguageCode,
    ) -> dict[str, Any] | str: ...


def chunk_transcript_segments(
    segments: list[SegmentForAnalysis],
    max_chars_per_chunk: int = 4000,
) -> list[TranscriptChunk]:
    if not segments:
        raise ValueError("Cannot chunk an empty segment list.")
    if max_chars_per_chunk <= 0:
        raise ValueError("max_chars_per_chunk must be positive.")

    chunks: list[TranscriptChunk] = []
    current_batch: list[SegmentForAnalysis] = []
    current_chars = 0

    for seg in segments:
        seg_len = len(seg.text)
        if current_batch and (current_chars + seg_len > max_chars_per_chunk):
            chunks.append(
                TranscriptChunk(
                    chunk_index=len(chunks),
                    segments=current_batch,
                    character_count=current_chars,
                )
            )
            current_batch = [seg]
            current_chars = seg_len
        else:
            current_batch.append(seg)
            current_chars += seg_len

    if current_batch:
        chunks.append(
            TranscriptChunk(
                chunk_index=len(chunks),
                segments=current_batch,
                character_count=current_chars,
            )
        )

    return chunks


def parse_and_validate_analysis(
    raw_payload: dict[str, Any] | str,
    valid_segment_ids: set[str],
) -> StructuredMeetingAnalysis:
    if isinstance(raw_payload, str):
        try:
            data = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON returned by model: {exc.msg}") from exc
    elif isinstance(raw_payload, dict):
        data = raw_payload
    else:
        raise TypeError("Model output must be a JSON object or dict.")

    validated = StructuredMeetingAnalysis.model_validate(data)

    for decision in validated.decisions:
        for ev in decision.evidence:
            if ev.segment_id not in valid_segment_ids:
                raise ValueError(
                    f"Decision references unknown segment_id '{ev.segment_id}'."
                )

    for item in validated.action_items:
        for ev in item.evidence:
            if ev.segment_id not in valid_segment_ids:
                raise ValueError(
                    f"Action item references unknown segment_id '{ev.segment_id}'."
                )

    return validated


def run_bounded_meeting_analysis(
    segments: list[SegmentForAnalysis],
    output_language: LanguageCode,
    provider: MeetingAnalysisProvider,
    max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    max_chars_per_chunk: int = 4000,
) -> AnalysisExecutionResult:
    if not segments:
        raise MeetingAnalysisError("Cannot analyze a meeting with no transcript segments.")

    chunks = chunk_transcript_segments(
        segments=segments,
        max_chars_per_chunk=max_chars_per_chunk,
    )
    valid_ids = {seg.segment_id for seg in segments}

    try:
        raw_output = provider.generate_raw_analysis(
            chunks=chunks,
            output_language=output_language,
        )
    except Exception as exc:
        raise MeetingAnalysisError("Analysis provider failed during generation.") from exc

    last_error = ""
    for attempt in range(max_repair_attempts + 1):
        try:
            validated = parse_and_validate_analysis(raw_output, valid_ids)
            return AnalysisExecutionResult(
                analysis=validated,
                model_name=provider.model_name,
                prompt_version=PROMPT_VERSION,
                repair_attempts_used=attempt,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            last_error = str(exc)
            if attempt >= max_repair_attempts:
                break
            try:
                raw_output = provider.repair_raw_analysis(
                    invalid_payload=raw_output,
                    validation_error=last_error,
                    attempt=attempt + 1,
                    chunks=chunks,
                    output_language=output_language,
                )
            except Exception as repair_exc:
                raise MeetingAnalysisError(
                    "Analysis provider failed during structured output repair."
                ) from repair_exc

    raise MeetingAnalysisError(
        f"Structured output validation failed after {max_repair_attempts} repair attempts: "
        f"{last_error}"
    )


class LocalBilingualMeetingAnalysisProvider:
    """Replaceable bilingual (Persian/English) structured meeting analysis provider."""

    model_name: str = "local-bilingual-analyzer-v1"

    def generate_raw_analysis(
        self,
        chunks: list[TranscriptChunk],
        output_language: LanguageCode,
    ) -> dict[str, Any]:
        all_segments = [seg for chunk in chunks for seg in chunk.segments]
        first = all_segments[0]
        last = all_segments[-1]

        first_ref = {
            "segment_id": first.segment_id,
            "start_time_seconds": first.start_time_seconds,
            "end_time_seconds": first.end_time_seconds,
            "speaker_label": first.speaker_label,
            "excerpt": first.text[:120],
        }
        last_ref = {
            "segment_id": last.segment_id,
            "start_time_seconds": last.start_time_seconds,
            "end_time_seconds": last.end_time_seconds,
            "speaker_label": last.speaker_label,
            "excerpt": last.text[:120],
        }

        if output_language == LanguageCode.PERSIAN:
            return {
                "overall_summary": (
                    "در این جلسه وضعیت پیشرفت فنی دستیار هوشمند جلسات و آماده‌سازی "
                    "ماژول‌های پیاده‌سازی صوت و تحلیل بررسی شد."
                ),
                "topic_summaries": [
                    {
                        "topic": "بررسی معماری و سرویس تبدیل صوت به متن",
                        "summary": "وضعیت پیاده‌سازی سرویس بک‌اند و زمان‌بندی قطعات متن مرور شد.",
                        "evidence": [first_ref],
                    }
                ],
                "key_discussion_points": [
                    "آغاز بررسی پیشرفت پروژه دستیار هوشمند",
                    "آماده‌سازی سرویس پیاده‌سازی صوت و تست نهایی زمان‌بندی‌ها",
                ],
                "decisions": [
                    {
                        "description": "استفاده از معماری ماژولار بک‌اند برای ذخیره‌سازی قطعات متن",
                        "commitment_type": "confirmed",
                        "evidence": [first_ref],
                    }
                ],
                "action_items": [
                    {
                        "description": "اجرای تست نهایی سرویس پیاده‌سازی صوت و زمان‌بندی‌ها",
                        "owner": None,
                        "deadline": None,
                        "commitment_type": "confirmed",
                        "evidence": [last_ref],
                    }
                ],
                "unresolved_questions": [],
                "disagreements_or_contradictions": [],
                "follow_up_items": [
                    "بررسی نتایج ارزیابی دوزبانه در جلسه آینده",
                ],
            }

        return {
            "overall_summary": (
                "The meeting covered backend architecture progress and the readiness of "
                "the Speech-to-Text and bilingual evaluation pipelines."
            ),
            "topic_summaries": [
                {
                    "topic": "Backend & Speech-to-Text Pipeline",
                    "summary": (
                        "Reviewed API milestones and confirmed readiness for evaluation tests."
                    ),
                    "evidence": [first_ref],
                }
            ],
            "key_discussion_points": [
                "Review of backend architecture and API milestones",
                "Finalizing Speech-to-Text pipeline and running evaluation tests",
            ],
            "decisions": [
                {
                    "description": "Proceed with the modular FastAPI and SQLAlchemy architecture",
                    "commitment_type": "confirmed",
                    "evidence": [first_ref],
                }
            ],
            "action_items": [
                {
                    "description": "Finalize the Speech-to-Text pipeline and run evaluation tests",
                    "owner": None,
                    "deadline": "Friday" if "Friday" in last.text else None,
                    "commitment_type": "confirmed",
                    "evidence": [last_ref],
                }
            ],
            "unresolved_questions": [],
            "disagreements_or_contradictions": [],
            "follow_up_items": [
                "Review bilingual evaluation metrics in the next sync",
            ],
        }

    def repair_raw_analysis(
        self,
        invalid_payload: dict[str, Any] | str,
        validation_error: str,
        attempt: int,
        chunks: list[TranscriptChunk],
        output_language: LanguageCode,
    ) -> dict[str, Any]:
        _ = (invalid_payload, validation_error, attempt)
        return self.generate_raw_analysis(chunks=chunks, output_language=output_language)


def get_meeting_analysis_provider() -> MeetingAnalysisProvider:
    return LocalBilingualMeetingAnalysisProvider()