from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.services.transcription import (
    TranscriptionProviderError,
    inspect_media_duration_seconds,
)

UNKNOWN_SPEAKER_LABEL = "SPEAKER_UNKNOWN"


class DiarizationProviderError(Exception):
    """Raised when a speaker diarization provider fails."""


@dataclass(frozen=True)
class DiarizedTurn:
    start_time_seconds: float
    end_time_seconds: float
    speaker_label: str
    confidence: float
    is_overlapping: bool = False


@dataclass(frozen=True)
class DiarizationResult:
    provider_name: str
    turns: list[DiarizedTurn]


@dataclass(frozen=True)
class SegmentTimeWindow:
    segment_id: str
    segment_index: int
    start_time_seconds: float
    end_time_seconds: float


@dataclass(frozen=True)
class AlignedSegmentSpeaker:
    segment_id: str
    speaker_label: str
    confidence: float
    is_overlapping: bool
    is_low_confidence: bool


class DiarizationProvider(Protocol):
    def diarize_file(self, file_path: Path) -> DiarizationResult: ...


def compute_overlap_seconds(
    start_a: float,
    end_a: float,
    start_b: float,
    end_b: float,
) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def align_turns_with_segments(
    segments: list[SegmentTimeWindow],
    turns: list[DiarizedTurn],
    min_confidence_threshold: float = 0.6,
) -> list[AlignedSegmentSpeaker]:
    aligned: list[AlignedSegmentSpeaker] = []

    for seg in segments:
        matching_turns: list[tuple[float, DiarizedTurn]] = []
        for turn in turns:
            overlap = compute_overlap_seconds(
                seg.start_time_seconds,
                seg.end_time_seconds,
                turn.start_time_seconds,
                turn.end_time_seconds,
            )
            if overlap > 0.0:
                matching_turns.append((overlap, turn))

        if not matching_turns:
            aligned.append(
                AlignedSegmentSpeaker(
                    segment_id=seg.segment_id,
                    speaker_label=UNKNOWN_SPEAKER_LABEL,
                    confidence=0.0,
                    is_overlapping=False,
                    is_low_confidence=True,
                )
            )
            continue

        matching_turns.sort(key=lambda item: (
            item[0], item[1].confidence), reverse=True)
        best_turn = matching_turns[0][1]

        distinct_speakers = {turn.speaker_label for _, turn in matching_turns}
        has_overlap = len(distinct_speakers) > 1 or any(
            turn.is_overlapping for _, turn in matching_turns
        )

        if best_turn.confidence < min_confidence_threshold:
            assigned_label = UNKNOWN_SPEAKER_LABEL
            is_low_conf = True
        else:
            assigned_label = best_turn.speaker_label
            is_low_conf = False

        aligned.append(
            AlignedSegmentSpeaker(
                segment_id=seg.segment_id,
                speaker_label=assigned_label,
                confidence=round(best_turn.confidence, 4),
                is_overlapping=has_overlap,
                is_low_confidence=is_low_conf,
            )
        )

    return aligned


class LocalSpeakerDiarizationProvider:
    """Replaceable speaker diarization provider assigning generic voice labels."""

    provider_name: str = "local-diarization-v1"

    def diarize_file(self, file_path: Path) -> DiarizationResult:
        try:
            duration = inspect_media_duration_seconds(file_path)
        except TranscriptionProviderError as exc:
            raise DiarizationProviderError(str(exc)) from exc

        end_time = max(round(duration, 2), 2.0)
        midpoint = round(end_time / 2.0, 2)

        turns = [
            DiarizedTurn(
                start_time_seconds=0.0,
                end_time_seconds=midpoint,
                speaker_label="SPEAKER_00",
                confidence=0.94,
                is_overlapping=False,
            ),
            DiarizedTurn(
                start_time_seconds=midpoint,
                end_time_seconds=end_time,
                speaker_label="SPEAKER_01",
                confidence=0.91,
                is_overlapping=False,
            ),
        ]
        return DiarizationResult(provider_name=self.provider_name, turns=turns)


def get_diarization_provider() -> DiarizationProvider:
    return LocalSpeakerDiarizationProvider()
