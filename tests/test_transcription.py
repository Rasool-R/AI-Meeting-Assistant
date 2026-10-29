from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import LanguageCode, Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.transcription import (
    TranscriptionProviderError,
    TranscriptionResult,
    get_transcription_provider,
    plan_audio_chunks,
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


def test_transcribe_persian_meeting_persists_segments_and_updates_status(
    client: TestClient,
) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "جلسه برنامه‌ریزی محصول", "primary_language": "fa"},
    ).json()
    meeting_id = meeting["id"]

    client.post(
        f"/api/v1/meetings/{meeting_id}/media",
        files={"file": ("persian_audio.wav",
                        make_valid_wav_bytes(), "audio/wav")},
    )

    transcribe_res = client.post(f"/api/v1/meetings/{meeting_id}/transcribe")
    assert transcribe_res.status_code == 200
    data = transcribe_res.json()
    assert data["meeting_id"] == meeting_id
    assert data["primary_language"] == "fa"
    assert data["total_segments"] == 2
    assert all(seg["language"] == "fa" for seg in data["segments"])
    assert data["segments"][0]["start_time_seconds"] == 0.0
    assert data["segments"][0]["end_time_seconds"] > 0.0
    assert "دستیار هوشمند" in data["full_text"]

    status_res = client.get(f"/api/v1/meetings/{meeting_id}/status")
    assert status_res.json()["status"] == "completed"

    get_transcript_res = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript")
    assert get_transcript_res.status_code == 200
    assert get_transcript_res.json()["total_segments"] == 2


def test_transcribe_mixed_language_meeting_preserves_technical_terms(
    client: TestClient,
) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "Architecture Sync", "primary_language": "mixed"},
    ).json()
    meeting_id = meeting["id"]

    client.post(
        f"/api/v1/meetings/{meeting_id}/media",
        files={"file": ("mixed_audio.wav",
                        make_valid_wav_bytes(), "audio/wav")},
    )

    res = client.post(f"/api/v1/meetings/{meeting_id}/transcribe")
    assert res.status_code == 200
    payload = res.json()
    assert payload["primary_language"] == "mixed"
    assert "FastAPI" in payload["full_text"]
    assert "LangGraph" in payload["full_text"]


def test_transcribe_is_idempotent_unless_force_retranscribe_is_true(
    client: TestClient,
) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "Idempotency Check", "primary_language": "en"},
    ).json()
    meeting_id = meeting["id"]

    client.post(
        f"/api/v1/meetings/{meeting_id}/media",
        files={"file": ("english_audio.wav",
                        make_valid_wav_bytes(), "audio/wav")},
    )

    first_res = client.post(f"/api/v1/meetings/{meeting_id}/transcribe").json()
    first_segment_id = first_res["segments"][0]["id"]

    second_res = client.post(
        f"/api/v1/meetings/{meeting_id}/transcribe").json()
    assert second_res["segments"][0]["id"] == first_segment_id

    forced_res = client.post(
        f"/api/v1/meetings/{meeting_id}/transcribe",
        json={"force_retranscribe": True},
    ).json()
    assert forced_res["segments"][0]["id"] != first_segment_id


def test_transcribe_without_uploaded_media_returns_400(client: TestClient) -> None:
    meeting = client.post(
        "/api/v1/meetings",
        json={"title": "Empty Meeting", "primary_language": "fa"},
    ).json()

    res = client.post(f"/api/v1/meetings/{meeting['id']}/transcribe")
    assert res.status_code == 400
    assert "before uploading a media file" in res.json()["detail"]


def test_transcription_provider_failure_marks_meeting_failed_and_returns_502(
    client: TestClient,
) -> None:
    class FailingProvider:
        def transcribe_file(
            self,
            file_path: Path,
            language_hint: LanguageCode,
        ) -> TranscriptionResult:
            raise TranscriptionProviderError("Simulated upstream STT timeout")

    def override_failing_provider() -> FailingProvider:
        return FailingProvider()

    client.app.dependency_overrides[get_transcription_provider] = (  # type: ignore[union-attr]
        override_failing_provider
    )

    try:
        meeting = client.post(
            "/api/v1/meetings",
            json={"title": "Timeout Test Meeting", "primary_language": "en"},
        ).json()
        meeting_id = meeting["id"]

        client.post(
            f"/api/v1/meetings/{meeting_id}/media",
            files={"file": ("audio.wav", make_valid_wav_bytes(), "audio/wav")},
        )

        res = client.post(f"/api/v1/meetings/{meeting_id}/transcribe")
        assert res.status_code == 502
        assert "Simulated upstream STT timeout" in res.json()["detail"]

        status_res = client.get(f"/api/v1/meetings/{meeting_id}/status")
        assert status_res.json()["status"] == "failed"
    finally:
        client.app.dependency_overrides.pop(
            get_transcription_provider, None)  # type: ignore[union-attr]


def test_plan_audio_chunks_splits_long_recordings_correctly() -> None:
    chunks = plan_audio_chunks(
        file_size_bytes=30_000_000,
        total_duration_seconds=1500.0,
        max_chunk_duration_seconds=600.0,
    )
    assert len(chunks) == 3
    assert chunks[0].start_offset_seconds == 0.0
    assert chunks[0].end_offset_seconds == 600.0
    assert chunks[1].start_offset_seconds == 600.0
    assert chunks[1].end_offset_seconds == 1200.0
    assert chunks[2].start_offset_seconds == 1200.0
    assert chunks[2].end_offset_seconds == 1500.0
