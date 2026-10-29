from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.diarization import (
    DiarizationProviderError,
    DiarizationResult,
    DiarizedTurn,
    get_diarization_provider,
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
        json={"title": "جلسه تفکیک گویندگان", "primary_language": language},
    ).json()
    meeting_id = meeting["id"]

    client.post(
        f"/api/v1/meetings/{meeting_id}/media",
        files={"file": ("meeting.wav", make_valid_wav_bytes(), "audio/wav")},
    )
    client.post(f"/api/v1/meetings/{meeting_id}/transcribe")
    return meeting_id


def test_diarize_meeting_assigns_generic_labels_without_fabricating_real_names(
    client: TestClient,
) -> None:
    meeting_id = create_transcribed_meeting(client, language="fa")

    res = client.post(f"/api/v1/meetings/{meeting_id}/diarize")
    assert res.status_code == 200
    data = res.json()

    assert data["meeting_id"] == meeting_id
    assert data["total_speakers"] == 2
    assert data["low_confidence_segments_count"] == 0
    assert data["overlapping_segments_count"] == 0

    for speaker in data["speakers"]:
        assert speaker["speaker_label"] in {"SPEAKER_00", "SPEAKER_01"}
        assert speaker["display_name"] is None
        assert speaker["is_user_verified"] is False
        assert speaker["segment_count"] == 1

    speakers_list = client.get(
        f"/api/v1/meetings/{meeting_id}/speakers").json()
    assert len(speakers_list) == 2


def test_user_can_correct_speaker_display_name_and_merge_labels(
    client: TestClient,
) -> None:
    meeting_id = create_transcribed_meeting(client, language="mixed")
    client.post(f"/api/v1/meetings/{meeting_id}/diarize")

    patch_res = client.patch(
        f"/api/v1/meetings/{meeting_id}/speakers/SPEAKER_00",
        json={"display_name": "مهندس سارا"},
    )
    assert patch_res.status_code == 200
    updated_speaker = patch_res.json()
    assert updated_speaker["speaker_label"] == "SPEAKER_00"
    assert updated_speaker["display_name"] == "مهندس سارا"
    assert updated_speaker["is_user_verified"] is True

    merge_res = client.patch(
        f"/api/v1/meetings/{meeting_id}/speakers/SPEAKER_01",
        json={"new_speaker_label": "SPEAKER_00", "display_name": "مهندس سارا"},
    )
    assert merge_res.status_code == 200
    merged_speaker = merge_res.json()
    assert merged_speaker["speaker_label"] == "SPEAKER_00"
    assert merged_speaker["segment_count"] == 2

    transcript = client.get(f"/api/v1/meetings/{meeting_id}/transcript").json()
    assert all(seg["speaker_label"] ==
               "SPEAKER_00" for seg in transcript["segments"])


def test_diarization_handles_overlapping_speech_and_low_confidence_results(
    client: TestClient,
) -> None:
    class OverlapAndLowConfidenceDiarizer:
        def diarize_file(self, file_path: Path) -> DiarizationResult:
            _ = file_path
            return DiarizationResult(
                provider_name="test-overlap-diarizer",
                turns=[
                    DiarizedTurn(
                        start_time_seconds=0.0,
                        end_time_seconds=1.0,
                        speaker_label="SPEAKER_00",
                        confidence=0.88,
                        is_overlapping=True,
                    ),
                    DiarizedTurn(
                        start_time_seconds=0.2,
                        end_time_seconds=0.9,
                        speaker_label="SPEAKER_01",
                        confidence=0.75,
                        is_overlapping=True,
                    ),
                    DiarizedTurn(
                        start_time_seconds=1.0,
                        end_time_seconds=2.5,
                        speaker_label="SPEAKER_02",
                        confidence=0.35,
                        is_overlapping=False,
                    ),
                ],
            )

    def override_diarizer() -> OverlapAndLowConfidenceDiarizer:
        return OverlapAndLowConfidenceDiarizer()

    client.app.dependency_overrides[get_diarization_provider] = (  # type: ignore[union-attr]
        override_diarizer
    )

    try:
        meeting_id = create_transcribed_meeting(client, language="en")
        res = client.post(
            f"/api/v1/meetings/{meeting_id}/diarize",
            json={"min_confidence_threshold": 0.6},
        )
        assert res.status_code == 200
        payload = res.json()

        assert payload["overlapping_segments_count"] == 1
        assert payload["low_confidence_segments_count"] == 1
        labels = [seg["speaker_label"] for seg in payload["segments"]]
        assert labels[0] == "SPEAKER_00"
        assert labels[1] == "SPEAKER_UNKNOWN"
    finally:
        client.app.dependency_overrides.pop(
            get_diarization_provider, None)  # type: ignore[union-attr]


def test_diarize_before_transcription_returns_400(client: TestClient) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "Untranscribed Meeting", "primary_language": "fa"},
    ).json()
    res = client.post(f"/api/v1/meetings/{meeting['id']}/diarize")
    assert res.status_code == 400
    assert "before generating transcript segments" in res.json()["detail"]


def test_diarization_provider_failure_returns_502(client: TestClient) -> None:
    class FailingDiarizer:
        def diarize_file(self, file_path: Path) -> DiarizationResult:
            _ = file_path
            raise DiarizationProviderError("Diarization model unavailable")

    def override_failing_diarizer() -> FailingDiarizer:
        return FailingDiarizer()

    client.app.dependency_overrides[get_diarization_provider] = (  # type: ignore[union-attr]
        override_failing_diarizer
    )

    try:
        meeting_id = create_transcribed_meeting(client, language="en")
        res = client.post(f"/api/v1/meetings/{meeting_id}/diarize")
        assert res.status_code == 502
        assert "Diarization model unavailable" in res.json()["detail"]
    finally:
        client.app.dependency_overrides.pop(
            get_diarization_provider, None)  # type: ignore[union-attr]
