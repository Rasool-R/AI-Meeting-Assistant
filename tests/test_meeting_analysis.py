from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import LanguageCode, Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.meeting_analysis import (
    SegmentForAnalysis,
    TranscriptChunk,
    chunk_transcript_segments,
    get_meeting_analysis_provider,
)


def make_valid_wav_bytes(payload_size: int = 3200) -> bytes:
    total_data = payload_size
    riff_size = 36 + total_data
    return (
        b"RIFF"
        + riff_size.to_bytes(4, "little")
        + b"WAVE"
        + b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + total_data.to_bytes(4, "little")
        + (b"\x00" * total_data)
    )


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    testing_session_local = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=test_engine)

    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    def override_get_settings() -> Settings:
        return Settings(
            media_storage_dir=str(tmp_path / "media"),
            max_upload_size_bytes=65536,
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_get_settings

    with TestClient(app) as test_client:
        yield test_client

    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def create_transcribed_meeting(client: TestClient, language: str = "fa") -> str:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "جلسه تحلیل هوشمند", "primary_language": language},
    ).json()
    meeting_id = meeting["id"]

    client.post(
        f"/api/v1/meetings/{meeting_id}/media",
        files={"file": ("meeting.wav", make_valid_wav_bytes(), "audio/wav")},
    )
    client.post(f"/api/v1/meetings/{meeting_id}/transcribe")
    return meeting_id


def test_analyze_persian_meeting_generates_persian_minutes_with_evidence_and_null_owner(
    client: TestClient,
) -> None:
    meeting_id = create_transcribed_meeting(client, language="fa")

    res = client.post(f"/api/v1/meetings/{meeting_id}/analyze")
    assert res.status_code == 200
    minutes = res.json()

    assert minutes["meeting_id"] == meeting_id
    assert minutes["output_language"] == "fa"
    assert "دستیار هوشمند" in minutes["overall_summary"]
    assert len(minutes["decisions"]) == 1
    assert len(minutes["action_items"]) == 1

    action = minutes["action_items"][0]
    assert action["owner"] is None
    assert action["deadline"] is None
    assert action["commitment_type"] == "confirmed"
    assert len(action["evidence"]) == 1
    assert action["evidence"][0]["start_time_seconds"] >= 0.0

    get_res = client.get(f"/api/v1/meetings/{meeting_id}/minutes")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == minutes["id"]


def test_analyze_allows_user_selected_english_output_for_persian_meeting(
    client: TestClient,
) -> None:
    meeting_id = create_transcribed_meeting(client, language="fa")

    res = client.post(
        f"/api/v1/meetings/{meeting_id}/analyze",
        json={"output_language": "en", "force_reanalyze": True},
    )
    assert res.status_code == 200
    minutes = res.json()
    assert minutes["output_language"] == "en"
    assert "Speech-to-Text" in minutes["overall_summary"]


def test_bounded_repair_recovers_from_invalid_first_model_response(
    client: TestClient,
) -> None:
    class FlakyThenValidAnalyzer:
        model_name = "flaky-repair-llm-v1"

        def generate_raw_analysis(
            self,
            chunks: list[TranscriptChunk],
            output_language: LanguageCode,
        ) -> str:
            _ = (chunks, output_language)
            return "{malformed json output from first LLM pass"

        def repair_raw_analysis(
            self,
            invalid_payload: dict[str, Any] | str,
            validation_error: str,
            attempt: int,
            chunks: list[TranscriptChunk],
            output_language: LanguageCode,
        ) -> dict[str, Any]:
            _ = (invalid_payload, validation_error, attempt, output_language)
            seg = chunks[0].segments[0]
            ref = {
                "segment_id": seg.segment_id,
                "start_time_seconds": seg.start_time_seconds,
                "end_time_seconds": seg.end_time_seconds,
                "speaker_label": seg.speaker_label,
                "excerpt": seg.text,
            }
            return {
                "overall_summary": "Repaired valid summary.",
                "topic_summaries": [],
                "key_discussion_points": ["Point 1"],
                "decisions": [
                    {
                        "description": "Confirmed architecture",
                        "commitment_type": "confirmed",
                        "evidence": [ref],
                    }
                ],
                "action_items": [
                    {
                        "description": "Follow up on tests",
                        "owner": "نامشخص",
                        "deadline": "TBD",
                        "commitment_type": "suggestion",
                        "evidence": [ref],
                    }
                ],
                "unresolved_questions": [],
                "disagreements_or_contradictions": [],
                "follow_up_items": [],
            }

    def override_flaky_analyzer() -> FlakyThenValidAnalyzer:
        return FlakyThenValidAnalyzer()

    client.app.dependency_overrides[get_meeting_analysis_provider] = (  # type: ignore[union-attr]
        override_flaky_analyzer
    )

    try:
        meeting_id = create_transcribed_meeting(client, language="en")
        res = client.post(f"/api/v1/meetings/{meeting_id}/analyze")
        assert res.status_code == 200
        data = res.json()

        assert data["repair_attempts_used"] == 1
        assert data["action_items"][0]["owner"] is None
        assert data["action_items"][0]["deadline"] is None
        assert data["action_items"][0]["commitment_type"] == "suggestion"
    finally:
        client.app.dependency_overrides.pop(get_meeting_analysis_provider, None)  # type: ignore[union-attr]


def test_bounded_repair_fails_with_502_when_all_attempts_exhausted(
    client: TestClient,
) -> None:
    class AlwaysInvalidAnalyzer:
        model_name = "broken-llm-v1"

        def generate_raw_analysis(
            self,
            chunks: list[TranscriptChunk],
            output_language: LanguageCode,
        ) -> dict[str, Any]:
            _ = (chunks, output_language)
            return {"overall_summary": ""}

        def repair_raw_analysis(
            self,
            invalid_payload: dict[str, Any] | str,
            validation_error: str,
            attempt: int,
            chunks: list[TranscriptChunk],
            output_language: LanguageCode,
        ) -> dict[str, Any]:
            _ = (invalid_payload, validation_error, attempt, chunks, output_language)
            return {"overall_summary": ""}

    def override_broken_analyzer() -> AlwaysInvalidAnalyzer:
        return AlwaysInvalidAnalyzer()

    client.app.dependency_overrides[get_meeting_analysis_provider] = (  # type: ignore[union-attr]
        override_broken_analyzer
    )

    try:
        meeting_id = create_transcribed_meeting(client, language="en")
        res = client.post(f"/api/v1/meetings/{meeting_id}/analyze")
        assert res.status_code == 502
        assert "after 2 repair attempts" in res.json()["detail"]
    finally:
        client.app.dependency_overrides.pop(get_meeting_analysis_provider, None)  # type: ignore[union-attr]


def test_user_can_edit_and_approve_meeting_minutes(client: TestClient) -> None:
    meeting_id = create_transcribed_meeting(client, language="fa")
    client.post(f"/api/v1/meetings/{meeting_id}/analyze")

    patch_res = client.patch(
        f"/api/v1/meetings/{meeting_id}/minutes",
        json={
            "overall_summary": "خلاصه ویرایش‌شده و تاییدشده توسط مدیر محصول.",
            "is_approved": True,
        },
    )
    assert patch_res.status_code == 200
    updated = patch_res.json()
    assert updated["overall_summary"] == "خلاصه ویرایش‌شده و تاییدشده توسط مدیر محصول."
    assert updated["is_user_edited"] is True
    assert updated["is_approved"] is True


def test_chunk_transcript_segments_splits_long_transcripts() -> None:
    segments = [
        SegmentForAnalysis(
            segment_id=f"seg-{i}",
            segment_index=i,
            start_time_seconds=float(i * 10),
            end_time_seconds=float((i + 1) * 10),
            speaker_label="SPEAKER_00",
            text="A" * 60,
            language="en",
        )
        for i in range(5)
    ]
    chunks = chunk_transcript_segments(segments, max_chars_per_chunk=150)
    assert len(chunks) == 3
    assert len(chunks[0].segments) == 2
    assert len(chunks[1].segments) == 2
    assert len(chunks[2].segments) == 1